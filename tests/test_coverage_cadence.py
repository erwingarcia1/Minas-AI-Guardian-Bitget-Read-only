"""The repeat cadence belongs to the coverage CLASS (2026-09-22).

On 2026-09-21 nine keys were refused by address in one minute. account_unreachable
repeated daily as designed; monitoring_key_tamper_suspected - the same class,
never given the cadence because it was applied per rule - repeated every 62
minutes and emailed the customer every time: 145 repeats in sixteen hours.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sentinel.alerts.dispatch import dispatch
from sentinel.alerts.meaning import COVERAGE_RULES, REPEAT_CADENCE_KEY, coverage_repeat_cooldowns
from sentinel.config import RulesConfig
from sentinel.detectors.base import Detection
from tests.conftest import make_account

COOLDOWNS = {"CRITICAL": 15, "HIGH": 30, "MEDIUM": 720, "LOW": 1440}


def test_every_coverage_rule_has_the_daily_cadence_enumerated_not_listed():
    rules = RulesConfig("config/rules.yaml")
    cadence = coverage_repeat_cooldowns(rules)
    assert len(COVERAGE_RULES) >= 2, "the class must not have quietly shrunk"
    daily = rules.get_int(*REPEAT_CADENCE_KEY)
    assert daily >= 24 * 60, "the reminder cadence is daily, not a severity cooldown"
    for rule in COVERAGE_RULES:
        assert cadence.get(rule) == daily, f"{rule} is a coverage rule without the class cadence"
    assert set(cadence) == set(COVERAGE_RULES), "the cadence map names exactly the class"
    assert all(cadence[r] > max(COOLDOWNS.values()) or cadence[r] >= daily for r in cadence)


def _fire(db, account_id, rule, when):
    det = Detection(rule=rule, severity="HIGH", confidence="high", message="Your account is NOT being watched.",
                    evidence={"consecutive_auth_failures": 63})
    import sentinel.alerts.dispatch as d

    real = d.datetime
    class _Clock(real):
        @classmethod
        def now(cls, tz=None):
            return when if tz is None else when.astimezone(tz)
    d.datetime = _Clock
    try:
        return dispatch(db, {"HIGH": [], "CRITICAL": []}, account_id, "acct", det, COOLDOWNS,
                        rule_cooldowns=coverage_repeat_cooldowns(RulesConfig("config/rules.yaml")))
    finally:
        d.datetime = real


def test_the_tamper_rule_no_longer_repeats_hourly(db):
    """The 2026-09-21 shape on one account: the first alert, then the same
    condition every hour. Inside the daily cadence nothing is re-sent; after
    it, one quiet repeat pointing at the first."""
    account_id = make_account(db)
    t0 = datetime(2026, 9, 21, 19, 10, tzinfo=timezone.utc)
    first = _fire(db, account_id, "monitoring_key_tamper_suspected", t0)
    assert first is not None
    for hour in range(1, 24):
        assert _fire(db, account_id, "monitoring_key_tamper_suspected", t0 + timedelta(minutes=62 * hour)) is None, \
            f"re-fired at +{62 * hour} min"
    later = _fire(db, account_id, "monitoring_key_tamper_suspected", t0 + timedelta(minutes=1441))
    assert later is not None
    with db.cursor() as cur:
        cur.execute("SELECT repeat_of, repeat_count FROM alerts WHERE id = ?", (later,))
        row = cur.fetchone()
        assert row["repeat_of"] == first
        cur.execute("SELECT repeat_count FROM alerts WHERE id = ?", (first,))
        assert cur.fetchone()["repeat_count"] == 23, "every suppressed re-fire is counted on the first alert"


def test_the_sibling_behaves_identically(db):
    account_id = make_account(db)
    t0 = datetime(2026, 9, 21, 19, 10, tzinfo=timezone.utc)
    assert _fire(db, account_id, "account_unreachable", t0) is not None
    assert _fire(db, account_id, "account_unreachable", t0 + timedelta(minutes=62)) is None
    assert _fire(db, account_id, "account_unreachable", t0 + timedelta(minutes=1441)) is not None
