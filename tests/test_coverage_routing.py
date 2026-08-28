"""#103 item 3: an unreachable account must not depend on the digest.

`account_unreachable` is the product announcing it has gone BLIND on a
customer. 41 of them on production were routed, by their severity, to a
digest that had never run. The routing is now decided by MEANING: a
coverage rule takes the CRITICAL channel set whatever its level, is judged
as a CRITICAL by the member's preferences (never held, email is the
floor), and escalates at the CRITICAL window to the emergency contact and
an operator page. The severity itself is NOT changed - a HIGH stays a
HIGH; only where it goes is decided here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sentinel.alerts.dispatch import dispatch
from sentinel.alerts.escalation import Escalator
from sentinel.alerts.meaning import COVERAGE_RULES, is_coverage_rule, preference_tier, routing_tier
from sentinel.detectors.base import Detection
from tests.conftest import make_account


class _Channel:
    def __init__(self, name):
        self.route_name = name
        self.recipients = [f"{name}@minas.test"]
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


def _unreachable(severity="MEDIUM"):
    return Detection(rule="account_unreachable", severity=severity, confidence="high",
                     message="Your account is NOT being watched.", evidence={"consecutive_failures": 5})


def _routine(severity="MEDIUM"):
    return Detection(rule="baseline_drift_new_deposit_source", severity=severity, confidence="low",
                     message="m", evidence={"address": "x"})


# --- the meaning ----------------------------------------------------------------------

def test_the_coverage_rules_are_the_ones_that_mean_we_are_blind():
    assert {"account_unreachable", "monitoring_key_tamper_suspected"} == COVERAGE_RULES
    assert is_coverage_rule("account_unreachable") and not is_coverage_rule("withdrawal_initiated")


@pytest.mark.parametrize("severity", ["LOW", "MEDIUM", "HIGH", "CRITICAL"])
def test_a_coverage_alert_routes_and_is_judged_as_critical_whatever_its_level(severity):
    assert routing_tier("account_unreachable", severity) == "CRITICAL"
    assert preference_tier("account_unreachable", severity) == "CRITICAL"
    assert routing_tier("withdrawal_burst", severity) == severity


# --- dispatch: the direct channel set, not the digest ------------------------------------

def test_a_medium_unreachable_reaches_every_direct_channel_and_keeps_its_severity(db):
    """The production shape: MEDIUM routed to [digest] alone. The digest is
    never a channel object (build_channels drops it), so MEDIUM's set is
    empty - and the coverage alert must go out on the CRITICAL set anyway."""
    aid = make_account(db)
    direct = [_Channel("telegram"), _Channel("telegram_team"), _Channel("email")]
    channels = {"CRITICAL": direct, "HIGH": [_Channel("telegram")], "MEDIUM": [], "LOW": []}
    alert_id = dispatch(db, channels, aid, "acct", _unreachable("MEDIUM"), {"MEDIUM": 720, "CRITICAL": 15})
    assert alert_id is not None
    assert [len(c.sent) for c in direct] == [1, 1, 1]
    with db.cursor() as cur:
        cur.execute("SELECT severity, delivered_to FROM alerts WHERE id = ?", (alert_id,))
        row = cur.fetchone()
    assert row["severity"] == "MEDIUM", "the level is not corrupted to inherit the routing"
    assert row["delivered_to"] == 3


def test_a_routine_medium_still_waits_for_the_digest(db):
    """The control: the meaning is what routes directly, not the level."""
    aid = make_account(db)
    direct = [_Channel("telegram"), _Channel("email")]
    channels = {"CRITICAL": direct, "MEDIUM": []}
    dispatch(db, channels, aid, "acct", _routine("MEDIUM"), {"MEDIUM": 720, "CRITICAL": 15})
    assert [len(c.sent) for c in direct] == [0, 0]


# --- escalation: the CRITICAL window, the critical path ------------------------------------

class _Router:
    def __init__(self):
        self.calls = []

    def send(self, detection, account_label, emails, phones, *, account_id=None, alert_id=None):
        self.calls.append((getattr(detection, "audience", "customer"), list(emails), list(phones)))


class _Alerter:
    def __init__(self):
        self.pages = []

    def page(self, **kw):
        self.pages.append(kw)


def _stale_alert(db, aid, rule, severity, minutes_ago):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, confidence, sent_at, "
            "delivered_to) VALUES (?, ?, ?, ?, 'm', '{}', 'high', ?, 0)",
            (aid, rule, severity, f"k-{rule}-{severity}", (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()))
        return cur.lastrowid


def _escalator(router, alerter):
    return Escalator([_Channel("telegram")], unacknowledged_minutes=15, high_unacknowledged_minutes=60,
                     operator_alerter=alerter, contact_router=router)


def test_an_unacknowledged_medium_unreachable_escalates_at_the_critical_window(db):
    aid = make_account(db)
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = 'owner@x.test', alert_email_secondary = 'kin@x.test' WHERE id = ?", (aid,))
    alert_id = _stale_alert(db, aid, "account_unreachable", "MEDIUM", minutes_ago=20)
    router, alerter = _Router(), _Alerter()
    assert _escalator(router, alerter).check(db) == 1
    audiences = [c[0] for c in router.calls]
    assert "secondary" in audiences, "the emergency contact hears that monitoring is blind"
    assert any("kin@x.test" in c[1] for c in router.calls)
    assert [p["rule"] for p in alerter.pages] == ["critical_alert_escalated"]
    with db.cursor() as cur:
        cur.execute("SELECT escalated_at, severity FROM alerts WHERE id = ?", (alert_id,))
        row = cur.fetchone()
    assert row["escalated_at"] is not None and row["severity"] == "MEDIUM"


def test_inside_the_critical_window_a_coverage_alert_waits_like_a_critical(db):
    aid = make_account(db)
    _stale_alert(db, aid, "account_unreachable", "HIGH", minutes_ago=10)
    assert _escalator(_Router(), _Alerter()).check(db) == 0


def test_a_routine_medium_never_escalates(db):
    aid = make_account(db)
    _stale_alert(db, aid, "baseline_drift_new_deposit_source", "MEDIUM", minutes_ago=600)
    assert _escalator(_Router(), _Alerter()).check(db) == 0


def test_the_escalation_words_say_coverage_not_critical_for_a_high(db):
    aid = make_account(db)
    _stale_alert(db, aid, "account_unreachable", "HIGH", minutes_ago=20)
    tele = _Channel("telegram")
    esc = Escalator([tele], unacknowledged_minutes=15, high_unacknowledged_minutes=60)
    esc.check(db)
    assert tele.sent and "coverage alert" in tele.sent[0].message and "blind" in tele.sent[0].message
    assert not tele.sent[0].message.startswith("CRITICAL alert")


# --- preferences: never held, never muted ----------------------------------------------------

class _RecEmail:
    instances = []

    def __init__(self, host, port, user, pw, sender, recipients, *, audience="operator"):
        self.recipients = recipients
        _RecEmail.instances.append(self)

    def send(self, alert):
        pass


def test_a_member_cannot_mute_or_hold_a_coverage_alert(db, monkeypatch):
    from sentinel.alerts.contact import ContactRouter
    from sentinel.alerts.preferences import set_member_prefs
    from sentinel.portal import set_member_password

    _RecEmail.instances = []
    monkeypatch.setattr("sentinel.alerts.contact.EmailChannel", _RecEmail)
    monkeypatch.setattr("sentinel.alerts.contact.build_sms_channel", lambda recipients: None)
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_SENDER", "alerts@test")
    aid = make_account(db)
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = 'owner@x.test' WHERE id = ?", (aid,))
    set_member_password(db, "owner@x.test", "pw-long-enough-1", notify=None)
    # email off for HIGH and MEDIUM, quiet hours on all day
    grid = {sev: {"email": sev == "CRITICAL", "telegram": False, "sms": False} for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}
    set_member_prefs(db, "owner@x.test", grid=grid, quiet_hours_enabled=True, quiet_start="00:00",
                     quiet_end="23:59", timezone_name="UTC")
    with db.cursor() as cur:
        cur.execute("INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, confidence, "
                    "sent_at, delivered_to) VALUES (?, 'account_unreachable', 'HIGH', 'k', 'm', '{}', 'high', "
                    "'2026-08-28T00:00:00+00:00', 0)", (aid,))
        alert_id = cur.lastrowid
    ContactRouter(db=db).send(_unreachable("HIGH"), "acct", ["owner@x.test"], [], account_id=aid, alert_id=alert_id)
    assert _RecEmail.instances and _RecEmail.instances[0].recipients == ["owner@x.test"]
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) c FROM held_alerts")
        assert cur.fetchone()["c"] == 0
