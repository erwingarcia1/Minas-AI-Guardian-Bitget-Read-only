from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sentinel.alerts.base import AlertChannel, AlertMessage
from sentinel.db import Database

logger = logging.getLogger("sentinel.escalation")


class Escalator:
    """Re-fires CRITICAL alerts that nobody acknowledged within the
    configured window. Acknowledge with: sentinel ack <alert_id>."""

    def __init__(self, channels: list[AlertChannel], unacknowledged_minutes: int,
                 operator_alerter=None):
        self._channels = channels
        self._window = timedelta(minutes=unacknowledged_minutes)
        # Optional: a deployment with no operator paging configured is worse
        # off, not broken. See `_page_if_nobody_heard`.
        self._operator_alerter = operator_alerter

    def check(self, db: Database) -> int:
        cutoff = (datetime.now(timezone.utc) - self._window).isoformat()
        with db.cursor() as cur:
            cur.execute(
                "SELECT a.id, a.rule, a.severity, a.message, a.confidence, ac.label "
                "FROM alerts a JOIN accounts ac ON ac.id = a.account_id "
                "WHERE a.severity = 'CRITICAL' AND a.acknowledged_at IS NULL "
                "AND a.escalated_at IS NULL AND a.sent_at < ? "
                # A live snooze holds escalation too (G-26, #30). Escalating
                # past a snooze would page the customer anyway and make the
                # button a lie; suppressing escalation *permanently* would be
                # the permanent mute this product does not have. It holds, and
                # then it lets go - which is what snoozes expiring is for.
                "AND NOT EXISTS (SELECT 1 FROM snoozes s WHERE s.account_id = a.account_id "
                "AND s.dedup_key = a.dedup_key AND s.expires_at > ?)",
                (cutoff, datetime.now(timezone.utc).isoformat()),
            )
            stale = cur.fetchall()

        escalated = 0
        for row in stale:
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
            self._page_if_nobody_heard(delivered, row)
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE alerts SET escalated_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), row["id"]),
                )
            escalated += 1
        return escalated

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
