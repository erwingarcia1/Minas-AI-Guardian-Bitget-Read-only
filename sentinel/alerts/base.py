from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

# --- which rules carry the advice -------------------------------------------
#
# The runbook below is about funds: revoke keys you do not recognise, check
# the exchange's withdrawal history, ask the exchange to hold a withdrawal.
# It is proportionate on an alert that something may be moving money on an
# account, and wrong on everything else. On 2026-08-31 it was rendered on an
# operator page about a missing backup directory and an unset heartbeat URL
# (rule liveness_failed) - told to a customer, that page would have had them
# revoking exchange keys and phoning exchange support because Minas had not
# installed a cron job. The advice was attached to the message format, and
# every rule got it.
#
# So advice is a property of the RULE. Every rule identifier in the codebase
# is listed here as ACCOUNT_ADVICE (the canonical steps) or NO_ADVICE (none,
# said explicitly) - tests/test_incident_advice.py enumerates the rules in
# the source and fails on one that is in neither, which is what stops this
# recurring when a rule is added. A rule missing from the map at runtime
# gets NO advice: like the headline map, omission must never leak.
ACCOUNT_ADVICE = "account"
# Coverage (2026-09-03): a CRITICAL that reaches the customer directly and
# the one coverage alert they can act on - the key can be replaced from the
# portal (#69), or Minas asked. Its advice states that checks are failing,
# never that anything happened to the funds.
COVERAGE_ADVICE = "coverage"
# A data gap: a withdrawal record with no destination, so the unseen-address
# check could not run. The customer can look at the exchange's own record.
DATA_GAP_ADVICE = "data_gap"
# The cause is on OUR side (2026-09-23): the product has established that the
# checks fail because of something Minas must fix - the host's address
# changed under nine allowlisted keys on 2026-09-21 - and the customer must
# do NOTHING. The coverage advice ("replace your key") would have them
# rotate a working credential to a new one with no allowlist entry either:
# still dark, and the credential changed under us. Advice deepening the
# outage it describes.
OUR_SIDE_ADVICE = "our_side"
NO_ADVICE = None

