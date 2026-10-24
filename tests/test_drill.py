"""#37 / decision 2: the product tests itself end to end.

Weekly, against the DEDICATED drill account, never a customer's; the
result in the portal (when, passed/failed, detection-to-delivery latency);
no scheduled customer texts; an on-demand "test my alerts" for a member
who wants the proof on their own phone; a failed drill pages Minas as an
outage; and - the owner's addition - "last successful delivery per channel
per customer" derived from REAL traffic, with staleness surfaced.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sentinel.alerts.delivery import record_delivery
from sentinel.drill import (
    DrillSchedule,
    delivery_recency,
    drill_account,
    last_drill,
    mark_drill_account,
    recent_drills,
    run_drill,
)
from sentinel.portal import set_member_password
from tests.conftest import make_account

MINAS = "ops@minas.test"


class _Chan:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, alert):
        if self.fail:
            raise RuntimeError("channel down")
        self.sent.append(alert)


class _Pager:
    def __init__(self):
        self.pages = []

    def page(self, **kw):
        self.pages.append(kw)
        return 1


class _Rules:
    def __init__(self, enabled=True, days=7):
        self._enabled, self._days = enabled, days

    def get(self, *keys, default=None):
        return self._enabled if keys == ("drills", "enabled") else default

    def get_int(self, *keys, default=None):
        return self._days if keys == ("drills", "interval_days") else default


def _drill_acct(db, email=MINAS, phone=None):
    account_id = make_account(db, label="minas-drill")
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = ?, alert_phone = ? WHERE id = ?", (email, phone, account_id))
    mark_drill_account(db, account_id)
    return account_id


class _Router:
    """Records what the contact router was asked to send; optionally
    writes the delivery rows a real send would."""

    def __init__(self, db, record=True):
        self.db, self.record, self.calls = db, record, []

    def send(self, detection, label, emails, phones, *, account_id=None, alert_id=None):
        self.calls.append((detection, label, emails, phones, account_id, alert_id))
        if self.record:
            for e in emails:
                record_delivery(self.db, channel="email", recipient=e, status="accepted", audience="customer",
                                kind="alert", alert_id=alert_id)


# --- the dedicated account ----------------------------------------------------------

def test_the_scheduled_drill_runs_only_against_the_dedicated_account(db):
    make_account(db, label="customer-1")
    assert drill_account(db) is None
    a = _drill_acct(db)
    assert drill_account(db)["id"] == a


def test_no_drill_account_is_a_failed_drill_and_a_page_not_a_silent_skip(db):
    make_account(db, label="customer-1")
    pager = _Pager()
    out = run_drill(db, {"CRITICAL": [_Chan()]}, _Router(db), pager, _Rules())
    assert out["result"] == "failed" and "no drill account" in out["detail"]
    assert pager.pages[-1]["rule"] == "drill_no_account"
    assert last_drill(db, kind="scheduled")["result"] == "failed"


# --- the whole path, recorded -------------------------------------------------------------

def test_a_drill_travels_the_path_and_records_passed_with_latency(db):
    a = _drill_acct(db)
    chan = _Chan()
    router = _Router(db)
    pager = _Pager()
    out = run_drill(db, {"CRITICAL": [chan]}, router, pager, _Rules())
    assert out["result"] == "passed" and out["account_id"] == a
    assert out["delivered_to"] == 2  # the global channel + the contact email
    assert out["latency_ms"] is not None and out["latency_ms"] >= 0
    assert pager.pages == []
    assert len(chan.sent) == 1 and chan.sent[0].drill is True
    assert router.calls[0][0].is_drill and router.calls[0][2] == [MINAS]
    row = last_drill(db, kind="scheduled")
    assert row["result"] == "passed" and row["delivered_to"] == 2 and row["label"] == "minas-drill"


def test_the_drill_alert_is_labelled_on_every_surface_and_carries_no_runbook(db):
    _drill_acct(db)
    chan = _Chan()
    run_drill(db, {"CRITICAL": [chan]}, _Router(db), _Pager(), _Rules())
    alert = chan.sent[0]
    assert alert.render().startswith("🛡 MINAS AI GUARDIAN - DRILL")
    assert "[DRILL]" in alert.render() and "Runbook" not in alert.render()
    assert alert.operator_subject().startswith("[Minas AI Guardian DRILL]")
    assert alert.customer_subject().startswith("[DRILL]")
    body = alert.render_customer()
    assert body.startswith("THIS IS A DRILL")
    assert "Revoke" not in body and "What to check" not in body
    with db.cursor() as cur:
        cur.execute("SELECT rule, acknowledged_by FROM alerts")
        row = cur.fetchone()
    assert row["rule"] == "drill" and row["acknowledged_by"] == "drill", "the drill acknowledges itself"


def test_a_failed_drill_pages_minas_as_an_outage(db):
    _drill_acct(db)
    pager = _Pager()
    out = run_drill(db, {"CRITICAL": [_Chan(fail=True)]}, _Router(db, record=False), pager, _Rules())
    assert out["result"] == "failed" and out["delivered_to"] == 0
    page = pager.pages[-1]
    assert page["rule"] == "drill_failed" and page["severity"] == "CRITICAL"
    assert "reached nobody" in out["detail"]


def test_each_drill_is_its_own_alert_never_a_suppressed_repeat(db):
    _drill_acct(db)
    chan = _Chan()
    run_drill(db, {"CRITICAL": [chan]}, _Router(db), _Pager(), _Rules())
    run_drill(db, {"CRITICAL": [chan]}, _Router(db), _Pager(), _Rules())
    assert len(chan.sent) == 2
    assert len(recent_drills(db)) == 2


def test_the_escalator_never_escalates_a_drill(db):
    from sentinel.alerts.escalation import Escalator

    _drill_acct(db)
    run_drill(db, {"CRITICAL": [_Chan()]}, _Router(db), _Pager(), _Rules())

    class _Ops:
        pages = []

        def page(self, **kw):
            _Ops.pages.append(kw)

    # Un-acknowledged and past the CRITICAL window but still LIVE (inside
    # the stale bound), a drill is not an escalation candidate: the rule
    # exclusion is what protects it, not the age.
    with db.cursor() as cur:
        cur.execute("UPDATE alerts SET acknowledged_at = NULL, acknowledged_by = NULL, sent_at = ?",
                    ((datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),))
    chans = [_Chan()]
    esc = Escalator(chans, unacknowledged_minutes=15, operator_alerter=_Ops())
    assert esc.check(db) == 0
    assert chans[0].sent == []
    # Old enough to be a backlog: a drill is not reported to operators as
    # one either - the exclusion holds on both paths.
    with db.cursor() as cur:
        cur.execute("UPDATE alerts SET sent_at = ?", ((datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),))
    assert esc.check(db) == 0
    assert chans[0].sent == [] and _Ops.pages == []


def test_a_drill_bypasses_the_members_quiet_hours(db):
    """They asked for it: the on-demand drill reaches them whatever their
    preferences say."""
    from sentinel.alerts.contact import ContactRouter
    from sentinel.alerts.preferences import set_member_prefs

    a = make_account(db, label="alice-1")
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = 'alice@example.com' WHERE id = ?", (a,))
    set_member_password(db, "alice@example.com", "pw-long-enough-1")
    set_member_prefs(db, "alice@example.com", timezone_name="UTC", quiet_hours_enabled=True,
                     quiet_start="00:00", quiet_end="23:59", grid={"CRITICAL": {"email": True}})
    sent = []

    class _Email:
        def __init__(self, *a, **k):
            self.recipients = a[5]

        def send(self, alert):
            sent.append((self.recipients, alert.drill))

    import sentinel.alerts.contact as contact_module

    contact_module.EmailChannel = _Email  # restored by the test's own scope below
    try:
        import os

        os.environ["SMTP_HOST"], os.environ["SMTP_SENDER"] = "smtp.test", "g@test"
        router = ContactRouter(db=db)
        out = run_drill(db, {}, router, _Pager(), _Rules(), account_id=a, kind="on_demand",
                        requested_by="alice@example.com")
    finally:
        from sentinel.alerts.email import EmailChannel

        contact_module.EmailChannel = EmailChannel
    assert sent == [(["alice@example.com"], True)]
    assert out["result"] == "passed" and out["delivered_to"] == 1


# --- the schedule ----------------------------------------------------------------------------

def test_the_schedule_is_due_when_there_was_never_a_drill_and_after_the_interval(db):
    runs = []
    sched = DrillSchedule(db, _Rules(days=7), lambda now: runs.append(now))
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    assert sched.due(now)
    _drill_acct(db)
    run_drill(db, {"CRITICAL": [_Chan()]}, _Router(db), _Pager(), _Rules(), now=now)
    assert not sched.due(now + timedelta(days=6, hours=23))
    assert sched.due(now + timedelta(days=7))


def test_the_schedule_reads_the_table_so_a_restart_resumes_the_cadence(db):
    _drill_acct(db)
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    run_drill(db, {"CRITICAL": [_Chan()]}, _Router(db), _Pager(), _Rules(), now=now)
    fresh = DrillSchedule(db, _Rules(days=7), lambda n: (_ for _ in ()).throw(AssertionError("ran early")))
    assert fresh.evaluate(now + timedelta(days=2)) is None


def test_a_disabled_schedule_never_runs(db):
    sched = DrillSchedule(db, _Rules(enabled=False), lambda now: (_ for _ in ()).throw(AssertionError("ran")))
    assert sched.evaluate() is None


def test_the_engine_ticks_the_schedule(db, cipher, rules, monkeypatch, tmp_path):
    """The shipped config has drills.enabled: false while the drill account
    is an open owner decision (#37, 2026-08-31); this test is about the
    ENGINE WIRING, so it runs against a config with the schedule on."""
    from pathlib import Path

    import yaml

    from sentinel.config import RulesConfig
    from sentinel.engine import Engine

    shipped = yaml.safe_load(Path("config/rules.yaml").read_text())
    shipped["drills"]["enabled"] = True
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(shipped))
    _drill_acct(db)
    chan = _Chan()
    engine = Engine(db, cipher, RulesConfig(str(path)), {"CRITICAL": [chan], "HIGH": [], "MEDIUM": [], "LOW": []})
    engine._scheduler_tick()
    assert last_drill(db, kind="scheduled") is not None


# --- delivery recency: real traffic, never the drill -------------------------------------

def test_recency_is_derived_from_real_deliveries_and_flags_stale_channels(db):
    a = make_account(db, label="alice-1")
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = 'alice@example.com', alert_phone = '+15550001' WHERE id = ?", (a,))
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    record_delivery(db, channel="email", recipient="alice@example.com", status="accepted", audience="customer",
                    kind="alert", alert_id=None)
    with db.cursor() as cur:
        cur.execute("UPDATE deliveries SET created_at = ? WHERE channel = 'email'",
                    ((now - timedelta(days=2)).isoformat(),))
        cur.execute("INSERT INTO deliveries (account_id, alert_id, rule, channel, recipient, audience, kind, "
                    "status, created_at) VALUES (?, NULL, 'r', 'sms', '+15550001', 'customer', 'alert', "
                    "'accepted', ?)", (a, (now - timedelta(days=45)).isoformat()))
    r = delivery_recency(db, "alice@example.com", now=now)
    assert r["email"]["last"] and not r["email"]["stale"]
    assert r["sms"]["last"] and r["sms"]["stale"]
    assert r["telegram"]["last"] is None and r["telegram"]["stale"]


def test_a_scheduled_drill_leaves_no_recency_for_a_customer(db):
    """The drill's own deliveries go to the drill account's contacts; a
    customer's recency is untouched by it."""
    _drill_acct(db)
    run_drill(db, {"CRITICAL": [_Chan()]}, _Router(db), _Pager(), _Rules())
    assert delivery_recency(db, "alice@example.com")["email"]["last"] is None
    assert delivery_recency(db, MINAS)["email"]["last"] is not None


# --- the portal --------------------------------------------------------------------------------

def test_the_status_page_shows_the_last_drill_and_offers_a_test(db):
    from sentinel.portal import _render_status

    a = make_account(db, label="alice-1")
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = 'alice@example.com' WHERE id = ?", (a,))
    set_member_password(db, "alice@example.com", "pw-long-enough-1")
    _drill_acct(db)
    run_drill(db, {"CRITICAL": [_Chan()]}, _Router(db), _Pager(), _Rules())
    page = _render_status(db, "alice@example.com")
    assert "Alert drills" in page and "Last scheduled drill" in page and "Passed" in page
    assert 'action="/drill"' in page and "Test my alerts now" in page
    assert "Last message that reached you" in page and "Never" in page


def test_the_dashboard_lists_drills(db):
    from sentinel.dashboard import _drills_section

    assert _drills_section(db) == ""
    _drill_acct(db)
    run_drill(db, {"CRITICAL": [_Chan(fail=True)]}, _Router(db, record=False), _Pager(), _Rules())
    card = _drills_section(db)
    assert "Fire drills" in card and "FAILED" in card and "reached nobody" in card


def test_the_cli_drill_needs_a_name_once_operators_exist(db, capsys):
    import argparse
    import os

    from sentinel.cli import cmd_drill
    from sentinel.operators import create_operator

    create_operator(db, "erwin", "correct-horse-battery")
    db.close()
    os.environ["SENTINEL_DB_PATH"] = str(db._path)
    assert cmd_drill(argparse.Namespace(account=None, as_operator=None)) == 2
    assert "--as" in capsys.readouterr().err
