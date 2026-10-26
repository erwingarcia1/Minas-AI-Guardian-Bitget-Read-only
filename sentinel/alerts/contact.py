from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sentinel.alerts.base import AlertMessage
from sentinel.alerts.email import EmailChannel
from sentinel.alerts.meaning import is_coverage_rule, preference_tier
from sentinel.alerts.sms import build_sms_channel
from sentinel.config import env_setting
from sentinel.net import smtp_refusal_code

logger = logging.getLogger("sentinel.alerts.contact")

# Decision 26b (the owner, 2026-10-25): the security notices whose refusal
# pages Minas. Each is a tripwire an attacker hopes never arrives - the one
# signal a member gets that somebody else changed their login or moved
# their alerts - so one the email provider REFUSED means the member was not
# warned by email, and only a person can make sure they are warned now.
# EVERY refusal pages, whatever the Telegram leg did: whoever is signed in
# to a login can connect a chat of their own, and the member is not emailed
# when one is, so a chat that took the copy may be the attacker's. The page
# says how many connected chats took it; that count never silences it.
# Rule -> what the member has NOT been told then; the page says it in those
# words, and '{label}' is the account the notice was about. A send that
# never reached the provider is not paged: it stays #58's log line, as for
# an alert.
#
# Not every notice. A refused reset link corrects itself (the member asks
# again, or calls Minas), and the emergency-contact note and the pause and
# erasure confirmations carry no warning. Two more notices call themselves
# tripwires in their docstrings and are left OUT because the owner was not
# asked about them: `telegram_disconnected` (sentinel/portal.py) and
# `app_signed_in` (sentinel/api.py, decision 21c). Adding either takes its
# line here AND an operator alerter on its sender: both build
# ContactRouter(db=db) today - `_send_telegram_disconnected_email`, and
# `send_new_device_email`, whose app Api has no alerter to pass - so a line
# here alone pages nothing. The WIRED registry in
# tests/test_security_notice_delivery.py fails until the new rule has an
# execution test that drives its real sender and sees the page.
TRIPWIRE_NOTICE_RULES: dict[str, str] = {
    "password_changed": "their portal password was changed",
    "two_factor_disabled": "two-factor was turned off on their login",
    "alert_destination_changed": "the alerts for '{label}' now go to another primary address",
}

# The tripwires about a portal LOGIN rather than one account. They are sent
# under the login's label, so their page names the accounts that login
# opens: with the address masked, that is what says whose login it is.
_LOGIN_TRIPWIRES = frozenset({"password_changed", "two_factor_disabled"})

# Anything shaped like an address, for masking text a page quotes.
_ADDRESS_SHAPED = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*")


def mask_address(address: str | None) -> str:
    """An address as an operator page may carry it: the first two characters
    of the local part, then ***@ and the domain - fewer characters when the
    local part is that short, so a mask never spells a whole one. A page
    goes to Telegram, SMS and the ops mailbox; the full address stays in the
    notice's failed delivery row, behind the dashboard's sign-in (decision
    26b)."""
    text = (address or "").strip()
    local, at, domain = text.rpartition("@")
    if not at:
        local, domain = text, ""
    shown = local[:min(2, max(len(local) - 1, 0))]
    return f"{shown}***{at}{domain}"


def _masked_text(text: str, addresses) -> str:
    """`text` with every address in it masked. The recipients first, in any
    casing and in both spellings a refusal quotes them back in - as written,
    and as the bytes repr of a server reply spells a non-ASCII one
    ('jos\\xc3\\xa9@...'): SMTPRecipientsRefused reads as a dict keyed by the
    address, and the server's own words ("550 5.1.1 <member@example.com>:
    Recipient address rejected") name it again. In each spelling, the local
    part where it stands alone too, and a plus-addressed one's base before
    the '+', when it is three characters or more: "550 5.1.1 User alice
    unknown" beside al***@example.com would spell the address, and so would
    "User alice" beside the mask of alice+minas@example.com. Then anything
    else shaped like an address.

    Every replacement is a function, never a template string: a backslash
    in an address would be read as a group reference, raise, and lose the
    page.

    Residual, stated: a local part of one or two characters standing alone
    is left as it is (masking every short word would bury the server's
    reply)."""
    for address in addresses:
        address = (address or "").strip()
        if address:
            masked = mask_address(address)
            # As written, and as a server reply's bytes repr spells it.
            for spelling in dict.fromkeys((address, str(address.encode("utf-8", "backslashreplace"))[2:-1])):
                text = re.sub(re.escape(spelling), lambda _, m=masked: m, text, flags=re.IGNORECASE)
                local = spelling.rpartition("@")[0]
                # The local part standing alone, and a plus-addressed one's base.
                for part in dict.fromkeys((local, local.split("+", 1)[0])):
                    if len(part) >= 3:
                        part_masked = mask_address(part)
                        text = re.sub(rf"(?<![\w.+-]){re.escape(part)}(?![\w.+-])",
                                      lambda _, m=part_masked: m, text, flags=re.IGNORECASE)
    return _ADDRESS_SHAPED.sub(lambda found: mask_address(found.group(0)), text)


