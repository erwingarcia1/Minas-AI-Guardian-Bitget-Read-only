from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sentinel.alerts.base import AlertChannel, AlertMessage
from sentinel.db import Database

logger = logging.getLogger("sentinel.escalation")


class _EscalationDetection:
    """Adapts an escalation `AlertMessage` to the shape `ContactRouter.send`
    expects. The rule name keeps its ESCALATION prefix on purpose: someone
    reading this needs to know it is the second time, or it is
    indistinguishable from the message they already ignored."""

    # An escalation is "nobody acknowledged this": it must reach the person
    # whatever their #24 preferences say for ordinary detections.
    bypass_preferences = True

    def __init__(self, alert: AlertMessage):
        self.rule = alert.rule
        self.severity = alert.severity
        self.confidence = alert.confidence
        self.message = alert.message
        self.evidence = alert.evidence


class Escalator:
    """Escalates alerts nobody acknowledged within the configured windows.

    CRITICAL (window: escalation_unacknowledged_minutes, default 15): re-fires
    the global channels, reaches the customer's own contacts, and pages an
    operator - as before - and this is now also the FIRST moment a secondary
    contact hears anything (decision 1, #27: a secondary is an EMERGENCY
    contact, not a copy-to).

    HIGH (window: escalation_unacknowledged_minutes_high, default 60): reaches
    the secondary contact ONLY - no global re-fire, no primary re-send, no
    operator page. That is the decision's literal scope: the only thing a HIGH
    escalation exists to do is bring in the second pair of eyes.

    LOW and MEDIUM never escalate and never reach a secondary at all.

    Acknowledge with: sentinel ack <alert_id>."""

    def __init__(self, channels: list[AlertChannel], unacknowledged_minutes: int,
                 operator_alerter=None, contact_router=None,
                 high_unacknowledged_minutes: int = 60):
        self._channels = channels
        self._window = timedelta(minutes=unacknowledged_minutes)
        self._high_window = timedelta(minutes=high_unacknowledged_minutes)
        # Optional: a deployment with no operator paging configured is worse
        # off, not broken. See `_page_if_nobody_heard`.
        self._operator_alerter = operator_alerter
        # The customer's OWN contacts, primary and secondary (G-21, #27).
        # Without this, escalation re-fired down the same global channels the
        # original alert used - the path that has already failed to get a
        # response - and the second pair of eyes never saw it. Escalation that
        # repeats the unanswered channel is not escalation.
        self._contact_router = contact_router

    def check(self, db: Database) -> int:
        now = datetime.now(timezone.utc)
        cutoff_critical = (now - self._window).isoformat()
        cutoff_high = (now - self._high_window).isoformat()
        with db.cursor() as cur:
            cur.execute(
                "SELECT a.id, a.rule, a.severity, a.message, a.confidence, a.account_id, "
                "ac.label, ac.alert_email, ac.alert_phone, ac.alert_email_secondary, "
                "ac.alert_phone_secondary "
                "FROM alerts a JOIN accounts ac ON ac.id = a.account_id "
                "WHERE a.acknowledged_at IS NULL AND a.escalated_at IS NULL "
                "AND ((a.severity = 'CRITICAL' AND a.sent_at < ?) "
                "  OR (a.severity = 'HIGH' AND a.sent_at < ?)) "
                # A live snooze holds escalation too (G-26, #30). Escalating
                # past a snooze would page the customer anyway and make the
                # button a lie; suppressing escalation *permanently* would be
                # the permanent mute this product does not have. It holds, and
                # then it lets go - which is what snoozes expiring is for.
                "AND NOT EXISTS (SELECT 1 FROM snoozes s WHERE s.account_id = a.account_id "
                "AND s.dedup_key = a.dedup_key AND s.expires_at > ?)",
                (cutoff_critical, cutoff_high, now.isoformat()),
            )
            stale = cur.fetchall()

        escalated = 0
        for row in stale:
            if row["severity"] == "CRITICAL":
                self._escalate_critical(row)
            else:
                self._reach_the_secondary_contact(row, window=self._high_window)
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE alerts SET escalated_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), row["id"]),
                )
            escalated += 1
        return escalated

    def _escalate_critical(self, row) -> None:
        alert = AlertMessage(
            account_label=row["label"],
            rule=f"ESCALATION-unacknowledged: {row['rule']}",
            severity="CRITICAL",
            confidence=row["confidence"],
            message=(
                f"CRITICAL alert #{row['id']} has not been acknowledged. "
                f"Original: {row['message']} -- acknowledge with: sentinel ack {row['id']}"
            ),
            evidence={"original_alert_id": row["id"]},
        )
        delivered = 0
        for channel in self._channels:
            try:
                channel.send(alert)
                delivered += 1
            except Exception as exc:  # noqa: BLE001 - escalation must try every channel
                logger.error("escalation channel failed: %s", exc)
        self._reach_the_customers_own_contacts(row, alert)
        self._reach_the_secondary_contact(row, window=self._window)
        self._page_escalated(row)
        self._page_if_nobody_heard(delivered, row)

    def _reach_the_secondary_contact(self, row, *, window: timedelta) -> None:
        """The emergency contact's ONLY entry point for a live alert
        (decision 1, #27), so its message must say why they are hearing at
        all - "why am I suddenly getting this one" is not an improvement on
        "why do I get all of these".

        Deliberately carries NONE of the original alert text: detection
        messages embed amounts and destination addresses, and the emergency
        contact's message must not (the same rule as every customer-facing
        surface: no balances, no amounts, no addresses, no one-tap link).

        No secondary contact on file is a deliberate silence, not a delivery
        failure - it returns without recording or paging anything, which is
        the #57 distinction applied in reverse."""
        if self._contact_router is None:
            return
        emails = [row["alert_email_secondary"]]
        phones = [row["alert_phone_secondary"]]
        if not any(emails) and not any(phones):
            return
        minutes = int(window.total_seconds() // 60)
        severity_word = "critical" if row["severity"] == "CRITICAL" else "high-severity"
        note = AlertMessage(
            account_label=row["label"],
            rule=f"ESCALATION-unacknowledged: {row['rule']}",
            severity=row["severity"],
            confidence=row["confidence"],
            message=(
                f"You are receiving this because the primary contact for "
                f"{row['label']} has not responded to a {severity_word} alert in "
                f"{minutes} minutes. You are listed as the emergency contact for "
                f"this account. Sign in to the Minas AI Guardian member portal to "
                f"see the incident and what to do next."
            ),
            evidence={"original_alert_id": row["id"]},
        )
        note.is_notice = True  # the message IS the content; render it verbatim
        try:
            self._contact_router.send(
                note, row["label"], emails, phones,
                account_id=row["account_id"], alert_id=row["id"],
            )
        except Exception as exc:  # noqa: BLE001 - one path must not cost the other
            logger.error("escalation to the secondary contact failed: %s", exc)

    def _reach_the_customers_own_contacts(self, row, alert) -> None:
        """Deliver the escalation to the account's own PRIMARY email and
        phone (#27; the secondary goes through _reach_the_secondary_contact).

        Guarded on its own, on the same rule as everything else on a delivery
        path (#57): the router reaching the customer's address and the global
        channels reaching whatever else is configured are two independent
        deliveries, and losing both because one raised is the failure this
        codebase keeps having."""
        if self._contact_router is None:
            return
        # Primary only: the secondary contact has its own path with its own
        # wording (_reach_the_secondary_contact) - and its own rules about
        # what the message may contain.
        emails = [row["alert_email"]]
        phones = [row["alert_phone"]]
        if not any(emails) and not any(phones):
            return
        try:
            self._contact_router.send(
                _EscalationDetection(alert), row["label"], emails, phones,
                account_id=row["account_id"], alert_id=row["id"],
            )
        except Exception as exc:  # noqa: BLE001 - one path must not cost the other
            logger.error("escalation to the account's own contacts failed: %s", exc)

    def _page_escalated(self, row) -> None:
        """Minas is told that an escalation happened at all (#27).

        Distinct from `escalation_delivered_to_nobody`, which is about our
        machinery failing. This one is about the *customer*: a CRITICAL has
        gone unanswered for the whole window, which is worth an operator's
        attention whether or not the escalation message left the building.
        Both fire when both are true."""
        if self._operator_alerter is None:
            return
        self._operator_alerter.page(
            rule="critical_alert_escalated",
            severity="HIGH",
            message=(
                f"CRITICAL alert #{row['id']} ({row['rule']}) on {row['label']} was not "
                f"acknowledged and has been escalated. The customer has not responded to a "
                f"CRITICAL for the full escalation window."
            ),
            evidence={"original_alert_id": row["id"], "rule": row["rule"],
                      "account_label": row["label"]},
            account_id=row["account_id"],
            dedup_scope=f"escalated:{row['id']}",
        )

    def _page_if_nobody_heard(self, delivered: int, row) -> None:
        """The strongest entry in the #58 sweep, and the only one with a real
        claim on CRITICAL.

        Escalation is what happens when a CRITICAL goes unacknowledged. There
        is nothing after it. If the escalation itself reaches nobody, the
        customer is not answering *and* nobody knows they are not answering -
        and the log line this used to be went into a file that, by definition,
        nobody was reading at the time.

        Only on zero. One channel failing while another delivers is degraded,
        not uninformed, and paging on that would train an operator to ignore
        the channel - which is the same outcome as not paging at all. An empty
        channel list counts as zero on purpose: every escalation going nowhere
        is worse than one that fails loudly."""
        if delivered > 0 or self._operator_alerter is None:
            return
        self._operator_alerter.page(
            rule="escalation_delivered_to_nobody",
            severity="CRITICAL",
            message=(
                f"Alert #{row['id']} ({row['rule']}) on {row['label']} went unacknowledged, "
                f"escalated, and the escalation reached no channel at all. Escalation is the "
                f"last resort - there is nothing after it. Check the channels configured for "
                f"escalation."
            ),
            evidence={"original_alert_id": row["id"], "rule": row["rule"],
                      "account_label": row["label"]},
            dedup_scope="escalation",
        )


def send_critical_ack_closing_note(db: Database, alert_id: int, *, rules=None) -> None:
    """The closing note (decision 1, #27): when a CRITICAL alert is
    acknowledged, the SECONDARY contact hears exactly one line - "a critical
    alert on <account> was acknowledged at <time>".

    Why acknowledgement must not simply silence the emergency contact:
    whoever compromises a primary's portal access could acknowledge, and the
    one person listed for emergencies would never hear anything happened.
    CRITICAL only; the primary does not receive it - they are the one who
    acknowledged. Together with the escalation this caps the secondary at two
    messages per incident. Usually one. Often none.

    Called by BOTH acknowledgement writers (operators.acknowledge_alert and
    portal.acknowledge_alert_as_member) - the pair-of-gates rule: hooking one
    path leaves the other silently mute. Exactly-once comes from the callers
    firing only on a successful FIRST acknowledgement (their UPDATEs are
    guarded by acknowledged_at IS NULL). Guarded top to bottom: a note that
    cannot send must never fail the acknowledgement that triggered it.

    An account with no secondary contact returns without recording anything:
    a deliberate silence is not a delivery failure (#57's distinction). The
    router is called with account_id=None so no sms_deliveries bookkeeping
    rows are written against the original alert for this side-channel note."""
    from sentinel.alerts.contact import ContactRouter

    try:
        with db.cursor() as cur:
            cur.execute(
                "SELECT a.severity, a.acknowledged_at, ac.label, "
                "ac.alert_email_secondary, ac.alert_phone_secondary "
                "FROM alerts a JOIN accounts ac ON ac.id = a.account_id "
                "WHERE a.id = ?",
                (alert_id,),
            )
            row = cur.fetchone()
        if row is None or row["severity"] != "CRITICAL":
            return
        emails = [row["alert_email_secondary"]]
        phones = [row["alert_phone_secondary"]]
        if not any(emails) and not any(phones):
            return
        when = (row["acknowledged_at"] or "")[:16].replace("T", " ")
        note = AlertMessage(
            account_label=row["label"],
            rule="critical_alert_acknowledged",
            severity="CRITICAL",
            confidence="high",
            message=(
                f"A critical alert on {row['label']} was acknowledged at {when} UTC. "
                f"You are receiving this because you are listed as the emergency "
                f"contact for this account. If this acknowledgement is a surprise, "
                f"sign in to the Minas AI Guardian member portal and review the "
                f"incident."
            ),
            evidence={"alert_id": alert_id},
        )
        note.is_notice = True
        ContactRouter(db=db, rules=rules).send(
            note, row["label"], emails, phones, account_id=None, alert_id=None,
        )
    except Exception as exc:  # noqa: BLE001 - the ack must never fail because the note could not send
        logger.error("closing note for acknowledged CRITICAL #%s failed: %s", alert_id, exc)
