"""The delivery record: one row per recipient per channel per attempt.

"Was this customer actually told" was unanswerable for every alert in the
database (#103, #14): `alerts.delivered_to` counted the GLOBAL channels only,
`sms_deliveries` recorded the SMS leg only, and the per-member email and
Telegram legs - the ones that reach the customer - left nothing behind. So
the audit's honest verdict on 124 unreachable alerts was "unverifiable".

This module is the one writer. `sms_deliveries` had the right shape (who,
provider, provider's message id, status, detail, when), so `deliveries` IS
that shape generalised - a `channel`, a `recipient`, an `audience` and a
`kind` added - and the old tables are compatibility VIEWS over it, so the
dashboard card, the drill's "last message to you" and every existing query
keep reading what they read. `alerts.delivered_to` is untouched: dispatch
still counts global channels there, and that count is now also derivable
from this table.

Statuses, deliberately few:
  accepted   the transport took it (SMTP accepted the message, Telegram
             returned ok, the SMS provider returned an id). Not "read".
  failed     the transport refused or raised - detail says what it said
  skipped    never attempted for a stated reason (no address on file, no
             provider configured, preference off, channel not routed)
  suppressed a repeat inside a cooldown window
  held       withheld by the member's quiet hours, released in a digest
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sentinel.db import Database

logger = logging.getLogger(__name__)

CHANNELS = ("email", "telegram", "telegram_team", "sms", "digest")
AUDIENCES = ("customer", "secondary", "operator")
KINDS = ("alert", "escalation", "notice", "digest", "drill", "operator_page")
STATUSES = ("accepted", "failed", "skipped", "suppressed", "held")


_BY_CLASS = {"emailchannel": "email", "telegramchannel": "telegram",
             "twiliosmschannel": "sms", "clicksendsmschannel": "sms"}


def channel_name_of(channel) -> str:
    """The delivery-record channel for a channel object: the name it was
    routed under when the builder set one, else its class, else a guess
    from its class name - a stub in a test, never a production channel."""
    routed = getattr(channel, "route_name", None)
    if routed in CHANNELS:
        return routed
    cls = type(channel).__name__.lower()
    if cls in _BY_CLASS:
        return _BY_CLASS[cls]
    if "mail" in cls:
        return "email"
    if "sms" in cls:
        return "sms"
    return "telegram"


def record_delivery(
    db: Database | None,
    *,
    channel: str,
    recipient: str | None,
    status: str,
    audience: str = "customer",
    kind: str = "alert",
    alert_id: int | None = None,
    account_id: int | None = None,
    rule: str | None = None,
    provider: str | None = None,
    provider_ref: str | None = None,
    detail: str | None = None,
    when: datetime | None = None,
) -> int | None:
    """Write one row. Never raises: the delivery already happened (or
    already failed), and losing the record must not turn that into a second
    failure on the send path. Returns the row id, or None when there is no
    database or the write failed."""
    if db is None:
        return None
    if channel not in CHANNELS:
        raise ValueError(f"unknown delivery channel {channel!r}")
    if status not in STATUSES:
        raise ValueError(f"unknown delivery status {status!r}")
    if audience not in AUDIENCES:
        raise ValueError(f"unknown delivery audience {audience!r}")
    if kind not in KINDS:
        raise ValueError(f"unknown delivery kind {kind!r}")
    stamp = (when or datetime.now(timezone.utc)).isoformat()
    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO deliveries (alert_id, account_id, rule, channel, recipient, audience, kind, "
                "status, provider, provider_ref, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (alert_id, account_id, rule, channel,
                 (recipient or "").strip().lower() if channel in ("email", "digest") else recipient,
                 audience, kind, status, provider, provider_ref, detail, stamp),
            )
            return cur.lastrowid
    except Exception as exc:  # noqa: BLE001 - the record must not cost the delivery
        logger.error("could not record %s delivery to %s: %s", channel, recipient, exc)
        return None


def last_accepted(db: Database, *, kind: str | None = None, channel: str | None = None,
                  recipient: str | None = None) -> str | None:
    """ISO timestamp of the newest accepted delivery matching the filters,
    or None. The liveness gate reads "a digest was sent in the last 48h"
    from here; the drill reads "the last message to you" from here."""
    clauses, params = ["status = 'accepted'"], []
    for column, value in (("kind", kind), ("channel", channel),
                          ("recipient", recipient.strip().lower() if recipient else None)):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    with db.cursor() as cur:
        cur.execute(f"SELECT max(created_at) AS last FROM deliveries WHERE {' AND '.join(clauses)}", params)
        row = cur.fetchone()
    return row["last"] if row else None