# A login page names at most this many accounts, each cut to a length: the
# list is in the message and the evidence, and the operator's Telegram takes
# 4096 characters - a longer page is refused, on the channel that carries it
# when the provider is refusing email.
_ACCOUNTS_NAMED = 10
_ACCOUNT_NAME_CHARS = 60


def _opens_sentence(accounts: list | None) -> str:
    """What a page about a login says of the accounts it opens (decision
    26b): None is a lookup that failed, never a login that opens nothing.
    At most _ACCOUNTS_NAMED by name, then how many more."""
    if accounts is None:
        return " The accounts that login opens could not be read."
    if not accounts:
        return " That login opens no account."
    named = ", ".join(f"'{name}'" for name in accounts[:_ACCOUNTS_NAMED])
    more = len(accounts) - _ACCOUNTS_NAMED
    return f" That login opens {named}" + (f" and {more} more" if more > 0 else "") + "."


def _kind_of(detection) -> str:
    """What kind of message this is, for the delivery record."""
    if getattr(detection, "is_drill", False):
        return "drill"
    rule = getattr(detection, "rule", "") or ""
    if rule == "proof_of_life_digest":
        return "digest"
    if rule.startswith("ESCALATION") or type(detection).__name__ == "_EscalationDetection":
        return "escalation"
    if getattr(detection, "is_notice", False):
        return "notice"
    return "alert"


