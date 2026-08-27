"""#73: a failing retention pass was one log line an hour, at no severity.

Three aggravations made it worse than a missed log line, and each gets its
own pin here: the failing prune reset its own timer (so it failed hourly,
indefinitely); the table-rows gauge was published on the line after the
prune, so the failure froze the one signal that would have shown the
resulting growth; and the untimestamped alarm sat after the prune in the
same try, so it was skipped on every failing cycle. `232528d` removed the
2028 trigger; this removes the silence that would have swallowed the next
cause just as completely.
"""
from __future__ import annotations

import sqlite3

import pytest
from prometheus_client import CollectorRegistry

from sentinel.alerts.operator import OperatorAlerter
from sentinel.config import RulesConfig
from sentinel.engine import Engine
from sentinel.metrics import Metrics


@pytest.fixture
def registry():
    return CollectorRegistry()


@pytest.fixture
def metrics(registry):
    return Metrics(registry=registry)


def _engine(db, cipher, metrics=None, alerter=None):
    return Engine(db, cipher, RulesConfig("config/rules.yaml"), {},
                  metrics=metrics, operator_alerter=alerter)


def _pages(db):
    with db.cursor() as cur:
        cur.execute("SELECT rule, severity FROM operator_pages ORDER BY id")
        return [(r["rule"], r["severity"]) for r in cur.fetchall()]


def _boom(db_, rules_):
    raise sqlite3.OperationalError("no such table: pretend")


def test_a_failing_prune_pages_the_operator(db, cipher, monkeypatch):
    monkeypatch.setattr("sentinel.engine.prune_all", _boom)
    alerter = OperatorAlerter(db, {}, RulesConfig("config/rules.yaml"))
    engine = _engine(db, cipher, alerter=alerter)

    engine._scheduler_tick()

    assert ("retention_prune_failed", "HIGH") in _pages(db)


def test_a_successful_prune_does_not_page(db, cipher):
    alerter = OperatorAlerter(db, {}, RulesConfig("config/rules.yaml"))
    engine = _engine(db, cipher, alerter=alerter)

    engine._scheduler_tick()

    # The tick also runs #37's drill schedule, which pages "no drill account"
    # in a fixture with none - a different alarm, and not this test's subject.
    assert [p for p in _pages(db) if p[0] != "drill_no_account"] == []


def test_a_failing_prune_still_publishes_table_counts(db, cipher, metrics, registry, monkeypatch):
    """The gauge that reveals unbounded growth must not be frozen by the
    thing causing the growth — that inversion was #73's sharpest edge."""
    monkeypatch.setattr("sentinel.engine.prune_all", _boom)
    engine = _engine(db, cipher, metrics=metrics)

    engine._scheduler_tick()

    assert registry.get_sample_value("sentinel_table_rows", {"table": "events"}) == 0.0


def test_a_failing_prune_does_not_skip_the_untimestamped_alarm(db, cipher, monkeypatch):
    monkeypatch.setattr("sentinel.engine.prune_all", _boom)
    engine = _engine(db, cipher)
    calls = []
    engine._untimestamped_alarm = type("Alarm", (), {"evaluate": lambda self, now: calls.append(now)})()

    engine._scheduler_tick()

    assert calls, "the alarm after the prune must run on a failing cycle"


def test_the_retention_gauge_drops_on_failure_and_recovers(db, cipher, metrics, registry, monkeypatch):
    engine = _engine(db, cipher, metrics=metrics)

    assert registry.get_sample_value("sentinel_retention_ok") == 1.0  # resting value

    monkeypatch.setattr("sentinel.engine.prune_all", _boom)
    engine._scheduler_tick()
    assert registry.get_sample_value("sentinel_retention_ok") == 0.0

    monkeypatch.setattr("sentinel.engine.prune_all", lambda db_, rules_: {})
    engine._last_prune = None  # make the prune due again
    engine._scheduler_tick()
    assert registry.get_sample_value("sentinel_retention_ok") == 1.0


def test_a_failing_prune_retries_on_its_interval_not_every_tick(db, cipher, monkeypatch):
    """The timer advancing on failure is deliberate and stays: retrying a
    broken prune on the 45-second loop would hammer the tables the fast loop
    reads. What #73 removes is the silence, not the pacing."""
    calls = {"n": 0}

    def counting_boom(db_, rules_):
        calls["n"] += 1
        raise sqlite3.OperationalError("still broken")

    monkeypatch.setattr("sentinel.engine.prune_all", counting_boom)
    engine = _engine(db, cipher)

    for _ in range(5):
        engine._scheduler_tick()

    assert calls["n"] == 1


def test_a_persistent_failure_does_not_page_every_interval(db, cipher, monkeypatch):
    """The operator alerter's cooldown owns the re-page cadence; the engine
    must not bypass it by inventing a fresh dedup scope per failure."""
    monkeypatch.setattr("sentinel.engine.prune_all", _boom)
    alerter = OperatorAlerter(db, {}, RulesConfig("config/rules.yaml"))
    engine = _engine(db, cipher, alerter=alerter)

    engine._scheduler_tick()
    engine._last_prune = None
    engine._scheduler_tick()

    assert _pages(db).count(("retention_prune_failed", "HIGH")) == 1