RULE_ADVICE: dict[str, str | None] = {
    # Money may be moving, or the keys that can move it changed: the steps
    # are exactly the reversible acts that fit.
    "withdrawal_to_unseen_address": ACCOUNT_ADVICE,
    "withdrawal_initiated": ACCOUNT_ADVICE,
    "withdrawal_burst": ACCOUNT_ADVICE,
    "withdrawal_cannot_be_identified": ACCOUNT_ADVICE,
    "unexplained_quantity_decrease": ACCOUNT_ADVICE,
    "transfer_route_never_seen": ACCOUNT_ADVICE,
    "baseline_drift_new_deposit_source": ACCOUNT_ADVICE,
    "never_touched_pair_activity": ACCOUNT_ADVICE,
    "off_market_execution": ACCOUNT_ADVICE,
    "abnormal_fee_spend": ACCOUNT_ADVICE,
    "account_permission_state_changed": ACCOUNT_ADVICE,
    "two_factor_disabled": ACCOUNT_ADVICE,
    "monitoring_key_tamper_suspected": ACCOUNT_ADVICE,
    # Coverage, not compromise: the checks are failing. Its own advice - the
    # key can be replaced from the portal, or Minas asked; never the
    # withdrawal steps.
    "account_unreachable": COVERAGE_ADVICE,
    # A withdrawal record with no destination address: the customer can
    # confirm the destination in the exchange's own history.
    "unseen_address_check_degraded": DATA_GAP_ADVICE,
    # An operator page only (onboarding.py); it does not reach a customer.
    "account_onboarded_with_partial_baseline": NO_ADVICE,
    # Account notices: transactional mail whose whole point is the sentence
    # it carries. Never a runbook.
    "password_reset_requested": NO_ADVICE,
    "password_changed": NO_ADVICE,
    "app_signed_in": NO_ADVICE,  # decision 21c: a phone signed in to this login
    "alert_destination_changed": NO_ADVICE,
    "secondary_contact_added": NO_ADVICE,
    "telegram_disconnected": NO_ADVICE,
    "pause_requested": NO_ADVICE,
    "pause_expiring": NO_ADVICE,
    "pause_executed": NO_ADVICE,
    "pause_cancelled": NO_ADVICE,
    "data_erased": NO_ADVICE,
    "proof_of_life_digest": NO_ADVICE,
    "daily_digest": NO_ADVICE,
    # Escalation and the emergency contact: the note to the secondary says
    # why they are receiving it and points at the portal; the operator page
    # that an alert went unacknowledged is about Minas, not the account.
    "critical_alert_escalated": NO_ADVICE,
    "critical_alert_acknowledged": NO_ADVICE,
    # The stale bound and the burst bound (2026-09-08): a backlog report to
    # Minas, and the one collapsed summary a customer receives in a burst.
    "escalation_backlog": NO_ADVICE,
    "escalation_burst_collapsed": NO_ADVICE,
    # The key-replacement flow: a key was (or could not be) swapped.
    "monitoring_key_replaced": NO_ADVICE,
    "monitoring_key_replacement_wrong_account": NO_ADVICE,
    "monitoring_key_replacement_needs_an_operator": NO_ADVICE,
    # Drills: labelled as such on every surface, and the runbook withheld.
    "drill": NO_ADVICE,
    # Operator pages: Minas's own faults. No customer runbook has any place
    # on them - this is the liveness_failed page of 2026-08-31.
    "liveness_failed": NO_ADVICE,
    "liveness_recovered": NO_ADVICE,
    "monitoring_gap": NO_ADVICE,
    "heartbeat_unreachable": NO_ADVICE,
    "checks_behind_schedule": NO_ADVICE,
    "account_unwatchable_too_long": NO_ADVICE,
    "coverage_failure_fleet": NO_ADVICE,
    "host_auth_failure": NO_ADVICE,
    "exchange_records_untimestamped": NO_ADVICE,
    "alert_delivered_to_nobody": NO_ADVICE,
    "customer_email_refused": NO_ADVICE,
    "escalation_delivered_to_nobody": NO_ADVICE,
    "contact_channel_has_no_provider": NO_ADVICE,
    "alert_routing_fell_back_to_defaults": NO_ADVICE,
    "alert_channel_routed_but_not_configured": NO_ADVICE,
    "retention_prune_failed": NO_ADVICE,
    "rules_config_rejected": NO_ADVICE,
    "drill_no_account": NO_ADVICE,
    "drill_failed": NO_ADVICE,
}

# --- advice is a property of the DIAGNOSIS, not only the rule (2026-09-23) --
#
# RULE_ADVICE made advice a property of the rule. A rule can also fire with
# an ESTABLISHED cause - a diagnosis the product reached from evidence it
# holds, not a guess - and then the rule's own advice can be wrong: a
# coverage alert whose cause is our own host tells the customer to replace
# a key that is not the problem. So the lookup takes the rule AND the cause.
# The cause travels on the alert's evidence under CAUSE_KEY (so the stored
# row, the escalation copy and the incident page all read the same fact),
# and CAUSE_ADVICE maps each established cause to the advice kind that
# replaces the rule's. A cause not in this map changes nothing - omission
# never changes advice - and a cause never ADDS advice to a rule that
# carries none. The next shared cause (an exchange-wide outage, a route, a
# certificate) is a row here and its text below, not a special case inline.
CAUSE_KEY = "cause"
CAUSE_ADVICE: dict[str, str] = {
    # alerts/fleet.py: enough accounts refused authentication together that
    # the only thing they share - this host - is the cause.
    "host_auth_failure": OUR_SIDE_ADVICE,
}


def established_cause(evidence: dict | None) -> str | None:
    """The cause an alert carries, or None. Read from the evidence dict a
    detection, a stored row or an escalation copy all hold."""
    if not isinstance(evidence, dict):
        return None
    cause = evidence.get(CAUSE_KEY)
    return str(cause) if cause else None


# The escalator re-sends an unacknowledged alert to Minas under this prefix
# plus the original rule; the advice, if any, is the original rule's.
ESCALATION_PREFIX = "ESCALATION-unacknowledged: "


