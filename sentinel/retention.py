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
    # Minas-side triage bookkeeping (#41): one row per alert, so it lives on
    # the alert's own window. `updated_at` rather than the alert's `sent_at`
    # because that is the column this table has, which lets a triage row
    # touched late outlive the alert it annotates - see ALERT_CHILDREN below
    # for why that is a foreign-key failure and not the harmless drift this
    # comment used to claim.
    "alert_triage": ("updated_at", "alerts_days"),
    "held_alerts": ("held_at", "alerts_days"),  # #24: follows the alert it holds
    "drills": ("started_at", "drills_days"),  # #37: the evidence the portal shows
    "contact_deliveries": ("delivered_at", "alerts_days"),  # #37: per-contact delivery record
    # Threshold changes (#16). History of what this product was tuned to
    # notice, on the same long window as `events` - an incident review asks
    # what the thresholds were at the time, not what they are now.
    "config_changes": ("changed_at", "config_changes_days"),
    # Reset tokens live an hour; the RECORD that somebody reset a password at
    # a given moment is forensic and lives as long as `events`. Pruning on
    # `created_at` cannot delete a live token: the shortest expressible window
    # is a day (0 means keep forever) and the longest token life is an hour.
    "password_resets": ("created_at", "password_resets_days"),
    # "That was me" (#28). A customer's judgement about one alert, on the same
    # window as the alert it judges - a mark that outlived its alert would show
    # in the operator's pattern view with nothing behind it, and a mark deleted
    # before its alert would make a rule look better than it was.
    "expected_activity": ("created_at", "expected_activity_days"),
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
    # #87: operator two-factor enrolments and the host break-glass resets,
    # with who did them. An audit row whose entire purpose is to be read
    # later is not something a retention period may remove.
    "operator_events",
    "member_account_grants",  # #61: who may open which account - configuration, not history
    # Deleting an enrolment silently turns 2FA OFF for that person, and
    # deleting a recovery code silently removes their way back in. Both are
    # current state, not history (#32).
    "totp_enrolments",
    "totp_recovery_codes",
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
    # Current state plus forensic history in one table (#18): the newest
    # 'connected' row IS the live channel binding - pruning it silently
    # disconnects someone's alerts - and revoked rows are the record of
    # where alert copies went, kept for the same reason snoozes are.
    # Volume is human-scale: one row per link attempt per member.
    "telegram_links",
    # Configuration, not history (#17): a pruned override silently resets an
    # account's sensitivity to the defaults. One row per (account, setting).
    "account_threshold_overrides",
    # Live countdowns plus their forensic record (#98): a pruned pending row
    # IS a lost pause, and executed/cancelled rows are who-asked-when facts.
    "pause_requests",
})

# Tables holding a row *about* one alert. Every one of them declares
# `alert_id ... REFERENCES alerts(id)`, `Database` opens every connection with
# `PRAGMA foreign_keys = ON`, and no table in this schema declares
# `ON DELETE CASCADE`. So SQLite refuses to delete an alert while any of these
# still points at it, and `DELETE FROM alerts` raises IntegrityError rather
# than skipping the row.
#
# That is not a theoretical ordering nit. `prune_all` walked PRUNABLE in
# insertion order, where `alerts` sits at index 1 and its three children at 3,
# 5 and 8 - so the parent was always deleted first, and the very first alert
# old enough to prune that carried an SMS-delivery record, a "that was me"
# mark or a triage note aborted the whole retention pass. Every table after
# `alerts` then went unpruned too, because the exception left `prune_all`
# entirely: a housekeeping job that silently stops housekeeping, which is the
# unbounded growth #60 exists to prevent.
#
# Fixing it with `ON DELETE CASCADE` would be the tidier schema, but SQLite
# cannot add a constraint to an existing table - it needs the 12-step rebuild,
# on a live database holding customer credentials, and `Database._init_schema`
# only knows how to add columns. So the cascade is written here instead, as an
# explicit delete keyed on the parent's own cutoff, which needs no migration
# and works on the deployed database as it stands.
#
# Each of these keeps its own PRUNABLE entry as well, and that is deliberate:
# this list stops a child outliving its parent, while the PRUNABLE entry lets
# a *shorter* child window still delete a child early. The effective retention
# of a child is min(its own window, the alert's).
ALERT_CHILDREN = ("alert_triage", "expected_activity", "sms_deliveries",
    "held_alerts",  # #24: a hold references the alert it holds
    "drills", "contact_deliveries",  # #37: both reference the alert they record
)


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
        cutoff = _cutoff(days)
        with db.cursor() as cur:
            if table == "alerts":
                # The cascade SQLite will not do for us. Runs inside the same
                # transaction as the parent delete, so a failure here cannot
                # leave children orphaned by a parent that went anyway.
                for child in ALERT_CHILDREN:
                    cur.execute(
                        f"DELETE FROM {child} WHERE alert_id IN "
                        f"(SELECT id FROM alerts WHERE {column} < ?)",
                        (cutoff,),
                    )
                    deleted[child] = deleted.get(child, 0) + cur.rowcount
            cur.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff,))
            deleted[table] = deleted.get(table, 0) + cur.rowcount

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
