from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sentinel.alerts.base import AlertChannel, AlertMessage
from sentinel.db import Database
from sentinel.detectors.base import Detection


def _dedup_key(account_id: int, detection: Detection) -> str:
    """Stable across repeated firings of the 'same' underlying condition
    (same rule + same primary evidence identifier) so a flapping condition
    doesn't storm the alert channel."""
    identity_bits = detection.evidence.get("order_id") or detection.evidence.get("coin") or ""
    raw = f"{account_id}:{detection.rule}:{identity_bits}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def dispatch(
    db: Database,
    channels: dict[str, list[AlertChannel]],
    account_id: int,
    account_label: str,
    detection: Detection,
    cooldown_minutes: dict[str, int],
) -> bool:
    """Sends `detection` to the channels registered for its severity, unless
    an alert with the same dedup key already fired within the cooldown
    window for that severity. Returns True if an alert was actually sent."""
    dedup_key = _dedup_key(account_id, detection)
    cooldown = timedelta(minutes=cooldown_minutes.get(detection.severity, 60))
    now = datetime.now(timezone.utc)

    with db.cursor() as cur:
        cur.execute(
            "SELECT sent_at FROM alerts WHERE account_id = ? AND dedup_key = ? "
            "ORDER BY sent_at DESC LIMIT 1",
            (account_id, dedup_key),
        )
        row = cur.fetchone()

    if row is not None:
        last_sent = datetime.fromisoformat(row["sent_at"])
        if now - last_sent < cooldown:
            return False

    alert = AlertMessage(
        account_label=account_label,
        rule=detection.rule,
        severity=detection.severity,
        confidence=detection.confidence,
        message=detection.message,
        evidence=detection.evidence,
    )
    for channel in channels.get(detection.severity, []):
        channel.send(alert)

    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO alerts
                (account_id, rule, severity, dedup_key, message, evidence_json, confidence, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                detection.rule,
                detection.severity,
                dedup_key,
                detection.message,
                json.dumps(detection.evidence, default=str),
                detection.confidence,
                now.isoformat(),
            ),
        )
    return True
