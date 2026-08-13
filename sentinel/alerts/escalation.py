from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sentinel.alerts.base import AlertChannel, AlertMessage
from sentinel.db import Database

logger = logging.getLogger("sentinel.escalation")


class Escalator:
    """Re-fires CRITICAL alerts that nobody acknowledged within the
    configured window. Acknowledge with: sentinel ack <alert_id>."""

    def __init__(self, channels: list[AlertChannel], unacknowledged_minutes: int):
        self._channels = channels
        self._window = timedelta(minutes=unacknowledged_minutes)

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
            for channel in self._channels:
                try:
                    channel.send(alert)
                except Exception as exc:  # noqa: BLE001 - escalation must try every channel
                    logger.error("escalation channel failed: %s", exc)
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE alerts SET escalated_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), row["id"]),
                )
            escalated += 1
        return escalated
