from __future__ import annotations

import argparse

from sentinel.cli import cmd_set_contact
from tests.conftest import make_account


def _args(**kw):
    ns = argparse.Namespace(label=None, alert_email=None, alert_phone=None, clear=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_set_contact_updates_existing_account(db, cipher, monkeypatch, tmp_path):
    account_id = make_account(db, "acct-c")
    # cmd_set_contact opens its own Database from env; point it at the same file.
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    rc = cmd_set_contact(_args(label="acct-c", alert_email="x@y.com", alert_phone="+15550001111"))
    assert rc == 0
    with db.cursor() as cur:
        cur.execute("SELECT alert_email, alert_phone FROM accounts WHERE id = ?", (account_id,))
        row = cur.fetchone()
    assert row["alert_email"] == "x@y.com"
    assert row["alert_phone"] == "+15550001111"


def test_set_contact_clear(db, cipher, monkeypatch):
    make_account(db, "acct-d")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-d", alert_email="a@b.com"))
    cmd_set_contact(_args(label="acct-d", clear=True))
    with db.cursor() as cur:
        cur.execute("SELECT alert_email, alert_phone FROM accounts WHERE label = 'acct-d'")
        row = cur.fetchone()
    assert row["alert_email"] is None and row["alert_phone"] is None


def test_set_contact_unknown_account_returns_error(db, monkeypatch):
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    assert cmd_set_contact(_args(label="nope", alert_email="x@y.com")) == 1


# --- the audit trail (G-32, #36) ----------------------------------------------
#
# Where the alerts about an account go is the setting an attacker changes
# FIRST: redirect the alerts, then drain the account. Until #36 this command
# rewrote the destination and left no trace at all. The tests below pin that a
# change leaves an event row - and that a non-change doesn't, because a
# forensic log that records re-runs as changes is a log nobody can read.

def _events(db, account_id):
    import json

    with db.cursor() as cur:
        cur.execute(
            "SELECT event_type, payload_json, actor FROM events "
            "WHERE account_id = ? ORDER BY id",
            (account_id,),
        )
        return [(r["event_type"], json.loads(r["payload_json"]), r["actor"])
                for r in cur.fetchall()]


def test_contact_change_is_recorded_with_old_and_new(db, monkeypatch):
    account_id = make_account(db, "acct-e")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-e", alert_email="old@y.com"))
    cmd_set_contact(_args(label="acct-e", alert_email="new@y.com", alert_phone="+15550001111"))
    events = _events(db, account_id)
    assert [e[0] for e in events] == ["alert_contact_updated", "alert_contact_updated"]
    assert events[1][1] == {
        "email_from": "old@y.com", "email_to": "new@y.com",
        "phone_from": None, "phone_to": "+15550001111",
    }
    # The CLI carries no operator identity; the row says so rather than guessing.
    assert events[1][2] is None


def test_rerunning_the_same_values_records_nothing(db, monkeypatch):
    account_id = make_account(db, "acct-f")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-f", alert_email="same@y.com"))
    cmd_set_contact(_args(label="acct-f", alert_email="same@y.com"))
    assert len(_events(db, account_id)) == 1


def test_clearing_contacts_is_recorded(db, monkeypatch):
    account_id = make_account(db, "acct-g")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-g", alert_email="a@b.com"))
    cmd_set_contact(_args(label="acct-g", clear=True))
    events = _events(db, account_id)
    assert events[-1][0] == "alert_contact_updated"
    assert events[-1][1]["email_from"] == "a@b.com"
    assert events[-1][1]["email_to"] is None


def test_sms_toggle_is_recorded_once_per_actual_change(db, monkeypatch):
    account_id = make_account(db, "acct-h")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-h", sms_alerts="off"))
    cmd_set_contact(_args(label="acct-h", sms_alerts="off"))  # not a change
    cmd_set_contact(_args(label="acct-h", sms_alerts="on"))
    events = [e for e in _events(db, account_id) if e[0] == "sms_alerts_changed"]
    assert [e[1] for e in events] == [
        {"from": True, "to": False},
        {"from": False, "to": True},
    ]