class ContactRouter:
    """Delivers an account's alerts to the per-account email/phone captured at
    onboarding, in addition to the global severity routing. Transport comes
    from the same SMTP_* / ClickSend / Twilio env used elsewhere; when a
    transport isn't configured, a contact is skipped with a one-time warning
    rather than an error - so per-account delivery lights up the moment
    credentials are added, with no code change.

    When constructed with `db` (and optionally `rules`), SMS delivery is also
    policy-aware and auditable:
      - every SMS outcome lands in sms_deliveries (provider message id on
        success, provider error on failure, skip/suppress reasons otherwise);
      - duplicate SMS for the same rule + account are suppressed within
        alerting.sms_cooldown_minutes (default 30);
      - the account's sms_alerts_enabled preference is respected, with
        CRITICAL overriding an opt-out when
        alerting.sms_critical_overrides_optout is true (default);
      - a CRITICAL alert with no phone on file is logged and recorded so the
        dashboard can nudge the operator to capture one."""

    def __init__(self, db=None, rules=None, operator_alerter=None) -> None:
        self._db = db
        self._rules = rules
        self._smtp = self._smtp_config()
        self._warned: set[str] = set()
        self._operator_alerter = operator_alerter
        self._email_muted: set[str] = set()
        self._telegram_targets: list[str] = []

    @staticmethod
    def _smtp_config() -> dict | None:
        # Blank and whitespace are unset (env_setting): a half-filled SMTP_*
        # must leave `_smtp` None so the not-configured warning fires once,
        # rather than build a channel that fails on every email.
        host = env_setting("SMTP_HOST")
        sender = env_setting("SMTP_SENDER")
        if host and sender:
            return {
                "host": host,
                "port": int(env_setting("SMTP_PORT", "587")),
                "username": env_setting("SMTP_USERNAME", ""),
                "password": env_setting("SMTP_PASSWORD", ""),
                "sender": sender,
            }
        return None

    def _warn_once(self, key: str, msg: str) -> None:
        """A missing provider is not a transient (#58, group A).

        The per-event failures on this path - one email bounced, one SMS
        rejected - are deliberately left as log lines: one is noise, and a
        rising rate is a metric-and-alarm shape rather than a page. This is the
        other kind. A contact channel that is *set on the account* but has no
        provider configured means every alert that account should ever receive
        through it silently does not happen, for the life of the process. It is
        permanent, it is a configuration mistake somebody can fix in a minute,
        and until now it was one log line at the moment of the first
        undelivered alert.

        Logged once per router, and paged at most once per operator
        cooldown, on the same key. The page is guarded like every page on
        this path: a security notice's router carries an alerter (decision
        26b) and reaches here with SMTP unset before its Telegram leg, and a
        failing page must not cost the member that copy."""
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning(msg)
        if self._operator_alerter is None:
            return
        try:
            self._operator_alerter.page(
                rule="contact_channel_has_no_provider",
                severity="HIGH",
                message=(
                    f"A customer contact channel is configured on at least one account but has no "
                    f"provider set, so those alerts are not being delivered: {msg}"
                ),
                evidence={"channel": key},
                dedup_scope=key,
            )
        except Exception as page_exc:  # noqa: BLE001 - a failing page must not cost the legs after it
            logger.error("could not page the missing %s provider: %s", key, page_exc)

    # --- SMS delivery log ----------------------------------------------------

    def _record_sms(
        self,
        account_id: int | None,
        alert_id: int | None,
        rule: str,
        status: str,
        *,
        phone: str | None = None,
        provider: str | None = None,
        provider_message_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        """The SMS leg's row (#103 item 2): the same general record every
        other leg writes; 'sent' is the transport's 'accepted'."""
        if self._db is None or account_id is None:
            return
        from sentinel.alerts.delivery import record_delivery

        leg = getattr(self, "_leg", None) or {}
        record_delivery(self._db, channel="sms", recipient=phone,
                        status="accepted" if status == "sent" else status,
                        audience=leg.get("audience", "customer"), kind=leg.get("kind", "alert"),
                        alert_id=alert_id, account_id=account_id, rule=rule,
                        provider=provider, provider_ref=provider_message_id, detail=detail)

    def _sms_suppressed(self, account_id: int, rule: str) -> bool:
        """Duplicate-SMS protection: has an SMS for this rule + account already
        gone out within the suppression window? Keyed wider than dispatch's
        dedup (rule, not rule + evidence id) - a burst of distinct withdrawals
        still yields one text, with the details on Telegram/email."""
        minutes = 30
        if self._rules is not None:
            minutes = self._rules.get_int("alerting", "sms_cooldown_minutes", default=30)
        with self._db.cursor() as cur:
            cur.execute(
                "SELECT created_at FROM sms_deliveries WHERE account_id = ? AND rule = ? "
                "AND status = 'sent' ORDER BY created_at DESC LIMIT 1",
                (account_id, rule),
            )
            row = cur.fetchone()
        if row is None:
            return False
        last = datetime.fromisoformat(row["created_at"])
        return datetime.now(timezone.utc) - last < timedelta(minutes=minutes)

    def _sms_pref_enabled(self, account_id: int) -> bool:
        with self._db.cursor() as cur:
            cur.execute("SELECT sms_alerts_enabled FROM accounts WHERE id = ?", (account_id,))
            row = cur.fetchone()
        return bool(row["sms_alerts_enabled"]) if row is not None else True

    def _critical_overrides_optout(self) -> bool:
        if self._rules is None:
            return True
        return bool(self._rules.get("alerting", "sms_critical_overrides_optout", default=True))

    # --- delivery --------------------------------------------------------------

    def send(self, detection, account_label: str, emails, phones, *, account_id=None, alert_id=None,
             audience: str = "customer") -> None:
        """Deliver to every configured per-account email + phone (primary and
        secondary). `emails`/`phones` are iterables; None/blank entries and
        duplicates are dropped. Telegram handles are intentionally NOT handled
        here - the Bot API can't DM an arbitrary @handle without the recipient
        first starting the bot (see docs), so those are stored for reference
        only until a customer-opt-in flow exists."""
        # Case-insensitive duplicate-drop for emails (#90): an address listed
        # as primary and secondary in different casings is one person, and
        # mailing them twice per alert teaches them to skim. First casing wins.
        seen: set[str] = set()
        emails = [e for e in (emails or [])
                  if e and not (e.lower() in seen or seen.add(e.lower()))]
        phones = list(dict.fromkeys(p for p in (phones or []) if p))
        auditable = self._db is not None and account_id is not None
        # The delivery record (#103 item 2): every leg below leaves a row -
        # accepted, failed, skipped, held - per recipient, per channel.
        self._leg = dict(audience=getattr(detection, "audience", None) or audience, kind=_kind_of(detection), alert_id=alert_id,
                         account_id=account_id, rule=getattr(detection, "rule", None))

        # A CRITICAL alert that has nowhere to text is itself worth surfacing:
        # log it and record it so the dashboard can prompt for a phone number.
        if not phones and detection.severity == "CRITICAL" and auditable:
            logger.warning(
                "CRITICAL alert for %s has no SMS destination on file - add a phone number "
                "to enable text alerts (rule=%s)",
                account_label, detection.rule,
            )
            self._record_sms(
                account_id, alert_id, detection.rule, "skipped",
                detail="no phone number on file",
            )

        if not emails and not phones:
            return
        # #24: each primary contact's own preferences - which channels for
        # this severity, and whether their quiet hours hold it for the next
        # digest. Notices and escalations bypass (they must always arrive);
        # a CRITICAL is never held; CRITICAL email is the floor. Only when
        # the router has a db to read preferences from.
        before_prefs = list(emails)
        emails, phones, held_for = self._apply_preferences(detection, emails, phones, account_id, alert_id)
        for held_email in held_for:
            logger.info("alert %s held for %s during their quiet hours - released in their next digest",
                        alert_id, held_email)
            self._record("email", held_email, "held", detail="quiet hours - released in the next digest")
        for muted in [e for e in before_prefs if e not in emails and e not in held_for]:
            self._record("email", muted, "skipped", detail="member preference: channel off for this severity")
        if getattr(detection, "receipt", False):
            emails, phones = self._route_receipt(emails, phones, account_id, alert_id, detection)
        if getattr(detection, "email_only", False):
            # Decision 26c (2026-10-25): the password-reset link goes to the
            # mailbox and nowhere else. A notice bypasses preferences, so
            # _apply_preferences made every address it goes to a Telegram
            # target - and the link reached any chat the member had
            # connected (#18). The link proves control of the mailbox; a
            # copy in Telegram let whoever held the chat take over the
            # login without it. Cleared here, before any leg is sent.
            self._telegram_targets = []
            phones = []
        if not emails and not phones and not self._telegram_targets:
            return
        alert = AlertMessage(
            account_label=account_label,
            rule=detection.rule,
            severity=detection.severity,
            confidence=detection.confidence,
            message=detection.message,
            evidence=detection.evidence,
            # Account notices (password reset, destination change) carry their
            # whole meaning in `message` and must render it verbatim; a
            # detection must not, because that text embeds addresses.
            notice=getattr(detection, "is_notice", False),
            incident_id=alert_id,
            drill=getattr(detection, "is_drill", False),
            display_label=getattr(detection, "display_label", None),
            escalation=bool(getattr(detection, "escalation", False)),
        )

        if getattr(detection, "coverage_repeat", False):
            # A coverage REPEAT: the daily "still not being watched" reminder.
            # Email only - no SMS, no Telegram copy - so a broken key costs
            # the customer one email a day, not a page a day (Part B of the
            # 1.11.0 verification: 2 emails + 9 Minas messages + a note to
            # the emergency contact, daily, for as long as it stayed broken).
            phones = []
            self._telegram_targets = []
        emails_for_email = [e for e in emails if e not in self._email_muted]
        for muted in [e for e in emails if e in self._email_muted]:
            self._record("email", muted, "skipped", detail="member preference: email off for this severity")
        # A security tripwire the provider refused, and when (decision 26b):
        # recorded by the email leg, paged after the Telegram leg.
        refused_tripwire = failed = None
        if emails_for_email:
            if self._smtp is None:
                self._warn_once(
                    "smtp",
                    "per-account email set but SMTP is not configured - set SMTP_* env to deliver "
                    "these alerts (Telegram still fires)",
                )
                for email in emails_for_email:
                    self._record("email", email, "skipped", detail="SMTP is not configured")
            else:
                try:
                    EmailChannel(
                        self._smtp["host"], self._smtp["port"], self._smtp["username"],
                        self._smtp["password"], self._smtp["sender"], emails_for_email,
                        audience="customer",
                    ).send(alert)
                    for email in emails_for_email:
                        self._record("email", email, "accepted", provider="smtp")
                except Exception as exc:  # noqa: BLE001 - delivery failure must not stall detection
                    # A connection error stays a log line (the #58 sweep: one
                    # flaky send is noise). A REFUSAL is different (2026-09-22):
                    # the provider answered "550 daily sending quota reached"
                    # to every customer alert for twelve hours and nothing
                    # said so - the alarm had consumed the capacity it needed
                    # to alarm. A refused CUSTOMER email on an alert that must
                    # reach a human now pages Minas, and the page's own email
                    # leg failing is why the operator routing has Telegram.
                    logger.warning("per-account email to %s failed: %s", emails, exc)
                    # One instant for the rows and for a tripwire page's
                    # failed_at, so the page points at exactly these rows.
                    failed = datetime.now(timezone.utc)
                    for email in emails_for_email:
                        self._record("email", email, "failed", provider="smtp", detail=str(exc), when=failed)
                    self._page_if_refused(exc, detection, account_label, alert_id, emails_for_email)
                    # A security tripwire the provider REFUSED (decision 26b)
                    # pages after the Telegram leg below, not here: the page
                    # says what that leg did, which only that leg knows.
                    if self._is_refused_tripwire(exc, detection):
                        refused_tripwire = exc

        # Guarded on its own like every leg: a Telegram outage must cost
        # neither the email above nor the SMS below. A leg that raises
        # counts as no chat having taken the copy.
        accepted = 0
        try:
            accepted = self._send_telegram(alert, account_label, self._telegram_targets)
        except Exception as exc:  # noqa: BLE001 - one channel must not cost the others
            logger.warning("per-member telegram for %s failed: %s", account_label, exc)

        # Decision 26b's page: EVERY refused tripwire pages, as the owner
        # approved. It waits for the Telegram leg only to say what that leg
        # did, never to be skipped by it: whoever is signed in to a login can
        # connect a chat of their own (POST /telegram/link), and the member
        # is not emailed when one is, so a chat that took the copy may belong
        # to the person who made the change - a page that chat could silence
        # would be theirs to switch off. Notices are never texted (every
        # sender passes no phone), so email and Telegram are every channel
        # the page has to speak for.
        if refused_tripwire is not None:
            self._page_if_tripwire_undelivered(refused_tripwire, detection, account_label, emails_for_email,
                                               failed, accepted)

        if phones:
            self._send_sms(detection, alert, account_label, phones, account_id, alert_id, auditable)

    def _page_if_refused(self, exc, detection, account_label, alert_id, recipients) -> None:
        """The provider REFUSED the message (an SMTP 4xx/5xx reply - a quota,
        a hard bounce, a rejected sender), the audience is the customer, and
        the alert is one that must reach a human now (CRITICAL, or a coverage
        rule). Then Minas is paged at CRITICAL, deduplicated on the refusal
        so a storm of refusals is one page per cooldown, with the provider's
        words in the evidence. Nothing here estimates remaining capacity:
        SMTP exposes none, and a guess would be a lie in the evidence."""
        if self._operator_alerter is None or self._leg.get("audience") != "customer":
            return
        code = smtp_refusal_code(exc)
        if code is None:
            return
        severity = getattr(detection, "severity", "")
        if severity != "CRITICAL" and not is_coverage_rule(getattr(detection, "rule", None)):
            return
        try:
            self._operator_alerter.page(
                rule="customer_email_refused", severity="CRITICAL",
                message=(f"The email provider REFUSED a {severity} alert ({detection.rule}) to the customer of "
                         f"{account_label} with SMTP {code}: {str(exc)[:160]}. Customer alert emails are not "
                         "being delivered until this clears - check the provider's quota or the sender's standing. "
                         "This page reaches you on Telegram because its own email leg fails the same way."),
                evidence={"smtp_code": code, "detail": str(exc)[:300], "rule": detection.rule,
                          "severity": severity, "alert_id": alert_id, "recipients": len(recipients)},
                dedup_scope=f"smtp-refused:{code}",
            )
        except Exception as page_exc:  # noqa: BLE001 - the page must not cost the SMS leg below
            logger.error("could not page the refused customer email: %s", page_exc)

    def _is_refused_tripwire(self, exc, detection) -> bool:
        """Decision 26b's whole condition, judged in the email leg - nothing
        the Telegram leg does after it sets the page aside: an operator
        alerter to page with, a notice whose rule is in
        TRIPWIRE_NOTICE_RULES, and a REFUSAL - an SMTP 4xx/5xx reply. A send
        that never reached the provider is not one: as for an alert, it
        stays a log line (#58). SMTP not configured is not one either - that
        leg is 'skipped', and the liveness gate already reads Email DEAD."""
        return (self._operator_alerter is not None and getattr(detection, "is_notice", False)
                and getattr(detection, "rule", None) in TRIPWIRE_NOTICE_RULES
                and smtp_refusal_code(exc) is not None)

    def _page_if_tripwire_undelivered(self, exc, detection, account_label, recipients, failed,
                                      chats_told) -> None:
        """Decision 26b (the owner, 2026-10-25): a security tripwire the email
        provider REFUSED pages Minas, at CRITICAL - customer_email_refused's
        footing, because it too must reach a human now. `send` calls this
        after the Telegram leg whenever `_is_refused_tripwire` held, with
        `chats_told`: how many connected chats accepted the copy. The count
        changes what the page says, never whether it goes - whoever is
        signed in to a login can connect a chat, and the member is not
        emailed when one is. So the page says the member was told nowhere,
        or that N chats took a copy and may not be the member's; the
        evidence carries N as telegram_accepted.

        One page per recipient, deduplicated on the notice, the address and
        the account: two members' refusals are two pages, and so are two
        accounts moved off one address; one member's repeats inside the
        cooldown are one. The page and its evidence carry the address
        MASKED, the server's words included - a page reaches Telegram, SMS
        and the ops mailbox - and point at the notice's failed delivery row,
        which has it in full, by `failed_at`: that row's created_at, to the
        microsecond. A tripwire about the login also names the accounts the
        login opens - at most _ACCOUNTS_NAMED, each cut to
        _ACCOUNT_NAME_CHARS, so the page fits one Telegram message. Never
        raises: a failing page must not cost the SMS leg after it, or the
        caller."""
        rule = getattr(detection, "rule", None)
        for recipient in recipients:
            try:
                untold = TRIPWIRE_NOTICE_RULES[rule]
                code = smtp_refusal_code(exc)
                masked = mask_address(recipient)
                label = _masked_text(str(account_label), recipients)
                words = _masked_text(str(exc), recipients)
                evidence = {"notice": rule, "recipient": masked, "smtp_code": code, "detail": words[:300],
                            "account_label": label, "failed_at": failed.isoformat(),
                            "telegram_accepted": chats_told}
                fact = untold.replace("{label}", label)
                if chats_told:
                    told = (f"The member has NOT been told by email that {fact}. {chats_told} Telegram chat(s) "
                            "connected to this login took its copy - but whoever is signed in to a login can "
                            "connect a chat, and the member is not emailed when one is, so a chat that "
                            "took it may not be the member's.")
                else:
                    told = f"The member has NOT been told that {fact}, by email or on Telegram."
                opens = ""
                if rule in _LOGIN_TRIPWIRES:
                    accounts = self._login_accounts(recipient, recipients)
                    evidence["accounts"] = None if accounts is None else accounts[:_ACCOUNTS_NAMED]
                    if accounts is not None and len(accounts) > _ACCOUNTS_NAMED:
                        evidence["accounts_total"] = len(accounts)
                    opens = _opens_sentence(accounts)
                message = (
                    f"A security notice's email did not reach the member: the {rule} notice about '{label}' "
                    f"to {masked} was REFUSED by the email provider with SMTP {code}: {words[:160]}. "
                    f"{told}{opens} Reach the member another way; if they did not make this "
                    "change, treat it as an incident. If the provider is refusing all of Minas's mail, "
                    "this page's own email copy fails the same way - Telegram still carries it, and SMS "
                    "where it is configured. "
                    "The full address is in this notice's failed delivery row (failed_at); the "
                    "overview's \"Failed email deliveries\" card shows it while it is among the latest 20."
                )
                self._operator_alerter.page(
                    rule="security_notice_undelivered", severity="CRITICAL", message=message,
                    evidence=evidence,
                    dedup_scope=f"{rule}:{recipient.strip().lower()}:{account_label}",
                )
            except Exception as page_exc:  # noqa: BLE001 - the legs after this one, and the caller, come first
                logger.error("could not page the undelivered %s notice: %s", rule, page_exc)

    def _login_accounts(self, address, recipients) -> list | None:
        """The labels of the accounts `address`'s portal login opens, masked
        like the rest of the page and each cut to _ACCOUNT_NAME_CHARS, for a
        page about the login itself (decision 26b): read through
        member_accounts, the function the portal enforces with. `send` runs
        its legs outside any cursor block, so the read cannot wait on one.
        None, with the error logged, when it cannot be read - not [], which
        is a login that opens nothing: the page goes without the accounts
        rather than not at all."""
        try:
            from sentinel.portal import member_accounts

            return [_masked_text(str(row["label"]), recipients)[:_ACCOUNT_NAME_CHARS]
                    for row in member_accounts(self._db, address)]
        except Exception as exc:  # noqa: BLE001 - the page goes without them rather than not at all
            logger.error("could not read the accounts a login opens, for its page: %s", exc)
            return None

    def _route_receipt(self, emails, phones, account_id, alert_id, detection):
        """A receipt (a withdrawal to a destination this account has
        withdrawn to before) is a digest line, not a page: no SMS, no
        Telegram push, and no email of its own when the member's digest will
        carry it. The customer is still told - a member whose digest is off,
        or an address with no portal login, gets the plain email, because a
        receipt nobody reads is not a receipt."""
        from sentinel.alerts.digest import digest_frequency, member_exists_for_digest

        for phone in phones:
            self._record_sms(account_id, alert_id, detection.rule, "skipped", phone=phone,
                             detail="routine receipt: not texted; in the member digest")
        self._telegram_targets = []
        kept = []
        for email in emails:
            if self._db is not None and member_exists_for_digest(self._db, email) \
                    and digest_frequency(self._db, email) != "off":
                self._record("email", email, "skipped", detail="routine receipt: delivered in the member digest")
            else:
                kept.append(email)
        return kept, []

    def _apply_preferences(self, detection, emails, phones, account_id, alert_id):
        """Filter the primary contacts by their #24 preferences. Sets
        self._email_muted (addresses that keep telegram/sms but not email for
        this severity) and self._telegram_targets (addresses whose telegram
        copy is on). Returns (emails, phones, held_for)."""
        self._email_muted: set[str] = set()
        self._telegram_targets = list(emails)
        if self._db is None or getattr(detection, "is_notice", False) \
                or getattr(detection, "bypass_preferences", False):
            return emails, phones, []
        from sentinel.alerts.preferences import channel_allowed, hold_alert, in_quiet_hours, member_prefs

        # #103 item 3: a coverage alert is judged as a CRITICAL here - never
        # held for quiet hours, email is the floor - whatever level it carries.
        severity = preference_tier(getattr(detection, "rule", None), detection.severity)
        owner_of_phone = self._phone_owners(account_id)
        kept_emails, held_for, telegram = [], [], []
        muted = set()
        for email in emails:
            prefs = member_prefs(self._db, email)
            if severity != "CRITICAL" and in_quiet_hours(prefs):
                if hold_alert(self._db, email, alert_id):
                    held_for.append(email)
                continue
            kept_emails.append(email)
            if channel_allowed(prefs, severity, "telegram"):
                telegram.append(email)
            if not channel_allowed(prefs, severity, "email"):
                muted.add(email)
        kept_phones = []
        for phone in phones:
            owner = owner_of_phone.get(phone)
            if owner is None:
                kept_phones.append(phone)  # a phone with no paired login keeps today's behaviour
                continue
            if owner in held_for:
                continue
            if channel_allowed(member_prefs(self._db, owner), severity, "sms"):
                kept_phones.append(phone)
        self._email_muted = muted
        self._telegram_targets = telegram
        return kept_emails, kept_phones, held_for

    def _phone_owners(self, account_id) -> dict:
        """phone -> the contact email it is paired with on the account row,
        so SMS preferences follow the person, not the number."""
        if self._db is None or account_id is None:
            return {}
        with self._db.cursor() as cur:
            cur.execute("SELECT alert_email, alert_phone, alert_email_secondary, alert_phone_secondary "
                        "FROM accounts WHERE id = ?", (account_id,))
            row = cur.fetchone()
        if row is None:
            return {}
        owners = {}
        for email_col, phone_col in (("alert_email", "alert_phone"), ("alert_email_secondary", "alert_phone_secondary")):
            if row[phone_col] and row[email_col]:
                owners[row[phone_col]] = row[email_col].strip().lower()
        return owners

    def _send_telegram(self, alert, account_label, emails) -> int:
        """Copies to chats the contacts connected themselves (#18). Resolution
        is by the contact addresses this alert is already going to, so being
        connected confers nothing beyond a second copy of what your email
        gets - and in CUSTOMER copy, never the operator format with rule
        names and evidence.

        Returns how many chats ACCEPTED the copy: 0 when there is no chat to
        send to, no bot token, or every post failed. A refused tripwire's
        page says this count (decision 26b); it never decides whether that
        page goes."""
        if self._db is None or not emails:
            return 0
        from sentinel.alerts.telegram_link import chat_ids_for_emails

        chats = chat_ids_for_emails(self._db, emails)
        if not chats:
            return 0
        # env_setting (#101): a whitespace token is unset, so the one-shot
        # warning below fires instead of one failed post per chat per alert.
        token = env_setting("TELEGRAM_BOT_TOKEN")
        if not token:
            self._warn_once(
                "telegram",
                "a member has Telegram connected but TELEGRAM_BOT_TOKEN is not set - "
                "their copies are not being delivered",
            )
            return 0
        from sentinel import net

        text = alert.render_customer()
        accepted = 0
        for chat in chats:
            try:
                resp = net.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat, "text": text}, timeout=10.0,
                )
                resp.raise_for_status()
                accepted += 1
                self._record("telegram", str(chat), "accepted", provider="telegram")
            except Exception as exc:  # noqa: BLE001 - one chat failing must not cost the others
                logger.warning("member telegram copy to chat %s failed: %s", chat, exc)
                self._record("telegram", str(chat), "failed", provider="telegram", detail=str(exc))
        return accepted

    def _record(self, channel: str, recipient, status: str, *, provider=None, provider_ref=None, detail=None,
                when=None) -> None:
        """One delivery row for one leg (#103 item 2). Never raises. `when`
        stamps the row with an instant the caller also uses elsewhere; the
        default is now."""
        from sentinel.alerts.delivery import record_delivery

        leg = getattr(self, "_leg", None) or {}
        record_delivery(self._db, channel=channel, recipient=recipient, status=status,
                        audience=leg.get("audience", "customer"), kind=leg.get("kind", "alert"),
                        alert_id=leg.get("alert_id"), account_id=leg.get("account_id"), rule=leg.get("rule"),
                        provider=provider, provider_ref=provider_ref, detail=detail, when=when)

    def _send_sms(self, detection, alert, account_label, phones, account_id, alert_id, auditable) -> None:
        # Member preference: opted-out accounts get no SMS, except CRITICAL
        # when the business rule says a critical alert must still page.
        if auditable and not self._sms_pref_enabled(account_id) and (
            detection.severity != "CRITICAL" or not self._critical_overrides_optout()
        ):
                self._record_sms(
                    account_id, alert_id, detection.rule, "skipped",
                    detail="sms_alerts disabled by member preference",
                )
                return

        # Duplicate-SMS protection (same rule + account within the window).
        if auditable and self._sms_suppressed(account_id, detection.rule):
            self._record_sms(
                account_id, alert_id, detection.rule, "suppressed",
                detail="duplicate SMS suppressed within cooldown window",
            )
            return

        channel = build_sms_channel(phones)
        if channel is None:
            self._warn_once(
                "sms",
                "per-account SMS set but no SMS provider is configured - set CLICKSEND_* or "
                "TWILIO_* env to deliver these alerts (Telegram still fires)",
            )
            for phone in phones:
                self._record_sms(account_id, alert_id, detection.rule, "skipped", phone=phone,
                                 detail="no SMS provider configured")
            return
        try:
            receipts = channel.send(alert)
        except Exception as exc:  # noqa: BLE001 - delivery failure must not stall detection
            # Stays a log line for the same reason as the email path above
            # (#58), and this one additionally leaves an `sms_deliveries` row
            # with status='failed' - a queryable record, which is more than the
            # email path has.
            logger.warning("per-account SMS to %s failed: %s", phones, exc)
            for phone in phones:
                self._record_sms(
                    account_id, alert_id, detection.rule, "failed",
                    phone=phone, provider=type(channel).__name__, detail=str(exc),
                )
            return
        # Channels return one receipt per number; a stub/legacy channel that
        # returns None still gets per-phone 'sent' rows (no provider id).
        if receipts is None:
            receipts = [{"to": p, "provider": None, "provider_message_id": None} for p in phones]
        for receipt in receipts:
            self._record_sms(
                account_id, alert_id, detection.rule, "sent",
                phone=receipt.get("to"),
                provider=receipt.get("provider"),
                provider_message_id=receipt.get("provider_message_id"),
            )
