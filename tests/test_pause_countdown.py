"""#98 / decision 15: the 24-hour cancellable pause - request, countdown,
automatic execution, and above all DURABILITY: the table is the countdown,
so a deadline that passed while the process was down executes on the next
startup - never skipped, never lost. Deploys happen, and the #81 timer can
restart the container on its own; a member who requested a pause must get
exactly what they were promised regardless.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import pytest

from sentinel.pause import (
    EXPIRY_NOTICE_HOURS,
    PAUSE_COUNTDOWN_HOURS,
    PauseMaintenance,
    cancel_pause,
    pending_pause,
    request_pause,
)

EMAIL = "alice@example.com"
NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def sent(monkeypatch):
    import sentinel.alerts.contact as contact_module

    out: list[tuple] = []

    class _Capture:
        def __init__(self, *a, **kw):
            pass

        def send(self, detection, account_label, emails, phones, **kw):
            out.append((detection, tuple(emails)))

    monkeypatch.setattr(contact_module, "ContactRouter", _Capture)
    return out


def _account(db, label="acct-p1", *, secondary="kin@example.com", status="active"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, alert_email_secondary, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, ?, ?, '2026-01-01', '2026-01-01')",
            (label, EMAIL, secondary, status))
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
        return account_id


def _events(db, account_id):
    with db.cursor() as cur:
        cur.execute("SELECT event_type FROM events WHERE account_id = ? ORDER BY id", (account_id,))
        return [r["event_type"] for r in cur.fetchall()]


def _status(db, account_id):
    with db.cursor() as cur:
        cur.execute("SELECT status FROM accounts WHERE id = ?", (account_id,))
        return cur.fetchone()["status"]


# --- request ------------------------------------------------------------------

def test_a_request_starts_a_real_24_hour_countdown_and_emails_every_contact(db, sent):
    account_id = _account(db)
    execute_at = request_pause(db, account_id, EMAIL, now=NOW)

    assert execute_at == (NOW + timedelta(hours=PAUSE_COUNTDOWN_HOURS)).isoformat()
    row = pending_pause(db, account_id)
    assert row is not None and row["requested_by"] == EMAIL
    assert "pause_requested" in _events(db, account_id)
    (detection, emails), = sent
    assert set(emails) == {EMAIL, "kin@example.com"}, "every contact, at request time"
    assert "unless cancelled" in detection.message


def test_the_notification_is_linkless_and_says_contact_minas(db, sent):
    """Decision 16's rule, applied at its sharpest: the recovery path must
    work for an owner locked out of the portal, so it is a human channel
    and never a link."""
    account_id = _account(db)
    request_pause(db, account_id, EMAIL, now=NOW)
    (detection, _), = sent
    assert "http" not in detection.message.lower()
    assert "contact Minas" in detection.message
    assert "never include links" in detection.message


def test_rerequesting_restarts_a_full_24_hours_and_never_shortens(db, sent):
    account_id = _account(db)
    request_pause(db, account_id, EMAIL, now=NOW)
    later = NOW + timedelta(hours=20)
    second = request_pause(db, account_id, EMAIL, now=later)

    assert second == (later + timedelta(hours=24)).isoformat(), "a fresh full window"
    with db.cursor() as cur:
        cur.execute("SELECT status FROM pause_requests WHERE account_id = ? ORDER BY id", (account_id,))
        statuses = [r["status"] for r in cur.fetchall()]
    assert statuses == ["cancelled", "pending"], "one pending countdown at a time"


# --- cancel -------------------------------------------------------------------

def test_cancel_by_member_and_by_minas_both_work_and_say_who(db, sent):
    account_id = _account(db)
    request_pause(db, account_id, EMAIL, now=NOW)
    assert cancel_pause(db, account_id, EMAIL) is True
    assert pending_pause(db, account_id) is None
    assert cancel_pause(db, account_id, EMAIL) is False, "nothing pending, nothing recorded"

    request_pause(db, account_id, EMAIL, now=NOW)
    assert cancel_pause(db, account_id, "operator erwin") is True
    with db.cursor() as cur:
        cur.execute("SELECT cancelled_by FROM pause_requests WHERE account_id = ? "
                    "AND cancelled_by LIKE 'operator%'", (account_id,))
        assert cur.fetchone() is not None


def test_the_operator_cli_cancel_is_gated_and_attributed(db, sent, monkeypatch):
    from sentinel.cli import cmd_cancel_pause
    from sentinel.operators import create_operator

    account_id = _account(db, "acct-p2")
    request_pause(db, account_id, EMAIL, now=NOW)
    create_operator(db, "erwin", "a-long-enough-password-1")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))

    ns = argparse.Namespace(label="acct-p2", as_operator=None)
    assert cmd_cancel_pause(ns) == 2, "operators exist - a name is required"
    assert pending_pause(db, account_id) is not None

    ns = argparse.Namespace(label="acct-p2", as_operator="erwin")
    assert cmd_cancel_pause(ns) == 0
    assert pending_pause(db, account_id) is None


# --- execution, and THE restart guarantee -------------------------------------

def test_a_due_pause_executes_disables_and_records(db, sent):
    account_id = _account(db)
    request_pause(db, account_id, EMAIL, now=NOW)
    sent.clear()

    n = PauseMaintenance(db).evaluate(now=NOW + timedelta(hours=24, minutes=1))

    assert n == 1
    assert _status(db, account_id) == "disabled"
    assert pending_pause(db, account_id) is None
    events = _events(db, account_id)
    assert "account_disabled" in events and "pause_executed" in events
    assert any("has taken effect" in d.message for d, _ in sent), "contacts told it happened"


def test_a_deadline_that_passed_while_the_process_was_down_executes_on_startup(db, sent):
    """THE durability criterion. The request happened; the process died (a
    deploy, the #81 timer); the deadline passed in the dark. A FRESH
    maintenance instance's first evaluate - the first scheduler tick of the
    new process - must execute it: not skip it, not lose it."""
    account_id = _account(db)
    request_pause(db, account_id, EMAIL, now=NOW)
    # No in-memory state survives: everything the new process knows is the
    # table. A brand-new instance stands in for the restarted process.
    fresh_process = PauseMaintenance(db)

    n = fresh_process.evaluate(now=NOW + timedelta(days=3))

    assert n == 1, "an overdue pause must execute on the next startup"
    assert _status(db, account_id) == "disabled"
    with db.cursor() as cur:
        cur.execute("SELECT status FROM pause_requests WHERE account_id = ?", (account_id,))
        assert cur.fetchone()["status"] == "executed", "not lost, not left pending"


def test_a_not_yet_due_pause_survives_ticks_untouched(db, sent):
    account_id = _account(db)
    request_pause(db, account_id, EMAIL, now=NOW)
    n = PauseMaintenance(db).evaluate(now=NOW + timedelta(hours=12))
    assert n == 0
    assert pending_pause(db, account_id) is not None
    assert _status(db, account_id) == "active"


def test_a_cancelled_pause_never_executes_however_late_the_tick(db, sent):
    account_id = _account(db)
    request_pause(db, account_id, EMAIL, now=NOW)
    cancel_pause(db, account_id, EMAIL)
    assert PauseMaintenance(db).evaluate(now=NOW + timedelta(days=2)) == 0
    assert _status(db, account_id) == "active"


def test_the_scheduler_actually_evaluates_the_countdown(db, cipher):
    """The #52 standard, again: a countdown the scheduler never reads is a
    decorative promise."""
    from prometheus_client import CollectorRegistry

    from sentinel.config import RulesConfig
    from sentinel.engine import Engine
    from sentinel.metrics import Metrics

    engine = Engine(db, cipher, RulesConfig("config/rules.yaml"), {},
                    metrics=Metrics(registry=CollectorRegistry()))

    class Spy:
        calls = 0

        def evaluate(self, now=None):
            Spy.calls += 1

    engine._pause_maintenance = Spy()
    engine._scheduler_tick()
    assert Spy.calls == 1, "the scheduler is not driving the pause countdown"


# --- the near-expiry reminder --------------------------------------------------

def test_near_expiry_sends_one_reminder_exactly_once(db, sent):
    account_id = _account(db)
    request_pause(db, account_id, EMAIL, now=NOW)
    sent.clear()
    maintenance = PauseMaintenance(db)
    near = NOW + timedelta(hours=24) - timedelta(hours=EXPIRY_NOTICE_HOURS) + timedelta(minutes=5)

    maintenance.evaluate(now=near)
    maintenance.evaluate(now=near + timedelta(minutes=10))

    reminders = [d for d, _ in sent if "Reminder" in d.message]
    assert len(reminders) == 1, "again near expiry - once, not a drumbeat"
    assert "http" not in reminders[0].message.lower()


# --- the surfaces --------------------------------------------------------------

def test_the_account_page_walks_request_pending_and_cancel(db, sent):
    from sentinel.portal import _render_member_account

    account_id = _account(db)
    page = _render_member_account(db, EMAIL, account_id)
    assert "Pause monitoring" in page
    assert 'action="/pause/request"' in page
    assert "Call Minas; a person can do it immediately" in page, (
        "the human fast path is stated where the slow path is offered")

    request_pause(db, account_id, EMAIL, now=NOW)
    page = _render_member_account(db, EMAIL, account_id)
    assert "Pause pending" in page
    assert 'action="/pause/cancel"' in page
    assert "we never email links" in page


def test_the_home_banner_says_about_to_not_be_watched(db, sent):
    from sentinel.portal import _render_home

    account_id = _account(db)
    with db.cursor() as cur:
        cur.execute("UPDATE poll_state SET last_success_at = ? WHERE account_id = ?",
                    (datetime.now(timezone.utc).isoformat(), account_id))
    request_pause(db, account_id, EMAIL, now=NOW)
    page = _render_home(db, EMAIL)
    assert "Monitoring is about to stop" in page
    assert "acct-p1" in page
    assert 'role="alert"' in page


def test_the_digest_carries_the_pending_pause_line(db, sent):
    from sentinel.alerts.digest import build_member_digest

    account_id = _account(db)
    with db.cursor() as cur:
        cur.execute("UPDATE poll_state SET last_success_at = ?, checks_total = 5 "
                    "WHERE account_id = ?", (datetime.now(timezone.utc).isoformat(), account_id))
    request_pause(db, account_id, EMAIL, now=NOW)
    body, _ = build_member_digest(db, EMAIL)
    # The label is asserted by name: two mutation survivors
    # (x_build_member_digest__mutmut_127/136) proved "PAUSE PENDING" alone
    # passes with the wrong account - or none - in the line.
    assert "PAUSE PENDING: acct-p1 stops being watched" in body
    assert "unless cancelled" in body


def test_a_member_cannot_pause_an_account_that_is_not_theirs(db, sent):
    """The isolation boundary, applied to the newest write: scope is checked
    before anything else, and the refusal is the same not-found every other
    page uses."""
    import http.client
    import urllib.parse

    from sentinel.portal import serve_portal, set_member_password

    _account(db, "mine")
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES ('theirs', 'bitget', 'independent', 'x', 'y', 'z', 'other@example.com', "
            "'active', '2026-01-01', '2026-01-01')")
        other_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (other_id,))
    set_member_password(db, EMAIL, "correct horse battery", notify=None)

    server = serve_portal(db, 0, host="127.0.0.1")
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/login",
                     body=urllib.parse.urlencode({"email": EMAIL, "password": "correct horse battery"}),
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        resp.read()
        token = (resp.getheader("Set-Cookie") or "").split("minas_portal=", 1)[1].split(";", 1)[0]
        conn.close()

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/pause/request",
                     body=urllib.parse.urlencode({"account_id": other_id}),
                     headers={"Content-Type": "application/x-www-form-urlencoded",
                              "Cookie": f"minas_portal={token}"})
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 404, "an account outside the member's scope does not exist"
    finally:
        server.shutdown()
        server.server_close()

    assert pending_pause(db, other_id) is None, "no countdown started on the refusal"
