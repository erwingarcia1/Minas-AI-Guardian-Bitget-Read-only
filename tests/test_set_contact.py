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


# --- #91: the emergency contact has a supported write path -------------------
#
# Until #91 the secondary was written once at onboarding and never again; a
# real person's removal request was honoured by SQL against production,
# unattributed by necessity. These pin the supported path: set, clear,
# diff-recorded, no-op-safe - and that touching the secondary can never
# disturb where the alerts themselves go.

def _seed_secondary(db, label, email="sec@y.com", phone=None):
    with db.cursor() as cur:
        cur.execute(
            "UPDATE accounts SET alert_email_secondary = ?, alert_phone_secondary = ? "
            "WHERE label = ?", (email, phone, label))


def _contacts(db, label):
    with db.cursor() as cur:
        cur.execute(
            "SELECT alert_email, alert_phone, alert_email_secondary, alert_phone_secondary "
            "FROM accounts WHERE label = ?", (label,))
        return dict(cur.fetchone())


def test_secondary_contact_can_be_set_and_is_recorded(db, monkeypatch):
    account_id = make_account(db, "acct-s1")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    rc = cmd_set_contact(_args(label="acct-s1", alert_email_secondary="kin@y.com",
                               alert_phone_secondary="+15550002222"))
    assert rc == 0
    row = _contacts(db, "acct-s1")
    assert row["alert_email_secondary"] == "kin@y.com"
    assert row["alert_phone_secondary"] == "+15550002222"
    events = _events(db, account_id)
    assert events[-1][0] == "secondary_contact_updated"
    assert events[-1][1] == {"email_from": None, "email_to": "kin@y.com",
                             "phone_from": None, "phone_to": "+15550002222"}


def test_a_secondary_change_cannot_disturb_the_primary(db, monkeypatch):
    """The criterion. Removing an emergency contact must not move where the
    alerts themselves go - the two destinations change only on their own
    flags."""
    make_account(db, "acct-s2")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-s2", alert_email="owner@y.com",
                          alert_phone="+15550003333"))
    _seed_secondary(db, "acct-s2")
    cmd_set_contact(_args(label="acct-s2", clear_secondary=True))
    row = _contacts(db, "acct-s2")
    assert row["alert_email"] == "owner@y.com", "clearing the secondary moved the primary"
    assert row["alert_phone"] == "+15550003333"
    assert row["alert_email_secondary"] is None
    assert row["alert_phone_secondary"] is None


def test_clearing_the_secondary_records_who_was_removed(db, monkeypatch):
    """The azdatoo case: the removal is exactly the change a customer reads
    /activity to find, so the event carries the removed address."""
    account_id = make_account(db, "acct-s3")
    _seed_secondary(db, "acct-s3", email="azdatoo@example.invalid")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-s3", clear_secondary=True))
    events = _events(db, account_id)
    assert events[-1][0] == "secondary_contact_updated"
    assert events[-1][1]["email_from"] == "azdatoo@example.invalid"
    assert events[-1][1]["email_to"] is None


def test_rerunning_the_same_secondary_records_nothing(db, monkeypatch):
    account_id = make_account(db, "acct-s4")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-s4", alert_email_secondary="kin@y.com"))
    cmd_set_contact(_args(label="acct-s4", alert_email_secondary="kin@y.com"))
    assert len(_events(db, account_id)) == 1


def test_an_invocation_with_no_flags_changes_nothing(db, monkeypatch):
    """Bare `set-contact --label x` used to CLEAR the primary - a footgun,
    removed with #91. Nothing requested, nothing changed, nothing recorded."""
    account_id = make_account(db, "acct-s5")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-s5", alert_email="keep@y.com"))
    rc = cmd_set_contact(_args(label="acct-s5"))
    assert rc == 0
    assert _contacts(db, "acct-s5")["alert_email"] == "keep@y.com"
    assert len(_events(db, account_id)) == 1


def test_secondary_events_reach_the_portal_activity_page():
    """Registration half: the event type must be in the /activity allowlist
    with a customer-readable label, or the removal is invisible on the one
    page built to reveal it."""
    from sentinel.events import ADMIN_EVENT_LABELS, ADMIN_EVENT_TYPES
    assert "secondary_contact_updated" in ADMIN_EVENT_TYPES
    assert ADMIN_EVENT_LABELS["secondary_contact_updated"] == "Emergency contact changed"


# --- the --as gate (#80's pattern) -------------------------------------------

def test_once_operators_exist_the_change_requires_a_name(db, monkeypatch):
    from sentinel.operators import create_operator
    account_id = make_account(db, "acct-s6")
    _seed_secondary(db, "acct-s6")
    create_operator(db, "erwin", "a-long-enough-password-1")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))

    rc = cmd_set_contact(_args(label="acct-s6", clear_secondary=True))
    assert rc == 2, "operators exist - an unattributed change must be refused"
    assert _contacts(db, "acct-s6")["alert_email_secondary"] == "sec@y.com", (
        "the refusal must leave the row untouched")
    assert _events(db, account_id) == []

    rc = cmd_set_contact(_args(label="acct-s6", clear_secondary=True, as_operator="erwin"))
    assert rc == 0
    events = _events(db, account_id)
    assert events[-1][0] == "secondary_contact_updated"
    assert events[-1][2] == "erwin", "the event must carry the operator's name"


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
