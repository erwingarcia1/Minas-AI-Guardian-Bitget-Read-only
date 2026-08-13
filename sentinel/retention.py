"""Bounding the tables that grow on every poll (#60).

`prune_balance_snapshots` was the only retention anywhere. Everything else grew
forever, most of it at the fast loop's 45-second cadence — and this project has
already had one outage from a query going quadratic over a growing table (see
the 2026-07-06 note in `baseline.py`).

Deleting old rows is the easy part. The two things that make this dangerous:

**Some tables must never be pruned at all.** `known_addresses`, `traded_pairs`
and `transfer_routes` are the account's *baseline*, not its history. Deleting an
old row there doesn't reclaim space, it manufactures a false positive: the next
withdrawal to a five-year-old address becomes "never seen before" and the
customer gets a CRITICAL about their own cold wallet.

**Some rows must survive inside the tables that are pruned.** A snapshot table's
newest row per account is the comparison baseline —
`detect_permission_state_change` returns nothing when there is no previous
snapshot, so deleting the last one silently resets detection rather than
disabling it visibly.

And the dedup tables (`seen_*`) are not history either: they are the answer to
"have I already alerted on this". Pruning one while the exchange still returns
that record re-alerts on a months-old movement, so **their retention is bounded
below by correctness, not by policy** — it must exceed the longest history any
exchange returns on a `since=None` fetch, a number we don't control. That floor
is an engineering constraint the legal retention decision (#47) cannot shorten.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sentinel.db import Database

logger = logging.getLogger("sentinel.retention")

# table -> (timestamp column, retention setting under `retention:`)
PRUNABLE: dict[str, tuple[str, str]] = {
    "events": ("created_at", "events_days"),
    "alerts": ("sent_at", "alerts_days"),
    "operator_pages": ("sent_at", "operator_pages_days"),
    "sms_deliveries": ("created_at", "sms_deliveries_days"),
    # Who silenced which alarm and until when (#30). Forensic, like `events`:
    # the silence lasts at most a day, the record of who asked for it lasts as
    # long as the alert it silenced. Keyed on `created_at`, which is safe
    # because no retention shorter than the 24h snooze cap is expressible -
    # `days <= 0` means keep forever, so the shortest real window is a day.
    "snoozes": ("created_at", "snoozes_days"),
    # Threshold changes (#16). History of what this product was tuned to
    # notice, on the same long window as `events` - an incident review asks
    # what the thresholds were at the time, not what they are now.
    "config_changes": ("changed_at", "config_changes_days"),
    # Dedup state, not history. See the module docstring: too short and old
    # movements re-alert.
    "seen_withdrawals": ("first_seen_at", "dedup_days"),
    "seen_transfers": ("first_seen_at", "dedup_days"),
    "seen_fills": ("first_seen_at", "dedup_days"),
}

# Same, but the newest snapshot per account is the comparison baseline and is
# kept regardless of age. Losing it doesn't disable detection loudly - it makes
# the next comparison find nothing to compare against.
KEEP_LATEST_PER_ACCOUNT: dict[str, tuple[str, str]] = {
    "balance_snapshots": ("snapshot_at", "balance_snapshots_days"),
    "permission_snapshots": ("snapshot_at", "permission_snapshots_days"),
}

# Deliberately never pruned. Baseline tables (deleting a row invents an anomaly),
# identity and configuration (deleting a row removes a person or a setting), and
# state tables that hold exactly one row per account.
NEVER_PRUNE = frozenset({
    "accounts",
    "poll_state",
    "known_addresses",     # a pruned address is a "never seen before" CRITICAL
    "traded_pairs",        # a pruned pair is a "never touched pair" HIGH
    "transfer_routes",     # a pruned route is a "route never seen" HIGH
    "fee_baseline",        # one row per account; pruning resets the EMA
    "operators",           # offboarding is `disable`, never deletion (G-50)
    "operator_meta",
    "portal_members",
    "portal_meta",
})


def _cutoff(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def prune_all(db: Database, rules) -> dict[str, int]:
    """Deletes rows past their retention. Returns rows deleted per table.

    Every period comes from config so the legal decision (#47) sets numbers
    rather than requiring a code change."""
    deleted: dict[str, int] = {}

    for table, (column, setting) in PRUNABLE.items():
        days = rules.get_int("retention", setting)
        if days <= 0:
            continue  # 0 means "keep forever", a legitimate choice
        with db.cursor() as cur:
            cur.execute(f"DELETE FROM {table} WHERE {column} < ?", (_cutoff(days),))
            deleted[table] = cur.rowcount

    for table, (column, setting) in KEEP_LATEST_PER_ACCOUNT.items():
        days = rules.get_int("retention", setting)
        if days <= 0:
            continue
        with db.cursor() as cur:
            # Keyed on (account_id, timestamp) rather than on the newest row id:
            # a balance snapshot is many rows sharing one timestamp, one per
            # coin, so keeping "the newest row" would keep a single coin and
            # silently shrink the baseline to it.
            cur.execute(
                f"DELETE FROM {table} WHERE {column} < ? "
                f"AND (account_id, {column}) NOT IN "
                f"(SELECT account_id, MAX({column}) FROM {table} GROUP BY account_id)",
                (_cutoff(days),),
            )
            deleted[table] = cur.rowcount

    total = sum(deleted.values())
    if total:
        logger.info("retention: deleted %d rows (%s)", total,
                    ", ".join(f"{t}={n}" for t, n in deleted.items() if n))
    return deleted


def table_counts(db: Database) -> dict[str, int]:
    """Row counts for every table with a retention decision, so growth is
    observable before it is a problem rather than after."""
    counts: dict[str, int] = {}
    for table in list(PRUNABLE) + list(KEEP_LATEST_PER_ACCOUNT):
        with db.cursor() as cur:
            cur.execute(f"SELECT count(*) c FROM {table}")
            counts[table] = cur.fetchone()["c"]
    return counts
