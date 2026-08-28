"""#8, criterion 6: a customer must be able to tell their accounts apart.

Connection labels are operator slugs. A member with four Binance accounts
receiving an alert could not tell which one it concerned. The member now
names each connection from the portal, and the name is used everywhere a
PERSON reads it - alert subject and body, the digest, the portal, the
incident page, the CSV. The slug stays the internal identity: operators,
the operator-format alert, the CLI and every lookup keep it, and the
member's name is never a key.
"""
from __future__ import annotations

import http.client
import re
import urllib.parse
from pathlib import Path

import pytest

from sentinel.alerts.base import AlertMessage
from sentinel.naming import clean_name, display_label, set_member_name
from sentinel.portal import ACCOUNT_SCOPED, PRIMARY_ONLY, serve_portal, set_member_password
from tests.conftest import make_account

ROOT = Path(__file__).resolve().parent.parent
PRIMARY = "owner@example.com"
SECONDARY = "kin@example.com"
PASSWORD = "pw-long-enough-1"


@pytest.fixture
def portal(db):
    srv = serve_portal(db, 0, host="127.0.0.1")
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _accounts(db):
    ids = []
    for label in ("binance-jaypee", "binance-jaypee-2"):
        aid = make_account(db, label)
        with db.cursor() as cur:
            cur.execute("UPDATE accounts SET alert_email = ?, alert_email_secondary = ?, exchange = 'binance' WHERE id = ?",
                        (PRIMARY, SECONDARY, aid))
        ids.append(aid)
    return ids


