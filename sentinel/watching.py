"""Is this account being watched? ONE answer, read from EVERY failure counter.

poll_state carries two counters: `consecutive_failures` (the exchange did not
answer) and `consecutive_auth_failures` (the exchange refused our key). An
authentication failure is a failure - the account is exactly as unwatched.

Until 2026-09-22 the decision was written in three places - the dashboard's
watch cell and problem state, the portal's member status, the engine's
failed-poll check - each reading both counters, each on its own. One
summary line, the dashboard's headline health percentage, read one. The
decision now lives here, once, and enumerates the counters; a test reads
poll_state's actual columns and fails if a failure counter exists that this
tuple does not name, so a counter that exists and is consulted by nothing
is a build failure rather than a fourth copy waiting to drift.

(The 2026-09-21 incident is not why this exists: the product's per-account
decisions read both counters throughout. An AUDIT SCRIPT read one, and
reported nine dark accounts as healthy - the lesson about that instrument
is in CONTRIBUTING.)
"""
from __future__ import annotations

# Every consecutive-failure counter on poll_state. A new counter is added
# HERE (and its writer in the poller); the registration test compares this
# tuple to the table.
FAILURE_COUNTERS: tuple[str, ...] = ("consecutive_failures", "consecutive_auth_failures")


def counter(row, name: str) -> int:
    """One counter's value from any row-like (sqlite3.Row, dict, or a row
    without the column at all - a fixture, an account with no poll_state row)."""
    try:
        keys = row.keys() if hasattr(row, "keys") else ()
        value = row[name] if name in keys else None
    except (KeyError, IndexError, TypeError):
        value = None
    return int(value or 0)


def failures_total(row) -> int:
    """Every counter, summed: the number the unwatched decision reads."""
    return sum(counter(row, name) for name in FAILURE_COUNTERS)


def is_unwatched(row, threshold: int) -> bool:
    """The account's checks have failed `threshold` times in a row, by ANY
    counter. The same judgement the heartbeat detector fires on, so a page
    and the alert cannot disagree."""
    return failures_total(row) >= int(threshold)


def counter_columns_sql(alias: str = "p") -> str:
    """The counters as a SELECT fragment: `p.consecutive_failures, p.consecutive_auth_failures`."""
    return ", ".join(f"{alias}.{name}" for name in FAILURE_COUNTERS)


def failures_total_sql(alias: str = "p") -> str:
    """The sum as a SQL expression, NULL-safe."""
    return " + ".join(f"COALESCE({alias}.{name}, 0)" for name in FAILURE_COUNTERS)


def unwatched_predicate_sql(alias: str = "p") -> str:
    """`(sum of every counter) >= ?` - bind the threshold once."""
    return f"({failures_total_sql(alias)}) >= ?"