def original_rule(rule: str | None) -> str | None:
    """The rule an escalation is about. The escalator keeps the prefix on
    the message so an operator can tell the second sending from the first;
    everything that maps a rule to words the CUSTOMER reads - headline,
    subject, advice - must resolve it, or a lost API key escalates as
    "Urgent: Unusual activity was detected on this account" (2026-08-30,
    42 times)."""
    if rule is not None and rule.startswith(ESCALATION_PREFIX):
        return rule[len(ESCALATION_PREFIX):]
    return rule


def advice_kind(rule: str | None, cause: str | None = None) -> str | None:
    """Which advice a rule carries, or None. Unknown rules carry none. An
    established cause replaces the rule's kind - only where the rule
    carries advice at all, and only for a cause CAUSE_ADVICE knows."""
    if rule is None:
        return None
    kind = RULE_ADVICE.get(original_rule(rule), NO_ADVICE)
    if kind is None:
        return None
    return CAUSE_ADVICE.get(cause, kind) if cause else kind


def advice_steps(exchange: str | None = None, *, rule: str | None = ..., cause: str | None = None) -> list[str]:
    """THE canonical "what to do next" (decisions 3 and 9, 2026-08-27): one
    source for the alert runbook and the incident page, so they cannot
    drift. Three steps, in this order, and nothing else:

      revoke unrecognised keys -> check the exchange's own withdrawal
      history -> contact exchange support with a reference.

    **The line: instruct for the reversible, never for the irreversible.**
    Revoking an unrecognised key and reading a history cost nothing if the
    alert was the customer's own bot. Moving funds is not a step - during a
    live compromise it can hand the attacker a destination - so it appears
    only as something to raise WITH the exchange.

    **Tone: certain about what was observed, never about what it means.**
    The alert's own first line states the observation as a fact. These
    steps never say "compromised", "attacker" or "theft": that is an
    inference the record does not support. And they carry no hedging
    preamble - Gmail's summariser dropped "if this was not you" and turned
    advice into an instruction we never gave - so each sentence has to read
    correctly ALONE, as a plain instruction for a reversible act.

    **The Guardian's key is one of the keys on that account.** It is
    read-only and cannot move money, so revoking it takes nothing from an
    attacker and switches the monitoring off - silently, because nobody
    connects "I revoked my keys" with "I am no longer watched". Panic makes
    revoke-everything the likely action, so ours is named in step 1.

    Per-rule variation of the WORDING is held (decision 3); WHETHER a rule
    carries the steps at all is `RULE_ADVICE`. With `rule` given, a rule
    that carries no advice returns no steps - the callers that render a
    message pass it. Without it (the default: the tests and the docs that
    quote the text) the steps themselves are returned. Legal reviews the
    wording later and does not block it."""
    kind = ACCOUNT_ADVICE if rule is ... else advice_kind(rule, cause)
    if kind is None:
        return []
    name = (exchange or "").strip().title() or "the exchange"
    if kind == COVERAGE_ADVICE:
        return _coverage_steps(name)
    if kind == DATA_GAP_ADVICE:
        return _data_gap_steps(name)
    if kind == OUR_SIDE_ADVICE:
        return _our_side_steps()
    return [
        "Revoke any API key on this account that you do not recognise. "
        "Leave the Minas AI Guardian key in place: it is read-only, and revoking it "
        "stops your monitoring without slowing anyone else down.",
        f"Check the withdrawal history in {name}'s own app or website for withdrawals "
        "you did not make.",
        f"Contact {name}'s support, quote the incident number above, and ask whether the "
        "withdrawal can be held and what they advise about moving your funds.",
    ]


def _coverage_steps(name: str) -> list[str]:
    """account_unreachable. Certain about what was observed - the checks are
    failing - and never about what it means. Every step is reversible, and
    the one that restores monitoring is the customer's own to take (#69)."""
    return [
        f"Check in {name}'s own app or website that the API key issued to Minas AI Guardian "
        "still exists, has not expired and is still read-only.",
        "Put a new read-only key in place yourself: sign in to Minas AI Guardian, open the "
        "account and follow the replacement steps there.",
        "Ask us if anything there does not work, or if you would rather we did it with you.",
    ]


