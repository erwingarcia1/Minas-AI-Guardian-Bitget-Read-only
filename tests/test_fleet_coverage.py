"""A fleet-wide coverage failure is ONE event (item 2, 2026-09-22).

The exact 2026-09-21 shape: nine accounts refused by address inside one
minute. Before: 144 per-account pages and 145 customer repeats in sixteen
hours. After: one page naming the nine, nine first notices, and silence
until their checks succeed again.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sentinel.alerts.dispatch import dispatch
from sentinel.alerts.fleet import FLEET_RULE, fleet_incident, in_fleet_incident, page_fleet_incident
from sentinel.alerts.watch_alarms import WatchAlarms
from sentinel.config import RulesConfig
from sentinel.detectors.base import Detection
from sentinel.poller import AccountPoller
from tests.test_watch_alarms import _account, _Recorder

T0 = datetime(2026, 9, 21, 19, 10, tzinfo=timezone.utc)
COOLDOWNS = {"CRITICAL": 15, "HIGH": 30, "MEDIUM": 720, "LOW": 1440}


def _unreachable():
    return Detection(rule="account_unreachable", severity="HIGH", confidence="high",
                     message="Your account is NOT being watched.", evidence={"auth_failures": 63})


def _first_notice(db, account_id, when):
    """The account's first coverage alert, stamped at `when`: the notice the
    customer receives exactly as before."""
    alert_id = dispatch(db, {"CRITICAL": [], "HIGH": []}, account_id, f"acct-{account_id}", _unreachable(), COOLDOWNS)
    with db.cursor() as cur:
        cur.execute("UPDATE alerts SET sent_at = ? WHERE id = ?", (when.isoformat(), alert_id))
    return alert_id


def _nine(db, spread_seconds=50):
    ids = []
    for i in range(9):
        account_id = _account(db, f"acct-{i}", last_ok=T0 - timedelta(minutes=1), auth_failures=63)
        _first_notice(db, account_id, T0 + timedelta(seconds=i * spread_seconds / 8))
        ids.append(account_id)
    return ids


def _pages(alerter, rule=None):
    return [p for p in alerter.pages if rule is None or p["rule"] == rule]


def test_the_2026_09_21_shape_is_one_page_and_nine_notices_not_144_and_145(db):
    ids = _nine(db)
    rules = RulesConfig("config/rules.yaml")
    alerter = _Recorder()
    alarms = WatchAlarms(db, rules, alerter)
    # The rest of the fleet keeps checking normally (five accounts did on
    # 2026-09-21), so this is a per-account fact, not the engine behind.
    fine = _account(db, "fine", last_ok=T0)
    # Sixteen hourly evaluations, as the scheduler would make them.
    for hour in range(1, 17):
        now = T0 + timedelta(hours=hour)
        with db.cursor() as cur:
            cur.execute("UPDATE poll_state SET last_success_at = ? WHERE account_id = ?", (now.isoformat(), fine))
        alarms.evaluate(now=now)
    fleet = _pages(alerter, FLEET_RULE)
    assert len(fleet) == 1, f"one page for the fleet, got {len(fleet)}"
    assert fleet[0]["evidence"]["count"] == 9 and all(f"acct-{i}" in fleet[0]["message"] for i in range(9))
    assert "9 accounts" in fleet[0]["message"]
    assert _pages(alerter, "account_unwatchable_too_long") == [], "no per-account page for a member of the fleet"
    # The nine first notices exist and are the only coverage alerts.
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM alerts WHERE rule = 'account_unreachable'")
        assert cur.fetchone()["n"] == 9
    # A repeat for any member, at every hour of the day, is held.
    for account_id in ids:
        assert in_fleet_incident(db, rules, account_id)


def test_a_members_daily_repeat_is_held_by_the_poller_and_a_strangers_is_not(db, cipher):
    ids = _nine(db)
    rules = RulesConfig("config/rules.yaml")
    stranger = _account(db, "lonely", last_ok=T0 - timedelta(hours=30), auth_failures=63)
    _first_notice(db, stranger, T0 - timedelta(hours=30))
    poller = AccountPoller(db, cipher, rules, {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []})
    before = _count(db)
    poller._emit(ids[0], "acct-0", _unreachable())
    assert _count(db) == before, "a member's repeat is held"
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM events WHERE account_id = ? AND event_type = 'account_unreachable'", (ids[0],))
        assert cur.fetchone()["n"] >= 1, "the event is still recorded for forensics"
    poller._emit(stranger, "lonely", _unreachable())
    assert _count(db) == before + 1, "an account outside the incident repeats on its own cadence"


def _count(db):
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM alerts")
        return cur.fetchone()["n"]


def test_below_the_minimum_or_outside_the_window_is_not_a_fleet_event(db):
    rules = RulesConfig("config/rules.yaml")
    a = _account(db, "one", last_ok=T0, auth_failures=63)
    _first_notice(db, a, T0)
    b = _account(db, "two", last_ok=T0, auth_failures=63)
    _first_notice(db, b, T0 + timedelta(seconds=30))
    assert fleet_incident(db, rules) is None, "two accounts are two facts"
    c = _account(db, "three", last_ok=T0, auth_failures=63)
    _first_notice(db, c, T0 + timedelta(hours=3))
    assert fleet_incident(db, rules) is None, "three accounts hours apart are not one event"
    d = _account(db, "four", last_ok=T0, auth_failures=63)
    _first_notice(db, d, T0 + timedelta(seconds=45))
    incident = fleet_incident(db, rules)
    assert incident and sorted(incident["labels"]) == ["four", "one", "two"], "the cluster inside the window, not the straggler"


def test_the_first_notice_is_never_held_and_recovery_ends_the_incident(db, cipher):
    ids = _nine(db)
    rules = RulesConfig("config/rules.yaml")
    # A tenth account that has NOT been told yet is not a member: its first notice goes out.
    tenth = _account(db, "tenth", last_ok=T0, auth_failures=63)
    assert not in_fleet_incident(db, rules, tenth)
    poller = AccountPoller(db, cipher, rules, {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []})
    before = _count(db)
    poller._emit(tenth, "tenth", _unreachable())
    assert _count(db) == before + 1
    # Recovery: counters reset on a successful poll -> no longer a member.
    with db.cursor() as cur:
        cur.execute("UPDATE poll_state SET consecutive_auth_failures = 0, consecutive_failures = 0 WHERE account_id = ?", (ids[0],))
    assert not in_fleet_incident(db, rules, ids[0])
    assert in_fleet_incident(db, rules, ids[1])


def test_the_page_is_sent_once_whatever_the_pager_cooldown_says(db):
    _nine(db)
    rules = RulesConfig("config/rules.yaml")
    alerter = _Recorder()
    incident = fleet_incident(db, rules)
    assert page_fleet_incident(db, alerter, incident) is not None
    assert page_fleet_incident(db, alerter, incident) is None
    assert len(alerter.pages) == 1


def test_an_auth_failure_counts_as_a_failure_for_membership(db):
    """The counters are two; membership reads both (item 3's lesson)."""
    rules = RulesConfig("config/rules.yaml")
    for i, kw in enumerate(({"auth_failures": 5}, {"failures": 5}, {"auth_failures": 5})):
        a = _account(db, f"m{i}", last_ok=T0, **kw)
        _first_notice(db, a, T0 + timedelta(seconds=i))
    incident = fleet_incident(db, rules)
    assert incident and incident["count"] == 3


def test_a_start_exactly_at_the_window_edge_is_inside_it(db):
    """Three accounts: t0, t0+300 s, t0+600 s with a 600 s window - the last
    one is a member (the edge is closed)."""
    rules = RulesConfig("config/rules.yaml")
    window = int(rules.get_seconds("operator_alerting", "fleet_coverage_window_seconds"))
    for i, offset in enumerate((0, window // 2, window)):
        a = _account(db, f"edge-{i}", last_ok=T0, auth_failures=63)
        _first_notice(db, a, T0 + timedelta(seconds=offset))
    incident = fleet_incident(db, rules)
    assert incident and incident["count"] == 3 and "edge-2" in incident["labels"]


def test_equal_clusters_resolve_to_the_earliest(db):
    """Two clusters of three, hours apart: the incident is the FIRST one, so
    its key - and therefore the one page - does not move to a later group."""
    rules = RulesConfig("config/rules.yaml")
    for i in range(3):
        a = _account(db, f"early-{i}", last_ok=T0, auth_failures=63)
        _first_notice(db, a, T0 + timedelta(seconds=i))
    for i in range(3):
        a = _account(db, f"late-{i}", last_ok=T0, auth_failures=63)
        _first_notice(db, a, T0 + timedelta(hours=5, seconds=i))
    incident = fleet_incident(db, rules)
    assert incident and sorted(incident["labels"]) == ["early-0", "early-1", "early-2"]
    assert incident["started_at"].startswith(T0.isoformat()[:16])


def test_an_unparsable_start_is_skipped_not_the_end_of_the_list(db):
    """A row whose coverage alert carries a malformed sent_at must not stop
    the accounts after it from clustering."""
    rules = RulesConfig("config/rules.yaml")
    bad = _account(db, "bad-stamp", last_ok=T0, auth_failures=63)
    bad_alert = _first_notice(db, bad, T0 - timedelta(days=1))
    with db.cursor() as cur:
        cur.execute("UPDATE alerts SET sent_at = '' WHERE id = ?", (bad_alert,))
        cur.execute("UPDATE alerts SET sent_at = '0000-bad-stamp' WHERE id = ?", (bad_alert,))  # sorts FIRST, unparsable
    for i in range(3):
        a = _account(db, f"good-{i}", last_ok=T0, auth_failures=63)
        _first_notice(db, a, T0 + timedelta(seconds=i))
    incident = fleet_incident(db, rules)
    assert incident and sorted(incident["labels"]) == ["good-0", "good-1", "good-2"]


def test_a_dark_stranger_listed_after_the_members_is_still_paged_on_its_own(db):
    """#104 batch C: the member skip is `continue`, not `break` - a tenth
    account that is dark for its own reason, rows after the nine, still
    gets its per-account page while the nine share the fleet page."""
    _nine(db)
    alerter = _Recorder()
    alarms = WatchAlarms(db, RulesConfig("config/rules.yaml"), alerter)
    _account(db, "fine", last_ok=T0 + timedelta(hours=1))
    _account(db, "stranger", last_ok=T0 - timedelta(hours=5), failures=40)
    alarms.evaluate(now=T0 + timedelta(hours=1))
    assert len(_pages(alerter, FLEET_RULE)) == 1
    assert [p["evidence"]["account_label"] for p in _pages(alerter, "account_unwatchable_too_long")] == ["stranger"]
