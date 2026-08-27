"""#46 / decision 6 interim: erasure on request.

Hard-delete now; record the forensic gap explicitly AS a gap; tell the
customer that is what happened. Scope is the person's own accounts - the
ones they are the primary contact of - plus their login; on an account
where they are only the second contact, they are removed as a contact.
"""
from __future__ import annotations

import json

import pytest

from sentinel.erasure import confirmation_text, erase_member, erasure_scope, export_member_data
from sentinel.portal import set_member_password
from tests.conftest import make_account

ALICE = "alice@example.com"
BOB = "bob@example.com"


def _account(db, label, primary, secondary=None, phone=None):
    aid = make_account(db, label=label)
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = ?, alert_email_secondary = ?, alert_phone = ?, "
                    "encrypted_api_key = 'K', encrypted_secret = 'S' WHERE id = ?",
                    (primary, secondary, phone, aid))
    return aid


def _history(db, aid):
    from sentinel.alerts.dispatch import dispatch
    from sentinel.detectors.base import Detection
    from sentinel.events import append_event

    append_event(db, aid, "withdrawal_initiated", "CRITICAL", {"order_id": "o1"})
    det = Detection(rule="withdrawal_initiated", severity="CRITICAL", confidence="high", message="m",
                    evidence={"order_id": "o1"})
    alert_id = dispatch(db, {}, aid, "acct", det, {"CRITICAL": 15})
    with db.cursor() as cur:
        cur.execute("INSERT INTO sms_deliveries (account_id, alert_id, rule, phone, status, created_at) "
                    "VALUES (?, ?, 'r', '+1', 'sent', '2026-01-01')", (aid, alert_id))
        cur.execute("INSERT INTO known_addresses (account_id, address, coin, source, first_seen_at) VALUES (?, 'addr', 'BTC', 'withdrawal', '2026-01-01')", (aid,))
    return alert_id


def _counts(db, aid):
    out = {}
    with db.cursor() as cur:
        for table in ("events", "alerts", "sms_deliveries", "known_addresses"):
            cur.execute(f"SELECT count(*) AS n FROM {table} WHERE account_id = ?", (aid,))
            out[table] = cur.fetchone()["n"]
    return out


# --- scope --------------------------------------------------------------------------

def test_scope_is_the_persons_own_accounts_and_their_login(db):
    a = _account(db, "a", ALICE)
    b = _account(db, "b", BOB, secondary=ALICE)
    set_member_password(db, ALICE, "pw-long-enough-1")
    scope = erasure_scope(db, ALICE)
    assert [x["id"] for x in scope["primary"]] == [a]
    assert [x["id"] for x in scope["secondary"]] == [b]
    assert scope["login"] is True


def test_nothing_held_is_refused(db):
    with pytest.raises(ValueError, match="nothing is held"):
        erase_member(db, "nobody@example.com", actor=None)


# --- hard delete, now -------------------------------------------------------------------

def test_erasure_removes_credentials_and_every_record_immediately(db):
    a = _account(db, "a", ALICE, phone="+1")
    _history(db, a)
    set_member_password(db, ALICE, "pw-long-enough-1")
    before = _counts(db, a)
    assert before["events"] >= 1 and before["alerts"] == 1
    gap = erase_member(db, ALICE, actor=ALICE)
    after = _counts(db, a)
    assert after["alerts"] == 0 and after["sms_deliveries"] == 0 and after["known_addresses"] == 0
    assert after["events"] == 1, "exactly one event remains: the gap"
    with db.cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE id = ?", (a,))
        row = cur.fetchone()
        cur.execute("SELECT count(*) AS n FROM portal_members WHERE email = ?", (ALICE,))
        login = cur.fetchone()["n"]
    assert row["encrypted_api_key"] == "" and row["encrypted_secret"] == ""
    assert row["alert_email"] is None and row["alert_phone"] is None
    assert row["label"] == f"erased-{a}" and row["status"] == "disabled" and row["erased_at"]
    assert login == 0
    assert gap["rows_removed"]["alerts"] == 1 and gap["rows_removed"]["portal_members"] == 1


def test_the_gap_is_recorded_as_a_gap_on_the_tombstone(db):
    a = _account(db, "a", ALICE)
    _history(db, a)
    erase_member(db, ALICE, actor="erwin")
    with db.cursor() as cur:
        cur.execute("SELECT event_type, payload_json, actor FROM events WHERE account_id = ?", (a,))
        rows = cur.fetchall()
    assert len(rows) == 1 and rows[0]["event_type"] == "data_erased" and rows[0]["actor"] == "erwin"
    payload = json.loads(rows[0]["payload_json"])
    assert payload["gap"] is True
    assert "cannot be reconstructed" in payload["note"]
    assert payload["rows_removed"]["alerts"] == 1 and payload["accounts"][0]["label"] == "a"