def _data_gap_steps(name: str) -> list[str]:
    """unseen_address_check_degraded: a withdrawal record with no destination
    address, so the Guardian could not compare it with the addresses this
    account has used. The exchange's own record has the destination."""
    return [
        f"Check the withdrawal in {name}'s own app or website and confirm that its destination "
        "is an address you know.",
        "Ask us if anything in that record does not add up; we will look at it with you.",
    ]


def _our_side_steps() -> list[str]:
    """The cause is ours. Two instructions, both for doing nothing to the
    key, because the frightened reader's likely act - replace it, revoke
    it - is the one that deepens the outage. Certain about what was
    observed; nothing about the funds."""
    return [
        "Leave your API key exactly as it is: do not replace it and do not revoke it. "
        "The key is not the problem, and a new one would be refused in the same way until "
        "we have finished.",
        "Wait for our message that the checks have succeeded again. Nothing else is needed "
        "from you, and you can ask us at any time.",
    ]


def advice_lead(rule: str | None, cause: str | None = None) -> str:
    """What was observed, stated before the steps, for the kinds whose steps
    would otherwise read as if something had happened to the funds. Empty
    for the account advice: there the alert's first line is the observation
    and the steps stand alone by design."""
    kind = advice_kind(rule, cause)
    if kind == OUR_SIDE_ADVICE:
        return ("What was observed: the exchange is refusing Minas AI Guardian's read-only checks on "
                "this account because of a change on our side - not because of anything you did, and "
                "not because of anything wrong with your key. The account is not being watched until "
                "we have put it right. Nothing in this record says anything happened to your funds.")
    if kind == COVERAGE_ADVICE:
        return ("What was observed: Minas AI Guardian's read-only checks on this account have failed "
                "repeatedly, so the account is not being watched until they succeed again. Nothing "
                "in this record says anything happened to your funds.")
    if kind == DATA_GAP_ADVICE:
        return ("What was observed: a withdrawal record from the exchange carried no destination "
                "address, so Minas AI Guardian could not compare it with the addresses this account "
                "has used before. Nothing in this record says the withdrawal was not yours.")
    return ""


def advice_note(rule: str | None, cause: str | None = None) -> str:
    """The paragraph below the steps. Only the account advice has one: the
    consequence of its step 1 (revoking every key). The coverage advice
    already IS about the key."""
    return monitoring_note() if advice_kind(rule, cause) == ACCOUNT_ADVICE else ""


def runbook(exchange: str | None = None, *, rule: str | None = ..., cause: str | None = None) -> str:
    """The advice as plain text for the alert email: numbered steps, then the
    monitoring note below the list. Empty for a rule that carries no advice
    (`RULE_ADVICE`): a message with no applicable advice gets NO block,
    never a generic one.

    The numbered list holds only things to *do*. What happens if step 1
    takes our key with it is a consequence, not a fourth action, and
    numbering it as one asked the reader to perform it. It sits below the
    list as a note.

    Plain text, deliberately: HTML mail is the phishing norm, and a
    plain-text security alert is harder to imitate convincingly. Multipart
    HTML+text is a separate decision, not an implementation detail."""
    steps = [f"{i}. {step}" for i, step in enumerate(advice_steps(exchange, rule=rule, cause=cause), 1)]
    if not steps:
        return ""
    lead = monitoring_note() if rule is ... else advice_note(rule, cause)
    head = advice_lead(rule, cause) if rule is not ... else ""
    parts = ([head] if head else []) + steps + ([lead] if lead else [])
    return "\n\n".join(parts)


