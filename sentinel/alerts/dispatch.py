from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sentinel.alerts.base import AlertChannel, AlertMessage
from sentinel.db import Database
from sentinel.detectors.base import Detection

logger = logging.getLogger("sentinel.alerts.dispatch")


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
) -> int | None:
    """Sends `detection` to the channels registered for its severity, unless
    an alert with the same dedup key already fired within the cooldown
    window for that severity. Returns the stored alert's row id when an
    alert was actually sent (so downstream delivery logs can reference it),
    or None when it was deduped away."""
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
            return None

    alert = AlertMessage(
        account_label=account_label,
        rule=detection.rule,
        severity=detection.severity,
        confidence=detection.confidence,
        message=detection.message,
        evidence=detection.evidence,
    )
    # Each send is a network call - Telegram HTTP, SMTP, an SMS provider - and
    # each is guarded on its own. Unguarded, one flaky channel skipped every
    # channel after it (including the ones configured as backups), left no
    # `alerts` row at all, and threw far enough to abandon the rest of that
    # account's poll cycle (#57).
    delivered = 0
    for channel in channels.get(detection.severity, []):
        try:
            channel.send(alert)
            delivered += 1
        except Exception as exc:  # noqa: BLE001 - a dead channel must not cost the others
            logger.error(
                "alert channel failed (%s) for %s on %s: %s",
                type(channel).__name__, detection.rule, account_label, exc,
            )

    # Recorded whether or not anyone took it. "We detected this" and "we managed
    # to tell someone" are different facts, and sharing a row's existence made
    # an undelivered alert indistinguishable from one that never happened - so
    # it was absent from the customer's history, the portal, the dashboard, and
    # anything handed to an exchange as evidence. `delivered_to` is what keeps
    # them apart, on the same footing as operator_pages.
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO alerts
                (account_id, rule, severity, dedup_key, message, evidence_json, confidence,
                 sent_at, delivered_to)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                delivered,
            ),
        )
        alert_id = cur.lastrowid
    return alert_id
