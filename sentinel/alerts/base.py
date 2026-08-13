from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


def runbook() -> str:
    """What to do if this was not you.

    Step 2 is the one with teeth, and the old wording - "revoke/rotate the API
    key(s) on this account" - was a safety defect rather than a phrasing
    problem. The Guardian's key is one of the keys on that account. It is
    read-only and cannot move money, so revoking it takes nothing away from an
    attacker; what it does is switch the monitoring off at the moment it
    matters most, silently, because nobody connects "I revoked my keys" with
    "I am no longer being watched". Panic makes revoke-everything the likely
    action, so the text has to separate their access from ours and say plainly
    what removing ours costs.

    The rest of the wording is still not signed off: decision 3 (legal/comms)
    owns "contact the exchange", "freeze", and how certain any of it may
    sound. This function exists so that review has one place to land."""
    return (
        "Immediate actions: 1) Freeze the account in the Bitget app if you did "
        "not authorize this. 2) Revoke any API key you do not recognise. Minas "
        "AI Guardian's own key is read-only - it cannot withdraw or trade - so "
        "revoking it takes nothing away from an attacker and stops the "
        "monitoring instead. If you do revoke every key, this account stays "
        "unwatched until a new Guardian key is issued. 3) Verify your "
        "withdrawal address whitelist in the Bitget app. 4) Enable/verify 2FA "
        "and Bitget's anti-phishing code. 5) Contact Bitget support if funds "
        f"moved without your authorization. {monitoring_check_line()}"
    )


def monitoring_check_line() -> str:
    """Where to confirm monitoring is still running after step 2.

    Points at the portal itself, not a deep link: there is no self-serve page
    for replacing a revoked key (onboarding is operator-driven), and a runbook
    that sends a frightened customer to a 404 is worse than one that sends
    them to the front door."""
    url = (os.environ.get("SENTINEL_PORTAL_URL") or "").strip().rstrip("/")
    if url:
        return f"Then check that monitoring is still running, and ask for a new key if it is not: {url}"
    return ("Then sign in to Minas AI Guardian to check monitoring is still "
            "running, and ask for a new key if it is not.")

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

    def render(self) -> str:
        """Operator format: rule names, confidence and raw evidence, all of
        which the operator needs and none of which a customer should receive.
        Unchanged - Telegram, SMS and the ops email channel all read this."""
        evidence_lines = "\n".join(f"  {k}: {v}" for k, v in self.evidence.items())
        return (
            f"🛡 MINAS AI GUARDIAN\n"
            f"[{self.severity}] {self.rule} - {self.account_label}\n"
            f"{self.message}\n"
            f"Confidence: {self.confidence}\n"
            f"Evidence:\n{evidence_lines}\n\n"
            f"Runbook:\n{runbook()}"
        )

    def operator_subject(self) -> str:
        return f"[Minas AI Guardian {self.severity}] {self.rule} - {self.account_label}"

    # --- customer-facing -----------------------------------------------------

    def headline(self) -> str:
        return _HEADLINES.get(self.rule, _GENERIC_HEADLINE)

    def customer_subject(self) -> str:
        """States the situation. No rule name, no severity token, no evidence."""
        if self.notice:
            return _NOTICE_SUBJECTS.get(self.rule, "A change was made to your Minas AI Guardian account")
        return f"{_SUBJECT_PREFIX.get(self.severity, '')}{self.headline()} - {self.account_label}"

    def render_customer(self) -> str:
        """Read on a phone, in a hurry, possibly by someone frightened.

        The first line carries the whole message; everything below it is
        assumed unread. The runbook appears only where it is warranted, and
        the specifics stay in the portal rather than the mailbox."""
        if self.notice:
            return f"{self.message}\n\n--\n{_FOOTER}"

        parts = [
            f"{self.headline()} ({self.account_label}). {portal_action_line()}",
            "",
            "We are telling you because it does not match how this account normally "
            "behaves. It may well be something you did - only you can say.",
        ]
        if self.severity in RUNBOOK_SEVERITIES:
            parts += ["", f"If this was not you:\n{runbook()}"]
        parts += ["", "--", _FOOTER]
        return "\n".join(parts)


class AlertChannel(ABC):
    @abstractmethod
    def send(self, alert: AlertMessage) -> None: ...