def customer_advised_line(rule: str | None = ..., cause: str | None = None) -> str:
    """What the customer's copy of an advice-bearing alert told them, said
    to an operator in the third person. One line per advice kind, no
    imperative to the reader: the operator format is read by Minas, and
    "revoke any API key you do not recognise" addressed to MINAS-OPS is the
    wrong audience. Empty for a rule with no advice."""
    kind = ACCOUNT_ADVICE if rule is ... else advice_kind(rule, cause)
    if kind == OUR_SIDE_ADVICE:
        return ("Customer advised: the checks are being refused because of a change on Minas's side; "
                "they were asked to leave their key exactly as it is and to do nothing until told the "
                "checks succeed again.")
    if kind == COVERAGE_ADVICE:
        return ("Customer advised: the read-only checks are failing and the account is unwatched "
                "until they succeed; check the exchange key exists and is read-only, replace it "
                "from the portal, or ask Minas.")
    if kind == DATA_GAP_ADVICE:
        return ("Customer advised: the withdrawal record had no destination; confirm the destination "
                "in the exchange's own history, ask Minas if it does not add up.")
    if kind == ACCOUNT_ADVICE:
        return ("Customer advised: revoke unrecognised API keys (keeping the Guardian key), "
                "check the exchange's own withdrawal history, contact the exchange's support "
                "with the incident number.")
    return ""


def monitoring_note() -> str:
    """The consequence of step 1 (revoking every key), sitting below the list.

    Points at the portal front door, not a deep link into the replacement page
    (#69). The page exists now, and the note says a customer can do it
    themselves - but sending them to a URL that lands directly on "paste your
    API key here" is the phishing template we would be teaching them to trust.
    The rule the copy is trying to install is *sign in, then look*, and a link
    that skips the signing in undermines it in the one message where it counts.

    Carries its own condition like every step, for the same reason: read alone,
    it must still be about a thing that may not have happened."""
    url = (os.environ.get("SENTINEL_PORTAL_URL") or "").strip().rstrip("/")
    where = f" Sign in at {url}" if url else " Sign in to Minas AI Guardian"
    return ("Note: if you revoked every key, this account is no longer "
            "monitored until a new Guardian key is issued." + where +
            " and open the account to put a new read-only key in place yourself; "
            "ask us if anything there does not work.")

# The runbook tells a frightened person to freeze an account and revoke keys.
# It belongs on the alerts where that is the proportionate response and on no
# others. Printed on every INFO notice it becomes the paragraph customers learn
# to scroll past, and the one time it matters it reads as boilerplate - so
# gating it is a property of the alert, not formatting.
#
# Two gates, both required: the RULE must carry advice (`RULE_ADVICE` above)
# and the severity must warrant interrupting someone with it. Drills are
# excluded on their own flag, since a drill can legitimately be CRITICAL.
RUNBOOK_SEVERITIES = frozenset({"CRITICAL", "HIGH"})

# What the customer is told happened, per rule. The rule name is an internal
# identifier and never appears in customer copy; neither do evidence values,
# which is where addresses and balances live (see the detectors - the free-text
# `message` embeds the withdrawal address). Detail belongs in the portal,
# behind a session; the email's job is to say enough to make someone open it.
#
# Anything absent from this map falls back to the generic line rather than
# de-slugging the rule, so a new detector cannot leak its identifier into a
# customer's inbox by omission.
_HEADLINES = {
    "withdrawal_to_unseen_address": "A withdrawal went to an address this account has not used before",
    "withdrawal_initiated": "A withdrawal was started on this account",
    "withdrawal_burst": "Several withdrawals were started in a short space of time",
    "withdrawal_cannot_be_identified": "A withdrawal could not be matched to known activity",
    "unexplained_quantity_decrease": "A balance fell by more than recent activity explains",
    "never_touched_pair_activity": "There was trading in a market this account has never traded",
    "off_market_execution": "A trade executed at a price well away from the market",
    "abnormal_fee_spend": "Trading fees are far above their usual level",
    "transfer_route_never_seen": "Funds moved along a route this account has never used",
    "baseline_drift_new_deposit_source": "New deposit addresses were added to this account",
    "monitoring_key_tamper_suspected": "The exchange is rejecting the monitoring key for this account",
    "account_permission_state_changed": "The permissions on the exchange API key changed",
    "two_factor_disabled": "Two-factor authentication was switched off",
    "account_unreachable": "Contact with this account has been lost",
    "critical_alert_escalated": "An urgent alert has not been acknowledged",
    "account_onboarded_with_partial_baseline": "Monitoring has started with only part of the account history",
}

_GENERIC_HEADLINE = "Unusual activity was detected on this account"

