from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


def advice_steps(exchange: str | None = None) -> list[str]:
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

    Per-rule variation is held (decision 3). Legal reviews the wording
    later and does not block it."""
    name = (exchange or "").strip().title() or "the exchange"
    return [
        "Revoke any API key on this account that you do not recognise. "
        "Leave the Minas AI Guardian key in place: it is read-only, and revoking it "
        "stops your monitoring without slowing anyone else down.",
        f"Check the withdrawal history in {name}'s own app or website for withdrawals "
        "you did not make.",
        f"Contact {name}'s support, quote the incident number above, and ask whether the "
        "withdrawal can be held and what they advise about moving your funds.",
    ]


def runbook(exchange: str | None = None) -> str:
    """The advice as plain text for the alert email: numbered steps, then the
    monitoring note below the list.

    The numbered list holds only things to *do*. What happens if step 1
    takes our key with it is a consequence, not a fourth action, and
    numbering it as one asked the reader to perform it. It sits below the
    list as a note.

    Plain text, deliberately: HTML mail is the phishing norm, and a
    plain-text security alert is harder to imitate convincingly. Multipart
    HTML+text is a separate decision, not an implementation detail."""
    steps = [f"{i}. {step}" for i, step in enumerate(advice_steps(exchange), 1)]
    return "\n\n".join(steps + [monitoring_note()])


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
# Severity is the gate because it is the only signal a detection carries today.
# Fire drills (#37) are not yet a distinct kind of message; when they land they
# must be excluded here explicitly, since a drill can legitimately be CRITICAL.
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
        return (
            f"🛡 MINAS AI GUARDIAN\n"
            f"[{self.severity}] {self.rule} - {self.account_label}\n"
            f"{self.message}\n"
            f"Confidence: {self.confidence}\n"
            f"Evidence:\n{evidence_lines}\n\n"
            f"Runbook:\n{runbook()}"
        )

    def operator_subject(self) -> str:
        if self.drill:
            return f"[Minas AI Guardian DRILL] {self.account_label}"
        return f"[Minas AI Guardian {self.severity}] {self.rule} - {self.account_label}"

    # --- customer-facing -----------------------------------------------------

    def headline(self) -> str:
        return _HEADLINES.get(self.rule, _GENERIC_HEADLINE)

    def customer_subject(self) -> str:
        """States the situation. No rule name, no severity token, no evidence."""
        if self.notice:
            return _NOTICE_SUBJECTS.get(self.rule, "A change was made to your Minas AI Guardian account")
        if self.drill:
            return f"[DRILL] A test of your Minas AI Guardian alerts - {self.account_label}"
        return f"{_SUBJECT_PREFIX.get(self.severity, '')}{self.headline()} - {self.account_label}"

    def render_customer(self) -> str:
        """Read on a phone, in a hurry, possibly by someone frightened.

        The first line carries the whole message; everything below it is
        assumed unread. The runbook appears only where it is warranted, and
        the specifics stay in the portal rather than the mailbox."""
        if self.notice:
            return f"{self.message}\n\n--\n{_FOOTER}"
        if self.drill:
            return (
                f"THIS IS A DRILL. {self.account_label} - a test of your Minas AI Guardian alerts. "
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
        opener = f"{self.account_label} - {headline[0].lower()}{headline[1:]}."
        # Plain text on purpose (decision 16): a reference the customer can
        # quote and find at the top of the portal home, never a link -
        # training the click is what a phisher's "view your incident" needs.
        reference = f" This is incident #{self.incident_id}." if self.incident_id else ""
        parts = [
            f"{opener}{reference} {portal_action_line()}",
            "",
            "We are telling you because it does not match how this account normally "
            "behaves. It may well be something you did - only you can say.",
        ]
        if self.severity in RUNBOOK_SEVERITIES:
            # A neutral heading, because the conditional now lives in each
            # step. "If this was not you:" as a preamble is exactly what a
            # summariser drops before turning the steps into instructions.
            parts += ["", "What to check:", "", runbook()]
        parts += ["", "--", _FOOTER]
        return "\n".join(parts)


class AlertChannel(ABC):
    @abstractmethod
    def send(self, alert: AlertMessage) -> None: ...
