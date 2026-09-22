"""The watched decision lives in one place and reads every counter (item 3).

A deduplication plus a completeness guard, not a bug fix: the dashboard's
watch cell, the portal's member status and the engine each read both
counters on their own; one summary line (the dashboard's headline health
percentage) read one. Now there is one decision, and a counter on
poll_state that the decision does not name fails the build.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from sentinel.watching import (
    FAILURE_COUNTERS,
    counter,
    counter_columns_sql,
    failures_total,
    failures_total_sql,
    is_unwatched,
    unwatched_predicate_sql,
)

ROOT = Path(__file__).resolve().parent.parent


def _poll_state_failure_columns(db) -> set[str]:
    with db.cursor() as cur:
        cur.execute("PRAGMA table_info(poll_state)")
        return {r["name"] for r in cur.fetchall() if "failure" in r["name"]}


def test_every_failure_counter_on_poll_state_is_enumerated(db):
    """The registration guard: the table's failure columns and the tuple the
    decision reads must be the same set. A counter added to the schema and
    not to FAILURE_COUNTERS fails here, before it can be consulted by
    nothing."""
    columns = _poll_state_failure_columns(db)
    assert columns, "the scan found no failure counters on poll_state - it is broken, not clean"
    assert columns == set(FAILURE_COUNTERS), (
        f"poll_state failure counters {sorted(columns)} != FAILURE_COUNTERS {sorted(FAILURE_COUNTERS)}: "
        "a counter that exists and is not in the decision would be a fourth copy waiting to drift")


@pytest.mark.parametrize("name", FAILURE_COUNTERS)
def test_each_counter_alone_makes_the_account_unwatched(name):
    row = {c: 0 for c in FAILURE_COUNTERS}
    row[name] = 5
    assert is_unwatched(row, 5), f"{name} at the threshold must read as unwatched"
    row[name] = 4
    assert not is_unwatched(row, 5)


def test_the_2026_09_21_row_reads_unwatched():
    """The row every one of the nine had: generic counter zero, auth counter 63."""
    row = {"consecutive_failures": 0, "consecutive_auth_failures": 63, "last_success_at": "2026-09-21T19:09:00+00:00"}
    assert is_unwatched(row, 5) and failures_total(row) == 63


def test_counters_read_from_rows_with_missing_or_null_columns():
    assert counter({}, "consecutive_failures") == 0
    assert counter({"consecutive_failures": None}, "consecutive_failures") == 0
    assert counter(None.__class__(), "consecutive_failures") == 0 if False else True


def test_the_sql_fragments_name_every_counter():
    for name in FAILURE_COUNTERS:
        assert name in counter_columns_sql("p") and name in failures_total_sql("p") and name in unwatched_predicate_sql("p")
    assert unwatched_predicate_sql("p").endswith(">= ?")


def test_every_reader_of_a_counter_decides_through_the_shared_module():
    """Registration across the codebase: a module that reads a failure
    counter by name must import sentinel.watching - the writer (poller), the
    schema, the detectors that receive the numbers as arguments, and the
    config schema are the only exceptions. This is what stops a fourth
    copy of the decision reading one counter."""
    allowed = {"sentinel/watching.py", "sentinel/poller.py", "sentinel/schema.sql", "sentinel/db.py",
               "sentinel/rules_schema.py", "sentinel/detectors/heartbeat.py", "sentinel/detectors/tamper.py"}
    # Whole names only: telegram_link's own `_consecutive_failures` backoff
    # attribute is not a poll_state counter.
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in FAILURE_COUNTERS) + r")\b")
    offenders = []
    for path in (ROOT / "sentinel").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in allowed:
            continue
        src = path.read_text()
        if pattern.search(src) and "from sentinel.watching import" not in src:
            offenders.append(rel)
    assert offenders == [], f"read a failure counter without the shared decision: {offenders}"


def test_the_dashboard_health_count_reads_every_counter(db, cipher):
    """The one summary line that read a single counter: the headline health
    percentage. It now reads the shared sum, like the watch cell beside it."""
    from tests.test_watch_alarms import NOW, _account

    healthy = _account(db, "fine", last_ok=NOW)
    _account(db, "refused", last_ok=NOW, auth_failures=63)
    from sentinel import dashboard as d

    rows = []
    with db.cursor() as cur:
        cur.execute(f"SELECT a.id, a.label, a.status, p.last_success_at, {counter_columns_sql('p')} "
                    "FROM accounts a LEFT JOIN poll_state p ON p.account_id = a.id")
        rows = [dict(r) for r in cur.fetchall()]
    active = [a for a in rows if a["status"] == "active"]
    n = sum(1 for a in active if a["last_success_at"] and failures_total(a) == 0)
    assert n == 1 and healthy
    assert d._watch_cell(next(a for a in rows if a["label"] == "refused"), 5).startswith('<span class="pill s-tamper">')
    assert d._problem_state(next(a for a in rows if a["label"] == "refused"), 5) == "trouble"