# Account notices are not detections: they are transactional mail whose whole
# point is the sentence they carry (a reset link, a changed destination), so
# these render their message verbatim and never carry a runbook.
_NOTICE_SUBJECTS = {
    "password_reset_requested": "Reset your Minas AI Guardian password",
    "password_changed": "Your Minas AI Guardian password was changed",
    "alert_destination_changed": "Your Minas AI Guardian alerts are going somewhere new",
    "escalation_burst_collapsed": "Several alerts on your account remain unacknowledged",
}

# Prefix by severity. Only the two severities that warrant interrupting someone
# get one; the rest arrive unadorned so the prefix keeps its meaning.
_SUBJECT_PREFIX = {"CRITICAL": "Urgent: ", "HIGH": "Please check: "}

_FOOTER = (
    "Minas AI Guardian watches this account and tells you what it sees. "
    "It never moves funds and never places trades."
)


def portal_action_line() -> str:
    """The 'go and look' line, with a link when the portal's public URL is set.

    Deliberately a sign-in link and not a one-tap action link: an
    acknowledgement that works straight from the mailbox hands anyone who has
    taken over that mailbox a mute button for the alert about the takeover
    (the judgment recorded against #26). sms.py builds its own shorter version
    of this line because it has a length budget to keep."""
    url = (os.environ.get("SENTINEL_PORTAL_URL") or "").strip().rstrip("/")
    if url:
        return f"Sign in to see the details: {url}"
    return "Sign in to Minas AI Guardian to see the details."