def _login(port, email):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/login", urllib.parse.urlencode({"email": email, "password": PASSWORD}),
                 {"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    resp.read()
    cookie = resp.getheader("Set-Cookie")
    conn.close()
    assert cookie
    return cookie.split(";")[0]


def _post(port, path, form, cookie):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", path, urllib.parse.urlencode(form),
                 {"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie})
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, body


def _get(port, path, cookie):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers={"Cookie": cookie})
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, body


# --- naming from the portal --------------------------------------------------------------

def test_the_primary_names_a_connection_and_it_is_recorded_on_activity(db, portal):
    a1, a2 = _accounts(db)
    set_member_password(db, PRIMARY, PASSWORD, notify=None)
    cookie = _login(portal, PRIMARY)
    status, _ = _post(portal, "/account/name", {"account_id": a1, "name": "  Binance - savings "}, cookie)
    assert status in (302, 303)
    with db.cursor() as cur:
        cur.execute("SELECT member_name, label FROM accounts WHERE id = ?", (a1,))
        row = cur.fetchone()
    assert row["member_name"] == "Binance - savings" and row["label"] == "binance-jaypee", "the slug is untouched"
    _, activity = _get(portal, "/activity", cookie)
    assert "Connection renamed" in activity
    with db.cursor() as cur:
        cur.execute("SELECT actor, payload_json FROM events WHERE event_type = 'account_named'")
        ev = cur.fetchone()
    assert ev["actor"] == PRIMARY and "Binance - savings" in ev["payload_json"]


def test_a_secondary_is_refused_the_rename_on_the_real_route(db, portal):
    a1, _ = _accounts(db)
    set_member_password(db, SECONDARY, PASSWORD, notify=None)
    assert "/account/name" in PRIMARY_ONLY and "/account/name" in ACCOUNT_SCOPED
    status, body = _post(portal, "/account/name", {"account_id": a1, "name": "Mine now"}, _login(portal, SECONDARY))
    assert status == 403 and "primary contact" in body
    with db.cursor() as cur:
        cur.execute("SELECT member_name FROM accounts WHERE id = ?", (a1,))
        assert cur.fetchone()["member_name"] is None


def test_a_stranger_s_account_is_not_found_not_renamed(db, portal):
    a1, _ = _accounts(db)
    other = make_account(db, "theirs")
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = 'other@example.com' WHERE id = ?", (other,))
    set_member_password(db, PRIMARY, PASSWORD, notify=None)
    status, _ = _post(portal, "/account/name", {"account_id": other, "name": "x"}, _login(portal, PRIMARY))
    assert status == 404


@pytest.mark.parametrize("bad, why", [("x" * 41, "at most 40"), ("two\nlines", "one line"), ("<b>hi</b>", "cannot contain")])
def test_a_bad_name_is_refused_with_a_reason(db, portal, bad, why):
    a1, _ = _accounts(db)
    set_member_password(db, PRIMARY, PASSWORD, notify=None)
    status, body = _post(portal, "/account/name", {"account_id": a1, "name": bad}, _login(portal, PRIMARY))
    assert status == 400 and why in body


def test_clearing_the_name_falls_back_to_the_slug(db):
    a1, _ = _accounts(db)
    assert set_member_name(db, a1, "Savings", actor=PRIMARY)
    assert set_member_name(db, a1, "   ", actor=PRIMARY)
    with db.cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE id = ?", (a1,))
        row = cur.fetchone()
    assert row["member_name"] is None and display_label(row) == "binance-jaypee"
    assert not set_member_name(db, a1, None, actor=PRIMARY), "a no-op writes no event"
    assert clean_name("  Binance — main  ") == "Binance — main"


# --- the name everywhere a person reads it ----------------------------------------------------

def test_the_portal_shows_the_name_on_home_account_history_incident_and_csv(db, portal):
    a1, a2 = _accounts(db)
    set_member_name(db, a1, "Binance - savings", actor=PRIMARY)
    with db.cursor() as cur:
        cur.execute("INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, confidence, "
                    "sent_at, delivered_to) VALUES (?, 'withdrawal_initiated', 'CRITICAL', 'k', 'm', '{}', 'high', "
                    "'2026-08-28T00:00:00+00:00', 0)", (a1,))
        alert_id = cur.lastrowid
    set_member_password(db, PRIMARY, PASSWORD, notify=None)
    cookie = _login(portal, PRIMARY)
    for path in ("/", f"/account?id={a1}", "/alerts", f"/incident?id={alert_id}", "/status", "/alerts.csv"):
        status, body = _get(portal, path, cookie)
        assert status == 200, path
        assert "Binance - savings" in body, f"{path} does not show the member's name"
    _, home = _get(portal, "/", cookie)
    assert "binance-jaypee-2" in home, "an unnamed connection still shows its slug"


def test_alert_copy_for_the_customer_carries_the_name_and_the_operator_copy_the_slug():
    msg = AlertMessage(account_label="binance-jaypee", rule="withdrawal_initiated", severity="CRITICAL",
                       confidence="high", message="m", evidence={}, incident_id=7, display_label="Binance - savings")
    assert "Binance - savings" in msg.customer_subject() and "binance-jaypee" not in msg.customer_subject()
    assert msg.render_customer().startswith("Binance - savings")
    assert "binance-jaypee" in msg.render() and "Binance - savings" not in msg.render()
    plain = AlertMessage(account_label="binance-jaypee", rule="r", severity="HIGH", confidence="high", message="m", evidence={})
    assert plain.customer_label == "binance-jaypee"


def test_the_poller_puts_the_name_on_what_the_customer_receives(db, monkeypatch):
    """Through the real loop: a named account's alert reaches the contact
    router with the member's name, and the global (operator) channel with
    the slug."""
    from sentinel.alerts.dispatch import dispatch
    from sentinel.detectors.base import Detection

    a1, _ = _accounts(db)
    set_member_name(db, a1, "Binance - savings", actor=PRIMARY)
    with db.cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE id = ?", (a1,))
        row = cur.fetchone()

    class _Chan:
        route_name = "email"
        recipients = ["ops@minas.test"]
        seen = []

        def send(self, alert):
            _Chan.seen.append(alert)

    det = Detection(rule="withdrawal_initiated", severity="CRITICAL", confidence="high", message="m", evidence={"order_id": "1"})
    det.display_label = display_label(row)
    dispatch(db, {"CRITICAL": [_Chan()]}, a1, row["label"], det, {"CRITICAL": 0})
    assert _Chan.seen[0].account_label == "binance-jaypee"
    assert _Chan.seen[0].customer_label == "Binance - savings"


def test_the_digest_names_the_connection(db):
    from sentinel.alerts.digest import build_member_digest

    a1, a2 = _accounts(db)
    set_member_name(db, a1, "Binance - savings", actor=PRIMARY)
    body, _ = build_member_digest(db, PRIMARY)
    assert "Binance - savings" in body and "binance-jaypee-2" in body


def test_the_escalation_to_the_emergency_contact_names_the_connection(db):
    from datetime import datetime, timedelta, timezone

    from sentinel.alerts.escalation import Escalator

    a1, _ = _accounts(db)
    set_member_name(db, a1, "Binance - savings", actor=PRIMARY)
    with db.cursor() as cur:
        cur.execute("INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, confidence, "
                    "sent_at, delivered_to) VALUES (?, 'withdrawal_initiated', 'CRITICAL', 'k', 'm', '{}', 'high', ?, 0)",
                    (a1, (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()))

    class _Router:
        calls = []

        def send(self, detection, account_label, emails, phones, *, account_id=None, alert_id=None):
            _Router.calls.append((detection.message, emails))

    Escalator([], unacknowledged_minutes=15, contact_router=_Router()).check(db)
    note = next(m for m, emails in _Router.calls if SECONDARY in emails)
    assert "Binance - savings" in note and "binance-jaypee" not in note


# --- the name is never a key -----------------------------------------------------------------------

def test_the_members_name_is_never_used_as_a_lookup_key():
    offenders = []
    for path in (ROOT / "sentinel").rglob("*.py"):
        text = path.read_text()
        for m in re.finditer(r"WHERE[^;\"']*member_name", text):
            offenders.append(f"{path.name}: {m.group(0)[:80]}")
        if re.search(r"member_name\s*=\s*\?", text) and path.name != "naming.py":
            offenders.append(f"{path.name}: binds member_name")
    assert offenders == [], offenders
    src = (ROOT / "sentinel/naming.py").read_text()
    assert "WHERE id = ?" in src and "WHERE member_name" not in src
