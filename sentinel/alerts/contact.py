from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sentinel.alerts.base import AlertMessage
from sentinel.alerts.email import EmailChannel
from sentinel.alerts.meaning import is_coverage_rule, preference_tier
from sentinel.alerts.sms import build_sms_channel
from sentinel.config import env_setting
from sentinel.net import smtp_refusal_code

logger = logging.getLogger("sentinel.alerts.contact")


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

        Paged once per process, on the same key that suppresses the log."""
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning(msg)
        if self._operator_alerter is None:
            return
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
                    for email in emails_for_email:
                        self._record("email", email, "failed", provider="smtp", detail=str(exc))
                    self._page_if_refused(exc, detection, account_label, alert_id, emails_for_email)

        # Guarded on its own like every leg: a Telegram outage must cost
        # neither the email above nor the SMS below.
        try:
            self._send_telegram(alert, account_label, self._telegram_targets)
        except Exception as exc:  # noqa: BLE001 - one channel must not cost the others
            logger.warning("per-member telegram for %s failed: %s", account_label, exc)

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

    def _send_telegram(self, alert, account_label, emails) -> None:
        """Copies to chats the contacts connected themselves (#18). Resolution
        is by the contact addresses this alert is already going to, so being
        connected confers nothing beyond a second copy of what your email
        gets - and in CUSTOMER copy, never the operator format with rule
        names and evidence."""
        if self._db is None or not emails:
            return
        from sentinel.alerts.telegram_link import chat_ids_for_emails

        chats = chat_ids_for_emails(self._db, emails)
        if not chats:
            return
        # env_setting (#101): a whitespace token is unset, so the one-shot
        # warning below fires instead of one failed post per chat per alert.
        token = env_setting("TELEGRAM_BOT_TOKEN")
        if not token:
            self._warn_once(
                "telegram",
                "a member has Telegram connected but TELEGRAM_BOT_TOKEN is not set - "
                "their copies are not being delivered",
            )
            return
        from sentinel import net

        text = alert.render_customer()
        for chat in chats:
            try:
                resp = net.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat, "text": text}, timeout=10.0,
                )
                resp.raise_for_status()
                self._record("telegram", str(chat), "accepted", provider="telegram")
            except Exception as exc:  # noqa: BLE001 - one chat failing must not cost the others
                logger.warning("member telegram copy to chat %s failed: %s", chat, exc)
                self._record("telegram", str(chat), "failed", provider="telegram", detail=str(exc))

    def _record(self, channel: str, recipient, status: str, *, provider=None, provider_ref=None, detail=None) -> None:
        """One delivery row for one leg (#103 item 2). Never raises."""
        from sentinel.alerts.delivery import record_delivery

        leg = getattr(self, "_leg", None) or {}
        record_delivery(self._db, channel=channel, recipient=recipient, status=status,
                        audience=leg.get("audience", "customer"), kind=leg.get("kind", "alert"),
                        alert_id=leg.get("alert_id"), account_id=leg.get("account_id"), rule=leg.get("rule"),
                        provider=provider, provider_ref=provider_ref, detail=detail)

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
