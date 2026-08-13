"""Silencing one alert, for a while (G-26, #30).

This is the only feature here whose purpose is to stop an alert reaching
someone. Dedup and cooldown also suppress, but they suppress a *repeat* of
something already delivered; a snooze suppresses a delivery outright, on
request, at the moment a customer is least likely to be sure what is happening
to their account. Three properties keep that from becoming a way to turn the
product off:

**It is keyed on the alert, not the rule.** `dedup_key` already identifies "the
same underlying condition" — rule plus the evidence identity (order id,
address, symbol, coin, route) plus the account. Snoozing on that key means a
withdrawal to a *different* address is a different alert and stays loud, which
is the case that matters: the customer recognising one withdrawal is exactly
when the attacker's next one arrives.

**It always expires.** `expires_at` is NOT NULL and the duration is clamped
here rather than in the form, so the table cannot represent a permanent mute
even if every caller is wrong. A UI cap is not a cap — the form is a POST that
can be retyped.

**It silences the phone, not the monitoring.** A snoozed detection still runs,
still writes its `alerts` row, and still appears in the portal. Only the
delivery is withheld, and the row records that it was withheld *on request* —
which is a different fact from `delivered_to = 0`, meaning we tried to tell
someone and reached nobody (#57). Sharing one column would turn every snooze
into a false report of a broken alert channel.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sentinel.db import Database

logger = logging.getLogger("sentinel.snooze")

# The longest any snooze may run. 24 hours because that is the digest cadence:
# no snooze outlives the next summary the customer receives, so a silenced
# alert cannot pass out of view entirely without at least one scheduled moment
# where the account is reported on again.
#
# Whether a CRITICAL should be snoozable for the same length as a LOW is a
# product decision, not an engineering one, and is logged in
# docs/DECISIONS-NEEDED.md rather than guessed at here.
MAX_SNOOZE_HOURS = 24

# Offered in the portal. Kept short deliberately: the useful snooze is "I am on
# the phone to the exchange right now", not "leave me alone until tomorrow".
SNOOZE_CHOICES = (1, 4, MAX_SNOOZE_HOURS)


def snooze_alert(
    db: Database, account_id: int, dedup_key: str, rule: str, hours: float, requested_by: str
):
    """Record a snooze and return its row, or None if `hours` isn't a duration.

    A zero or negative duration is rejected rather than clamped up to a
    minimum. "Snooze for no time" is not a request this product needs to
    honour, and letting it through writes a row that is already expired -
    harmless today, and exactly the sort of already-in-the-past row that reads
    as a mute the moment some comparison is written the other way round."""
    if hours is None or hours <= 0:
        return None
    hours = min(float(hours), MAX_SNOOZE_HOURS)
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(hours=hours)).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO snoozes (account_id, dedup_key, rule, requested_by, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, dedup_key, rule, requested_by, now.isoformat(), expires_at),
        )
        snooze_id = cur.lastrowid
    logger.info(
        "snooze: %s on account %d silenced by %s until %s",
        rule, account_id, requested_by, expires_at,
    )
    with db.cursor() as cur:
        cur.execute("SELECT * FROM snoozes WHERE id = ?", (snooze_id,))
        return cur.fetchone()


def active_snooze(db: Database, account_id: int, dedup_key: str) -> str | None:
    """The expiry of a live snooze on this alert, or None.

    Compared against `now` in the query rather than in Python so that a row
    whose expiry has passed can never be treated as live by a caller that
    forgot to check - there is only one place this comparison exists."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT expires_at FROM snoozes WHERE account_id = ? AND dedup_key = ? "
            "AND expires_at > ? ORDER BY expires_at DESC LIMIT 1",
            (account_id, dedup_key, datetime.now(timezone.utc).isoformat()),
        )
        row = cur.fetchone()
    return row["expires_at"] if row else None


def active_snoozes_for_accounts(db: Database, account_ids: list[int]) -> dict[str, str]:
    """dedup_key -> expiry, for rendering. One query rather than one per row."""
    if not account_ids:
        return {}
    marks = ",".join("?" for _ in account_ids)
    with db.cursor() as cur:
        cur.execute(
            f"SELECT dedup_key, MAX(expires_at) AS expires_at FROM snoozes "
            f"WHERE account_id IN ({marks}) AND expires_at > ? GROUP BY dedup_key",
            [*account_ids, datetime.now(timezone.utc).isoformat()],
        )
        return {r["dedup_key"]: r["expires_at"] for r in cur.fetchall()}
