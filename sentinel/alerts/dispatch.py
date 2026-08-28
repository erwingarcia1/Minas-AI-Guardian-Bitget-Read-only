from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sentinel.alerts.base import AlertChannel, AlertMessage
from sentinel.alerts.meaning import routing_tier
from sentinel.db import Database
from sentinel.detectors.base import Detection
from sentinel.snooze import active_snooze

logger = logging.getLogger("sentinel.alerts.dispatch")


# Evidence fields that identify *which* occurrence of a rule this is, in
# preference order. A detection is deduped against others sharing its rule,
# account and identity - so anything that distinguishes two real occurrences has
# to appear here.
#
# `symbol` is why this is a list rather than the previous
# `order_id or coin or ""`. An off-market fill carries `symbol`, not `coin`, and
# its `order_id` is None when the exchange didn't reference the fill - so the
# identity collapsed to the empty string and *every* off-market fill on an
# account shared one dedup key. The second symbol's alert was suppressed for the
# whole HIGH cooldown. G-47 is what made that reachable: before it, id-less
# fills were never judged at all, so they never got as far as being deduped.
_IDENTITY_FIELDS = ("order_id", "symbol", "coin", "address", "route")


def _record_global(db, channel, status, detection, account_id, alert_id, *, detail=None) -> None:
    """One delivery row per recipient of a GLOBAL channel (#103 item 2).
    These reach Minas, not the customer - audience 'operator' - which is
    exactly the distinction `delivered_to` alone could not make."""
    from sentinel.alerts.delivery import channel_name_of, record_delivery

    name = channel_name_of(channel)
    for recipient in (list(getattr(channel, "recipients", None) or []) or [None]):
        record_delivery(db, channel=name, recipient=recipient, status=status, audience="operator",
                        kind="alert", alert_id=alert_id, account_id=account_id, rule=detection.rule,
                        detail=detail)


def _dedup_key(account_id: int, detection: Detection) -> str:
    """Stable across repeated firings of the 'same' underlying condition
    (same rule + same evidence identity) so a flapping condition doesn't storm
    the alert channel - while two genuinely different occurrences of one rule
    stay two alerts.

    Absent fields are skipped rather than substituted with a falsy value: an
    identity of `""` is not an identity, and treating it as one is how distinct
    events collapse into a single suppressed alert. When a detection genuinely
    carries nothing identifying, rule + account is the honest key - that is one
    condition, not many."""
    identity_bits = ":".join(
        f"{field}={detection.evidence[field]}"
        for field in _IDENTITY_FIELDS
        if detection.evidence.get(field) is not None
    )
    raw = f"{account_id}:{detection.rule}:{identity_bits}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def dispatch(
    db: Database,
    channels: dict[str, list[AlertChannel]],
    account_id: int,
    account_label: str,
    detection: Detection,
    cooldown_minutes: dict[str, int],
    rule_cooldowns: dict[str, int] | None = None,
) -> int | None:
    """Sends `detection` to the channels registered for its severity, unless
    an alert with the same dedup key already fired within the cooldown
    window for that severity. Returns the stored alert's row id when an
    alert row was claimed (so downstream delivery logs can reference it),
    or None when it was suppressed as a repeat - in which case the repeat
    is counted on the alert row it repeated, never hidden (#22)."""
    dedup_key = _dedup_key(account_id, detection)
    # A per-rule override beats the severity default. Severity answers "how
    # urgent is this"; some rules also need "and how often may it repeat",
    # which is a different question. `account_unreachable` is the case that
    # forced it: at HIGH's 30 minutes a broken key would page twice an hour
    # until fixed, which trains a customer to ignore it, and with no repeat at
    # all an unwatched account looks fine forever (G-6).
    minutes = (rule_cooldowns or {}).get(detection.rule, cooldown_minutes.get(detection.severity, 60))
    cooldown = timedelta(minutes=minutes)
    now = datetime.now(timezone.utc)

    # A live snooze withholds the delivery and nothing else: the detection has
    # already run, and the alerts row below is written either way. The customer
    # silenced their phone, not the monitoring - so the history a customer
    # hands to an exchange still has the event in it, marked as silenced on
    # request rather than missing. Looked up BEFORE the claim block: cursor
    # blocks hold the database write lock and do not nest.
    snoozed_until = active_snooze(db, account_id, dedup_key)

    # The dedup check and the row that wins it happen under ONE cursor block
    # (#22). The block holds the database write lock, so two threads
    # detecting the same condition serialize here: the first claims the row,
    # the second sees it and is counted as a repeat. The old shape checked
    # in one block and inserted in another with the network sends between -
    # a window two simultaneous checks could both slip through.
    #
    # The row is claimed with delivered_to = 0 BEFORE any send. "We detected
    # this" and "we managed to tell someone" are different facts (#57), and
    # a process that dies mid-delivery now leaves "detected, reached nobody"
    # rather than nothing at all.
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, sent_at FROM alerts WHERE account_id = ? AND dedup_key = ? "
            "ORDER BY sent_at DESC LIMIT 1",
            (account_id, dedup_key),
        )
        row = cur.fetchone()
        if row is not None:
            last_sent = datetime.fromisoformat(row["sent_at"])
            if now - last_sent < cooldown:
                # Suppressed from the channels, never from the record: the
                # ongoing problem stays one alert that says how many times
                # and how recently it has kept firing, so nothing is hidden
                # while nothing storms the channel either (#22).
                cur.execute(
                    "UPDATE alerts SET repeat_count = repeat_count + 1, "
                    "last_repeat_at = ? WHERE id = ?",
                    (now.isoformat(), row["id"]),
                )
                return None
        cur.execute(
            """
            INSERT INTO alerts
                (account_id, rule, severity, dedup_key, message, evidence_json, confidence,
                 sent_at, delivered_to, snoozed_until)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                0,
                snoozed_until,
            ),
        )
        alert_id = cur.lastrowid

    alert = AlertMessage(
        account_label=account_label,
        rule=detection.rule,
        severity=detection.severity,
        confidence=detection.confidence,
        message=detection.message,
        evidence=detection.evidence,
        incident_id=alert_id,
        drill=getattr(detection, "is_drill", False),
    )
    # Each send is a network call - Telegram HTTP, SMTP, an SMS provider - and
    # each is guarded on its own. Unguarded, one flaky channel skipped every
    # channel after it (including the ones configured as backups), left no
    # `alerts` row at all, and threw far enough to abandon the rest of that
    # account's poll cycle (#57).
    # Route on the meaning, not the level (#103 item 3): a coverage alert
    # takes the CRITICAL channel set whatever its severity says, so "we are
    # blind on your account" never waits for a digest.
    routed = channels.get(routing_tier(detection.rule, detection.severity), [])
    delivered = 0
    for channel in ([] if snoozed_until else routed):
        try:
            channel.send(alert)
            delivered += 1
            _record_global(db, channel, "accepted", detection, account_id, alert_id)
        except Exception as exc:  # noqa: BLE001 - a dead channel must not cost the others
            logger.error(
                "alert channel failed (%s) for %s on %s: %s",
                type(channel).__name__, detection.rule, account_label, exc,
            )
            _record_global(db, channel, "failed", detection, account_id, alert_id, detail=str(exc))
    if snoozed_until:
        for channel in routed:
            _record_global(db, channel, "skipped", detection, account_id, alert_id,
                           detail=f"snoozed until {snoozed_until}")

    if delivered:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE alerts SET delivered_to = ? WHERE id = ?",
                (delivered, alert_id),
            )
    return alert_id