def test_a_second_contact_is_removed_not_the_primarys_data(db):
    b = _account(db, "b", BOB, secondary=ALICE)
    _history(db, b)
    set_member_password(db, ALICE, "pw-long-enough-1")
    erase_member(db, ALICE, actor=ALICE)
    with db.cursor() as cur:
        cur.execute("SELECT alert_email, alert_email_secondary, encrypted_api_key, erased_at FROM accounts WHERE id = ?", (b,))
        row = cur.fetchone()
        cur.execute("SELECT count(*) AS n FROM alerts WHERE account_id = ?", (b,))
        alerts = cur.fetchone()["n"]
        cur.execute("SELECT event_type, payload_json FROM events WHERE account_id = ? ORDER BY id DESC LIMIT 1", (b,))
        last = cur.fetchone()
    assert row["alert_email"] == BOB and row["alert_email_secondary"] is None
    assert row["encrypted_api_key"] == "K" and row["erased_at"] is None
    assert alerts == 1, "Bob's history is Bob's"
    assert last["event_type"] == "alert_contact_updated" and json.loads(last["payload_json"])["secondary_removed"]


def test_the_customer_is_told_in_writing_with_what_went_and_what_remains(db):
    a = _account(db, "a", ALICE)
    _history(db, a)
    told = []
    erase_member(db, ALICE, actor=ALICE, notify=lambda address, gap: told.append((address, confirmation_text(gap))))
    assert told and told[0][0] == ALICE
    text = told[0][1]
    assert "has been deleted" in text and "What remains" in text and "cannot be reconstructed" in text.lower() or "reconstructed" in text


def test_a_failing_confirmation_does_not_undo_the_erasure(db):
    a = _account(db, "a", ALICE)

    def boom(address, gap):
        raise RuntimeError("smtp down")

    gap = erase_member(db, ALICE, actor=ALICE, notify=boom)
    assert gap["gap"] is True
    with db.cursor() as cur:
        cur.execute("SELECT erased_at FROM accounts WHERE id = ?", (a,))
        assert cur.fetchone()["erased_at"]


def test_an_erased_account_is_never_polled_and_never_erasable_twice(db):
    a = _account(db, "a", ALICE)
    erase_member(db, ALICE, actor=ALICE)
    assert erasure_scope(db, ALICE) == {"primary": [], "secondary": [], "login": False}
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM accounts WHERE status = 'active' AND id = ?", (a,))
        assert cur.fetchone()["n"] == 0


# --- download first ------------------------------------------------------------------------

def test_export_carries_the_persons_own_accounts_and_never_credentials(db):
    a = _account(db, "a", ALICE)
    _account(db, "b", BOB, secondary=ALICE)
    _history(db, a)
    set_member_password(db, ALICE, "pw-long-enough-1")
    data = export_member_data(db, ALICE)
    assert [x["id"] for x in data["accounts"]] == [a]
    assert data["accounts"][0]["alerts"] and data["accounts"][0]["events"]
    assert "encrypted" not in json.dumps(data)
    assert data["secondary_contact_on"][0]["label"] == "b"


# --- the portal ---------------------------------------------------------------------------------

def test_the_portal_requires_the_password_then_deletes_and_ends_the_session(db, monkeypatch):
    from tests.test_incident_page import _login, _request

    a = _account(db, "a", ALICE)
    _history(db, a)
    set_member_password(db, ALICE, "pw-long-enough-1")
    from sentinel.portal import serve_portal

    sent = []
    monkeypatch.setattr("sentinel.alerts.contact.ContactRouter.send",
                        lambda self, det, label, emails, phones, **kw: sent.append((emails, det.message)))
    srv = serve_portal(db, 0)
    port = srv.server_address[1]
    try:
        token = _login(("127.0.0.1", port), ALICE, "pw-long-enough-1")
        status, body = _request(("127.0.0.1", port), "/security", cookie=token)
        assert "Delete my data" in body and 'href="/export.json"' in body
        status, body = _request(("127.0.0.1", port), "/export.json", cookie=token)
        assert status == 200 and '"accounts"' in body
        import http.client
        import urllib.parse

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/erase", urllib.parse.urlencode({"password": "wrong", "confirm": "yes"}),
                     {"Content-Type": "application/x-www-form-urlencoded", "Cookie": f"minas_portal={token}"})
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 401
        conn.request("POST", "/erase", urllib.parse.urlencode({"password": "pw-long-enough-1", "confirm": "yes"}),
                     {"Content-Type": "application/x-www-form-urlencoded", "Cookie": f"minas_portal={token}"})
        resp = conn.getresponse()
        page = resp.read().decode()
        assert resp.status == 200 and "Your data has been deleted" in page
        status, _ = _request(("127.0.0.1", port), "/security", cookie=token)
        assert status != 200 or "Delete my data" not in _
    finally:
        srv.shutdown()
    assert sent and sent[0][0] == [ALICE] and "has been deleted" in sent[0][1]
    assert erasure_scope(db, ALICE)["login"] is False


def test_the_cli_erase_needs_a_name_and_confirmation(db, capsys, monkeypatch):
    import argparse
    import os

    from sentinel.cli import cmd_erase
    from sentinel.operators import create_operator

    _account(db, "a", ALICE)
    create_operator(db, "erwin", "correct-horse-battery")
    db.close()
    os.environ["SENTINEL_DB_PATH"] = str(db._path)
    assert cmd_erase(argparse.Namespace(email=ALICE, as_operator=None, yes=False)) == 2
    monkeypatch.setattr("builtins.input", lambda *_: "no")
    assert cmd_erase(argparse.Namespace(email=ALICE, as_operator="erwin", yes=False)) == 1
    assert "not erased" in capsys.readouterr().err
