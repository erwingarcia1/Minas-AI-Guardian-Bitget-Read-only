"""Part B of the v1.11.0 verification: a repeat of a coverage alert
re-notifies on a slow cadence; the first one stays loud.

Measured against v1.11.0 as tagged: a key that broke on Monday cost the
customer 2 emails a day, the emergency contact a note a day, and Minas 9
messages a day (3 direct + 3 escalation + 3 page) for as long as it stayed
broken - Paula's key stayed broken for 31 days. Now: the FIRST alert of an
incident is exactly as loud as before; every re-fire inside the repeat
window points at that first alert, reaches the customer's own email once a
day (the daily reminder that silence is not safety), and reaches nobody
else - no global channels, no escalation, no emergency contact, no page.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sentinel.alerts.dispatch import dispatch
from sentinel.alerts.escalation import Escalator
from sentinel.alerts.meaning import REPEAT_WINDOW_DAYS, routing_tier
from sentinel.detectors.base import Detection
from tests.conftest import make_account


class _Channel:
    def __init__(self, name):
        self.route_name = name
        self.recipients = [f"{name}@minas.test"]
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


def _unreachable(n=5):
    return Detection(rule="account_unreachable", severity="HIGH", confidence="high",
                     message="Your account is NOT being watched.", evidence={"consecutive_failures": n})


def _channels():
    direct = [_Channel("telegram"), _Channel("telegram_team"), _Channel("email")]
    return {"CRITICAL": direct, "HIGH": [_Channel("telegram")], "MEDIUM": [], "LOW": []}, direct


def _age(db, alert_id, hours):
    with db.cursor() as cur:
        cur.execute("UPDATE alerts SET sent_at = ? WHERE id = ?",
                    ((datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(), alert_id))


def test_the_first_coverage_alert_is_loud_and_the_daily_repeat_is_quiet(db):
    aid = make_account(db)
    channels, direct = _channels()
    first = dispatch(db, channels, aid, "acct", _unreachable(), {"HIGH": 30}, {"account_unreachable": 1440})
    assert [len(c.sent) for c in direct] == [1, 1, 1], "the first one reaches every direct channel"
    _age(db, first, 25)  # a day later, still broken: the cooldown has expired
    second = dispatch(db, channels, aid, "acct", _unreachable(6), {"HIGH": 30}, {"account_unreachable": 1440})
    assert second is not None and second != first, "the daily repeat is a real alert row, not a suppressed count"
    assert [len(c.sent) for c in direct] == [1, 1, 1], "the repeat reaches no global channel"
    with db.cursor() as cur:
        cur.execute("SELECT id, repeat_of, delivered_to FROM alerts ORDER BY id")
        rows = [tuple(r) for r in cur.fetchall()]
    assert rows == [(first, None, 3), (second, first, 0)]


def test_a_repeat_of_a_repeat_still_points_at_the_first_alert(db):
    aid = make_account(db)
    channels, _ = _channels()
    ids = []
    for day in range(3):
        alert_id = dispatch(db, channels, aid, "acct", _unreachable(5 + day), {"HIGH": 30}, {"account_unreachable": 1440})
        ids.append(alert_id)
        _age(db, alert_id, 25 * (3 - day))
    with db.cursor() as cur:
        cur.execute("SELECT repeat_of FROM alerts ORDER BY id")
        assert [r["repeat_of"] for r in cur.fetchall()] == [None, ids[0], ids[0]]


def test_after_the_repeat_window_a_new_incident_is_loud_again(db):
    aid = make_account(db)
    channels, direct = _channels()
    first = dispatch(db, channels, aid, "acct", _unreachable(), {"HIGH": 30}, {"account_unreachable": 1440})
    _age(db, first, 24 * (REPEAT_WINDOW_DAYS + 1))
    second = dispatch(db, channels, aid, "acct", _unreachable(), {"HIGH": 30}, {"account_unreachable": 1440})
    assert [len(c.sent) for c in direct] == [2, 2, 2]
    with db.cursor() as cur:
        cur.execute("SELECT repeat_of FROM alerts WHERE id = ?", (second,))
        assert cur.fetchone()["repeat_of"] is None


def test_a_repeat_never_escalates_and_never_reaches_the_emergency_contact(db):
    aid = make_account(db)
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = 'owner@x.test', alert_email_secondary = 'kin@x.test' WHERE id = ?", (aid,))
    channels, _ = _channels()
    first = dispatch(db, channels, aid, "acct", _unreachable(), {"HIGH": 30}, {"account_unreachable": 1440})
    _age(db, first, 25)
    second = dispatch(db, channels, aid, "acct", _unreachable(6), {"HIGH": 30}, {"account_unreachable": 1440})
    _age(db, second, 1)  # the repeat is an hour old and unacknowledged

    class _Router:
        calls = []

        def send(self, detection, account_label, emails, phones, *, account_id=None, alert_id=None):
            _Router.calls.append((alert_id, list(emails)))

    class _Alerter:
        pages = []

        def page(self, **kw):
            _Alerter.pages.append(kw)

    esc = Escalator([_Channel("telegram")], unacknowledged_minutes=15, high_unacknowledged_minutes=60,
                    operator_alerter=_Alerter(), contact_router=_Router())
    escalated = esc.check(db)
    assert escalated == 1, "the FIRST alert escalates; the repeat does not"
    assert all(alert_id == first for alert_id, _ in _Router.calls)
    assert len(_Alerter.pages) == 1
    with db.cursor() as cur:
        cur.execute("SELECT id, escalated_at FROM alerts ORDER BY id")
        rows = {r["id"]: r["escalated_at"] for r in cur.fetchall()}
    assert rows[first] is not None and rows[second] is None


def test_the_repeat_reaches_the_customers_email_once_and_nothing_louder(db, monkeypatch):
    from sentinel.alerts.contact import ContactRouter

    class _RecEmail:
        sent = []

        def __init__(self, host, port, user, pw, sender, recipients, *, audience="operator"):
            self.recipients = recipients

        def send(self, alert):
            _RecEmail.sent.append(list(self.recipients))

    class _RecSms:
        sent = []

        def __init__(self, recipients):
            self.recipients = recipients

        def send(self, alert):
            _RecSms.sent.append(list(self.recipients))
            return [{"to": p, "provider": "stub", "provider_message_id": None} for p in self.recipients]

    monkeypatch.setattr("sentinel.alerts.contact.EmailChannel", _RecEmail)
    monkeypatch.setattr("sentinel.alerts.contact.build_sms_channel", lambda recipients: _RecSms(recipients) if recipients else None)
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_SENDER", "alerts@test")
    aid = make_account(db)
    with db.cursor() as cur:
        cur.execute("INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, confidence, "
                    "sent_at, delivered_to) VALUES (?, 'account_unreachable', 'HIGH', 'k', 'm', '{}', 'high', "
                    "'2026-08-28T00:00:00+00:00', 0)", (aid,))
        alert_id = cur.lastrowid
    det = _unreachable()
    det.coverage_repeat = True
    ContactRouter(db=db).send(det, "acct", ["owner@x.test"], ["+15550001"], account_id=aid, alert_id=alert_id)
    assert _RecEmail.sent == [["owner@x.test"]], "the daily reminder, by email"
    assert _RecSms.sent == [], "no text for a repeat"
    det2 = _unreachable()
    ContactRouter(db=db).send(det2, "acct", ["owner@x.test"], ["+15550001"], account_id=aid, alert_id=alert_id)
    assert _RecSms.sent == [["+15550001"]], "the first alert of an incident still texts"


def test_a_quiet_repeat_does_not_page_delivered_to_nobody(db):
    """delivered_to = 0 is the repeat's normal state, not a delivery failure."""
    from sentinel.poller import AccountPoller

    aid = make_account(db)
    with db.cursor() as cur:
        cur.execute("INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, confidence, "
                    "sent_at, delivered_to, repeat_of) VALUES (?, 'account_unreachable', 'HIGH', 'k', 'm', '{}', "
                    "'high', '2026-08-28T00:00:00+00:00', 0, 1)", (aid,))
        repeat_id = cur.lastrowid

    class _Alerter:
        pages = []

        def page(self, **kw):
            _Alerter.pages.append(kw)

    poller = AccountPoller.__new__(AccountPoller)
    poller._db = db
    poller._operator_alerter = _Alerter()
    poller._page_if_undelivered(aid, "acct", _unreachable(), repeat_id)
    assert _Alerter.pages == []