@dataclass
class AlertMessage:
    account_label: str
    rule: str
    severity: str
    confidence: str
    message: str
    evidence: dict
    # Set by the notice adapters (portal._PlainNotice). Detections leave it
    # False and are rendered from the headline map instead of their free text.
    notice: bool = False
    # The alerts-table row id, known before any send since #22 claimed the
    # row first. Decision 16's replacement for a deep link: the customer
    # email names "incident #N" as PLAIN TEXT - findable and quotable, but
    # nothing to click - and the portal home pins the newest unacknowledged
    # incident so signing in directly lands on it in one step.
    incident_id: int | None = None
    # #37: a drill. The label is the whole point of a drill: a customer must
    # never mistake it for a real event, and an operator must never treat it
    # as one - so it is said first, on every surface, and the runbook is
    # withheld (the "explicitly excluded" this file's gate anticipated).
    drill: bool = False
    # #8: the member's own name for the connection, used wherever a PERSON
    # reads the account: subject, customer body, digest. The operator format
    # keeps `account_label`, the slug - that is the identity operators and
    # the CLI work with. Never a key.
    display_label: str | None = None
    # The second sending of an unacknowledged alert (the escalator sets it
    # on the customer's copy). The customer's body says so in one sentence,
    # because otherwise it is indistinguishable from the message they
    # already have; the headline, subject and advice are the ORIGINAL
    # rule's, so a coverage escalation reads as coverage.
    escalation: bool = False

    @property
    def customer_label(self) -> str:
        return (self.display_label or "").strip() or self.account_label

    @property
    def cause(self) -> str | None:
        """The established cause this alert carries (CAUSE_KEY on its
        evidence), or None: what turns the rule's advice into the
        diagnosis's."""
        return established_cause(self.evidence)

    def render(self) -> str:
        """Operator format: rule names, confidence and raw evidence, all of
        which the operator needs and none of which a customer should receive.
        Unchanged - Telegram, SMS and the ops email channel all read this."""
        evidence_lines = "\n".join(f"  {k}: {v}" for k, v in self.evidence.items())
        if self.drill:
            return (
                f"🛡 MINAS AI GUARDIAN - DRILL\n"
                f"[DRILL] {self.rule} - {self.account_label}\n"
                f"{self.message}\n"
                "This is a scheduled test of the alert path, not a real event. No action is needed.\n"
                f"Evidence:\n{evidence_lines}"
            )
        body = (
            f"🛡 MINAS AI GUARDIAN\n"
            f"[{self.severity}] {self.rule} - {self.account_label}\n"
            f"{self.message}\n"
            f"Confidence: {self.confidence}\n"
            f"Evidence:\n{evidence_lines}"
        )
        # Only where the rule carries advice (RULE_ADVICE), and in the
        # operator's voice: what the customer was told, in one line - never
        # the customer's steps themselves. The page of 2026-08-31 told
        # MINAS-OPS to "sign in to Minas AI Guardian ... yourself; ask us if
        # anything there does not work" (monitoring_note, via the runbook);
        # customer copy on an operator surface is the wrong audience, whether
        # or not the rule applies. tests/test_customer_channel_guard.py holds
        # the line in both directions.
        advised = customer_advised_line(self.rule, self.cause)
        return f"{body}\n\n{advised}" if advised else body

    def operator_subject(self) -> str:
        if self.drill:
            return f"[Minas AI Guardian DRILL] {self.account_label}"
        return f"[Minas AI Guardian {self.severity}] {self.rule} - {self.account_label}"

    # --- customer-facing -----------------------------------------------------

    def headline(self) -> str:
        return _HEADLINES.get(original_rule(self.rule), _GENERIC_HEADLINE)

    def customer_subject(self) -> str:
        """States the situation. No rule name, no severity token, no evidence."""
        if self.notice:
            return _NOTICE_SUBJECTS.get(original_rule(self.rule), "A change was made to your Minas AI Guardian account")
        if self.drill:
            return f"[DRILL] A test of your Minas AI Guardian alerts - {self.customer_label}"
        return f"{_SUBJECT_PREFIX.get(self.severity, '')}{self.headline()} - {self.customer_label}"

    def render_customer(self) -> str:
        """Read on a phone, in a hurry, possibly by someone frightened.

        The first line carries the whole message; everything below it is
        assumed unread. The runbook appears only where it is warranted, and
        the specifics stay in the portal rather than the mailbox."""
        if self.notice:
            return f"{self.message}\n\n--\n{_FOOTER}"
        if self.drill:
            return (
                f"THIS IS A DRILL. {self.customer_label} - a test of your Minas AI Guardian alerts. "
                "Nothing has happened on your account and no action is needed. If this message "
                "reached you, the path from our detection to your phone works.\n\n"
                f"{portal_action_line()}\n\n--\n{_FOOTER}"
            )

        # Account first, headline second. The subject ends with the label, so
        # a body opening with "headline (label)" put the same words twice in a
        # row on screen - and when a label carries its own parenthetical it
        # nested brackets on top of that. Leading with the account also reads
        # better for a customer holding more than one.
        headline = self.headline()
        opener = f"{self.customer_label} - {headline[0].lower()}{headline[1:]}."
        # Plain text on purpose (decision 16): a reference the customer can
        # quote and find at the top of the portal home, never a link -
        # training the click is what a phisher's "view your incident" needs.
        reference = f" This is incident #{self.incident_id}." if self.incident_id else ""
        # An escalation is the second sending: said in one plain sentence,
        # with the same headline and advice as the first, never "Urgent:
        # Unusual activity" for a rule that has its own words.
        again = " This alert has not been acknowledged and is being sent again." if self.escalation else ""
        parts = [
            f"{opener}{reference}{again} {portal_action_line()}",
            "",
            "We are telling you because it does not match how this account normally "
            "behaves. It may well be something you did - only you can say.",
        ]
        steps = runbook(rule=self.rule, cause=self.cause) if self.severity in RUNBOOK_SEVERITIES else ""
        if steps:
            # A neutral heading, because the conditional now lives in each
            # step. "If this was not you:" as a preamble is exactly what a
            # summariser drops before turning the steps into instructions.
            parts += ["", "What to check:", "", steps]
        parts += ["", "--", _FOOTER]
        return "\n".join(parts)


class AlertChannel(ABC):
    # Set by the routing builder to the name the channel was routed under
    # ('telegram', 'telegram_team', 'email', 'sms'), so a delivery record can
    # say which leg it was (#103 item 2). Falls back to the class name.
    route_name: str | None = None

    @abstractmethod
    def send(self, alert: AlertMessage) -> None: ...

    @property
    def recipients(self) -> list[str]:
        """Who this channel delivers to, as the delivery record names them:
        addresses, phone numbers or a chat id. Empty when unknown."""
        return []
