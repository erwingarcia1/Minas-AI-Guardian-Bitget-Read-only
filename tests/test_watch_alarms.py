"""#42's two remaining criteria: alarms that fire when checks fall behind
schedule, and when one account has been unwatchable for too long - pages
to Minas, shaped so a wedged engine is one page, not one per account.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sentinel.alerts.watch_alarms import WatchAlarms

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


class _Recorder:
    def __init__(self):
        self.pages = []

    def page(self, **kw):
        self.pages.append(kw)
        return 1


@pytest.fixture
def alerter():
    return _Recorder()


def _account(db, label, *, last_ok, status="active", onboarded=None,
             failures=0, auth_failures=0):
    onboarded = (onboarded or NOW - timedelta(days=30)).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, ?, ?)",
            (label, status, onboarded, onboarded))
        account_id = cur.lastrowid
        cur.execute(
            "INSERT INTO poll_state (account_id, last_success_at, consecutive_failures, "
            "consecutive_auth_failures) VALUES (?, ?, ?, ?)",
            (account_id, last_ok.isoformat() if last_ok else None, failures, auth_failures))
        return account_id


def test_fresh_accounts_raise_nothing(db, alerter):
    _account(db, "a1", last_ok=NOW - timedelta(seconds=30))
    WatchAlarms(db, None, alerter).evaluate(now=NOW)
    assert alerter.pages == []


def test_everything_behind_is_one_page_not_one_per_account(db, alerter):
    """The engine as a whole falling behind is one fact. Eight pages saying
    it eight ways is how an operator learns to skim."""
    for i in range(4):
        _account(db, f"a{i}", last_ok=NOW - timedelta(minutes=20))
    WatchAlarms(db, None, alerter).evaluate(now=NOW)

    assert len(alerter.pages) == 1
    page = alerter.pages[0]
    assert page["rule"] == "checks_behind_schedule"
    assert page["severity"] == "HIGH"
    assert "engine as a whole" in page["message"]
    assert "all 4 active account(s)" in page["message"]
    assert page["dedup_scope"] == "checks-behind"


def test_one_dark_account_among_working_ones_is_named(db, alerter):
    """The sharper case: others check normally, so the fault is THIS
    account's - the page says so, with the counters that say why."""
    _account(db, "healthy", last_ok=NOW - timedelta(seconds=30))
    dark_id = _account(db, "dark", last_ok=NOW - timedelta(hours=2),
                       failures=88, auth_failures=3)
    WatchAlarms(db, None, alerter).evaluate(now=NOW)

    assert len(alerter.pages) == 1
    page = alerter.pages[0]
    assert page["rule"] == "account_unwatchable_too_long"
    assert page["account_id"] == dark_id
    assert "dark has not completed a check" in page["message"]
    assert "other accounts are checking normally" in page["message"]
    assert "88 consecutive" in page["message"]
    assert page["dedup_scope"] == "unwatchable:dark"


def test_a_never_checked_account_ages_from_onboarding(db, alerter):
    _account(db, "healthy", last_ok=NOW - timedelta(seconds=30))
    _account(db, "never", last_ok=None, onboarded=NOW - timedelta(hours=3))
    WatchAlarms(db, None, alerter).evaluate(now=NOW)
    assert [p["rule"] for p in alerter.pages] == ["account_unwatchable_too_long"]


def test_a_freshly_onboarded_account_is_not_behind(db, alerter):
    _account(db, "healthy", last_ok=NOW - timedelta(seconds=30))
    _account(db, "brand-new", last_ok=None, onboarded=NOW - timedelta(minutes=2))
    WatchAlarms(db, None, alerter).evaluate(now=NOW)
    assert alerter.pages == []


def test_disabled_accounts_are_not_paged_about(db, alerter):
    """An operator chose that stillness; the portal and digest already show
    it. Paging Minas about a pause Minas made is noise."""
    _account(db, "healthy", last_ok=NOW - timedelta(seconds=30))
    _account(db, "paused", last_ok=NOW - timedelta(days=5), status="disabled")
    WatchAlarms(db, None, alerter).evaluate(now=NOW)
    assert alerter.pages == []