def test_routing_tier_for_a_repeat_is_no_global_channel():
    assert routing_tier("account_unreachable", "HIGH") == "CRITICAL"
    assert routing_tier("account_unreachable", "HIGH", repeat=True) is None
    assert routing_tier("withdrawal_initiated", "HIGH", repeat=True) == "HIGH", "only coverage rules have quiet repeats"


def test_the_first_24_hours_of_a_broken_key_in_numbers(db):
    """The measurement Part B asked for, as a test: with the cooldown at a
    day, a key that breaks and stays broken produces in its first 24 hours
    ONE loud alert (every direct channel, one escalation) and nothing else."""
    aid = make_account(db)
    channels, direct = _channels()
    ids = []
    for minutes in range(0, 24 * 60, 45):  # every fast loop for a day, inside the 1440-minute cooldown
        alert_id = dispatch(db, channels, aid, "acct", _unreachable(5 + minutes // 45), {"HIGH": 30},
                            {"account_unreachable": 1440})
        if alert_id is not None:
            ids.append(alert_id)
    assert len(ids) == 1, f"one alert row in the first day, got {len(ids)}"
    assert sum(len(c.sent) for c in direct) == 3
    with db.cursor() as cur:
        cur.execute("SELECT repeat_count FROM alerts WHERE id = ?", (ids[0],))
        assert cur.fetchone()["repeat_count"] == 31, "the other 31 polls are counted on the row, not sent"


def test_the_repeat_window_boundary_is_inclusive_and_the_cooldown_boundary_is_not(db):
    """Two boundaries, pinned to within a second (an exact wall-clock
    equality is unreachable at microsecond resolution - the survivor ledger
    records the `<`/`<=` mutants as equivalent for that reason). A re-fire
    just inside REPEAT_WINDOW_DAYS is still a repeat; a re-fire just past
    the cooldown is a new row, not a suppressed count."""
    aid = make_account(db)
    channels, direct = _channels()
    first = dispatch(db, channels, aid, "acct", _unreachable(), {"HIGH": 30}, {"account_unreachable": 1440})
    # exactly at the cooldown: not suppressed
    with db.cursor() as cur:
        cur.execute("UPDATE alerts SET sent_at = ? WHERE id = ?",
                    ((datetime.now(timezone.utc) - timedelta(minutes=1440, seconds=1)).isoformat(), first))
    second = dispatch(db, channels, aid, "acct", _unreachable(6), {"HIGH": 30}, {"account_unreachable": 1440})
    assert second is not None and second != first
    # exactly at the window: still a repeat of the first
    with db.cursor() as cur:
        cur.execute("DELETE FROM alerts WHERE id = ?", (second,))
        cur.execute("UPDATE alerts SET sent_at = ? WHERE id = ?",
                    ((datetime.now(timezone.utc) - timedelta(days=REPEAT_WINDOW_DAYS) + timedelta(seconds=30)).isoformat(), first))
    third = dispatch(db, channels, aid, "acct", _unreachable(7), {"HIGH": 30}, {"account_unreachable": 1440})
    with db.cursor() as cur:
        cur.execute("SELECT repeat_of FROM alerts WHERE id = ?", (third,))
        assert cur.fetchone()["repeat_of"] == first
    assert [len(c.sent) for c in direct] == [1, 1, 1]


def test_the_quiet_leg_flag_travels_from_dispatch_to_the_router(db, monkeypatch):
    """Through the real seam: dispatch marks the detection, the poller hands
    the same detection to the router, and the router texts nobody. A flag
    that is set to None instead of False/True would read as 'not a repeat'
    at the router - this is the test that tells them apart."""
    from sentinel.alerts.contact import ContactRouter

    class _RecSms:
        sent = []

        def __init__(self, recipients):
            self.recipients = recipients

        def send(self, alert):
            _RecSms.sent.append(list(self.recipients))
            return []

    monkeypatch.setattr("sentinel.alerts.contact.build_sms_channel", lambda recipients: _RecSms(recipients) if recipients else None)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    aid = make_account(db)
    channels, _ = _channels()
    det1 = _unreachable()
    first = dispatch(db, channels, aid, "acct", det1, {"HIGH": 30}, {"account_unreachable": 1440})
    assert det1.coverage_repeat is False
    ContactRouter(db=db).send(det1, "acct", [], ["+15550001"], account_id=aid, alert_id=first)
    assert _RecSms.sent == [["+15550001"]], "the first alert texts"
    _age(db, first, 25)
    det2 = _unreachable(6)
    second = dispatch(db, channels, aid, "acct", det2, {"HIGH": 30}, {"account_unreachable": 1440})
    assert det2.coverage_repeat is True
    ContactRouter(db=db).send(det2, "acct", [], ["+15550001"], account_id=aid, alert_id=second)
    assert _RecSms.sent == [["+15550001"]], "the repeat does not text"