def test_the_scheduler_actually_evaluates_the_alarms(db, cipher):
    """The #52 standard: an alarm that exists but is never called is the
    same as no alarm. Behaviour is covered above; this pins the wiring."""
    from prometheus_client import CollectorRegistry

    from sentinel.alerts.operator import OperatorAlerter
    from sentinel.config import RulesConfig
    from sentinel.engine import Engine
    from sentinel.metrics import Metrics

    class Spy:
        def __init__(self):
            self.calls = 0

        def evaluate(self, now=None):
            self.calls += 1

    engine = Engine(
        db, cipher, RulesConfig("config/rules.yaml"), {},
        metrics=Metrics(registry=CollectorRegistry()),
        operator_alerter=OperatorAlerter(db, {}, RulesConfig("config/rules.yaml")),
    )
    spy = Spy()
    engine._watch_alarms = spy
    engine._scheduler_tick()
    assert spy.calls == 1, "the scheduler is not evaluating the watch alarms"


def test_no_alerter_means_no_construction_not_a_crash(db, cipher):
    from prometheus_client import CollectorRegistry

    from sentinel.config import RulesConfig
    from sentinel.engine import Engine
    from sentinel.metrics import Metrics

    engine = Engine(db, cipher, RulesConfig("config/rules.yaml"), {},
                    metrics=Metrics(registry=CollectorRegistry()))
    assert engine._watch_alarms is None
    engine._scheduler_tick()  # must not raise


# --- #104 batch C: the alarm's decisions, pinned -------------------------------

def _alarms(db, alerter):
    from sentinel.config import RulesConfig
    from tests.test_operator_alerting import rules_yaml

    return WatchAlarms(db, RulesConfig(rules_yaml()), alerter)


def test_the_fleet_is_behind_exactly_at_the_window_not_one_second_after(db, alerter):
    """`freshest >= behind_after`: the freshest success ageing INTO the
    window is the page, not one second past it."""
    alarms = _alarms(db, alerter)
    behind_after = alarms._threshold("checks_behind_seconds", 600)
    _account(db, "only", last_ok=NOW - timedelta(seconds=behind_after))
    alarms.evaluate(now=NOW)
    assert [p["rule"] for p in alerter.pages] == ["checks_behind_schedule"]


def test_an_account_is_unwatchable_exactly_at_the_window(db, alerter):
    """`age < unwatchable_after` is fine; AT the window it is paged."""
    alarms = _alarms(db, alerter)
    unwatchable_after = alarms._threshold("unwatchable_seconds", 1800)
    _account(db, "healthy", last_ok=NOW - timedelta(seconds=30))
    _account(db, "edge", last_ok=NOW - timedelta(seconds=unwatchable_after))
    alarms.evaluate(now=NOW)
    assert [(p["rule"], p["evidence"]["account_label"]) for p in alerter.pages] == [("account_unwatchable_too_long", "edge")]


def test_a_healthy_account_ahead_of_a_dark_one_does_not_end_the_sweep(db, alerter):
    """`continue`, not `break`: two healthy rows come first and the dark
    third is still paged."""
    _account(db, "healthy-1", last_ok=NOW - timedelta(seconds=30))
    _account(db, "healthy-2", last_ok=NOW - timedelta(seconds=40))
    _account(db, "dark", last_ok=NOW - timedelta(hours=3), failures=9)
    _alarms(db, alerter).evaluate(now=NOW)
    assert [p["evidence"]["account_label"] for p in alerter.pages] == ["dark"]


def test_a_malformed_last_success_falls_back_to_onboarding_not_to_unknown(db, alerter):
    """`_age` tries the fallback after a malformed candidate (`continue`);
    a `break` would age the account as unknown and never page it."""
    _account(db, "healthy", last_ok=NOW - timedelta(seconds=30))
    broken = _account(db, "broken-stamp", last_ok=None, onboarded=NOW - timedelta(hours=2))
    with db.cursor() as cur:
        cur.execute("UPDATE poll_state SET last_success_at = 'not-a-timestamp' WHERE account_id = ?", (broken,))
    _alarms(db, alerter).evaluate(now=NOW)
    assert [p["evidence"]["account_label"] for p in alerter.pages] == ["broken-stamp"]
