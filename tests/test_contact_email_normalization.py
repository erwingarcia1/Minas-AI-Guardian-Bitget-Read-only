"""#90: contact columns store the canonical form - emails lowercased, NULL
as the one absent value - enforced at the write boundary, not remembered at
every read.

Production grew `azdatoo@gmail.com` on four accounts and `AZDATOO@GMAIL.COM`
on a fifth because writes stored the as-typed casing; every read that
behaved did so only because someone remembered `lower()` at that call site.
The guard pair per house rules: a registration test that every writer of the
contact columns goes through the canonicalizer, and execution tests that a
mixed-case write round-trips to one canonical form - plus the migration for
rows written before the boundary existed, and the case-insensitive
duplicate-drop in ContactRouter.
"""
from __future__ import annotations

import argparse
import inspect
import re
import sqlite3
from pathlib import Path

import pytest

from sentinel.cli import cmd_set_contact
from tests.conftest import make_account

ROOT = Path(__file__).resolve().parent.parent


def _args(**kw):
    ns = argparse.Namespace(label=None, alert_email=None, alert_phone=None, clear=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


@pytest.fixture
def quiet_notices(monkeypatch):
    """The #89 welcome note and #36 destination notice fire on these writes;
    swallow them - delivery is not what this module tests."""
    import sentinel.alerts.contact as contact_module

    class _Null:
        def __init__(self, *a, **kw):
            pass

        def send(self, *a, **kw):
            pass

    monkeypatch.setattr(contact_module, "ContactRouter", _Null)


def _row(db, label):
    with db.cursor() as cur:
        cur.execute(
            "SELECT alert_email, alert_phone, alert_email_secondary, alert_phone_secondary "
            "FROM accounts WHERE label = ?", (label,))
        return dict(cur.fetchone())


# --- registration: every writer goes through the canonicalizer ----------------

def test_every_writer_of_the_contact_columns_canonicalizes():
    """Mechanical, so the NEXT writer cannot ship without it: any product
    file whose SQL writes a contact email column must call
    canonical_contact_email, and must be one this test already knows about -
    a new writer fails here until it normalizes and is listed."""
    # db.py is listed because its migration UPDATEs match the writer shape -
    # and they ARE canonicalization (NULLIF(lower(trim(...)))), checked below.
    # erasure.py is listed because its tombstone UPDATE matches the writer
    # shape - and it only ever writes NULL (#46), which needs no normalizing.
    KNOWN_WRITERS = {"sentinel/onboarding.py", "sentinel/cli.py", "sentinel/db.py", "sentinel/erasure.py"}
    write_stmt = re.compile(
        r"(INSERT INTO accounts|UPDATE accounts SET)[^\"]*alert_email", re.S)

    found = set()
    for path in (ROOT / "sentinel").rglob("*.py"):
        text = path.read_text()
        if write_stmt.search(text):
            found.add(str(path.relative_to(ROOT)))
            assert ("canonical_contact_email" in text or "NULLIF(lower(trim(" in text
                    or "alert_email = NULL" in text), (
                f"{path.name} writes a contact email column without the "
                f"canonicalizer - the compare-side-only trap #90 closed")
    assert found == KNOWN_WRITERS, (
        "the set of contact-column writers changed - normalize in the new "
        f"writer and update this list. Found: {sorted(found)}")


def test_the_migration_statements_exist_for_every_contact_column():
    """The boundary fixes new writes; the migration fixes the rows already
    written (azdatoo's five). Both halves, or the mixed state persists."""
    from sentinel.db import Database
    src = inspect.getsource(Database)
    for col in ("alert_email", "alert_email_secondary",
                "alert_phone", "alert_phone_secondary"):
        assert re.search(rf"UPDATE accounts SET {col} = NULLIF\(", src), (
            f"no canonicalizing migration for {col}")


# --- execution: writes round-trip to canonical --------------------------------

def test_onboarding_stores_canonical_email_and_null_for_blank(db, cipher, monkeypatch, quiet_notices):
    from tests.test_onboarding import _client_with, account_info_response

    monkeypatch.setattr(
        "sentinel.onboarding.make_client",
        lambda *a, **kw: _client_with(
            account_info_result=account_info_response(authorities="spot_account_r")),
    )
    from sentinel.onboarding import onboard_account

    onboard_account(
        db, cipher, label="acct-n1", topology="independent",
        api_key="k", secret="s", passphrase="p",
        alert_email="  MiXeD@Example.COM  ", alert_phone="",
        alert_email_secondary="", alert_phone_secondary="  ",
    )
    row = _row(db, "acct-n1")
    assert row["alert_email"] == "mixed@example.com"
    assert row["alert_phone"] is None, "'' stored as a value is the falsy-sentinel trap"
    assert row["alert_email_secondary"] is None
    assert row["alert_phone_secondary"] is None


def test_set_contact_stores_canonical_for_both_destinations(db, monkeypatch, quiet_notices):
    make_account(db, "acct-n2")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-n2", alert_email="OWNER@X.com",
                          alert_email_secondary=" KIN@X.com "))
    row = _row(db, "acct-n2")
    assert row["alert_email"] == "owner@x.com"
    assert row["alert_email_secondary"] == "kin@x.com"


def test_retyping_the_same_address_in_a_different_case_is_not_a_change(db, monkeypatch, quiet_notices):
    """The trap named in the issue: the diff runs on canonical values, so a
    casing difference cannot manufacture a forensic row - or a second #89
    welcome note."""
    import json

    account_id = make_account(db, "acct-n3")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-n3", alert_email_secondary="kin@x.com"))
    cmd_set_contact(_args(label="acct-n3", alert_email_secondary="KIN@X.COM"))
    with db.cursor() as cur:
        cur.execute("SELECT payload_json FROM events WHERE account_id = ?", (account_id,))
        rows = cur.fetchall()
    assert len(rows) == 1, "a casing difference manufactured a change"
    assert json.loads(rows[0]["payload_json"])["email_to"] == "kin@x.com"


def test_setting_an_empty_string_stores_null(db, monkeypatch, quiet_notices):
    make_account(db, "acct-n4")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-n4", alert_email_secondary="kin@x.com"))
    cmd_set_contact(_args(label="acct-n4", alert_email_secondary=""))
    assert _row(db, "acct-n4")["alert_email_secondary"] is None


# --- the migration: rows written before the boundary --------------------------

def test_reopening_the_database_canonicalizes_existing_rows(tmp_path):
    """The azdatoo shape, replayed: mixed-case and ''-as-absent rows written
    under the old code become canonical on the next open - the migration is
    what reaches production's five accounts at the next deploy."""
    from sentinel.db import Database

    path = tmp_path / "legacy.db"
    first = Database(str(path))
    with first.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, "
            "encrypted_secret, encrypted_passphrase, alert_email, alert_phone, "
            "alert_email_secondary, status, onboarded_at, updated_at) "
            "VALUES ('legacy', 'bitget', 'independent', 'x', 'y', 'z', "
            "'AZDATOO@GMAIL.COM', '', ' Kin@X.com ', 'active', '2026-01-01', '2026-01-01')")
    first.close()

    reopened = Database(str(path))
    with reopened.cursor() as cur:
        cur.execute("SELECT alert_email, alert_phone, alert_email_secondary "
                    "FROM accounts WHERE label = 'legacy'")
        row = dict(cur.fetchone())
    reopened.close()
    assert row["alert_email"] == "azdatoo@gmail.com"
    assert row["alert_phone"] is None
    assert row["alert_email_secondary"] == "kin@x.com"


def test_the_migration_is_idempotent(tmp_path):
    from sentinel.db import Database

    path = tmp_path / "twice.db"
    Database(str(path)).close()
    Database(str(path)).close()  # a second open must not error or change shape
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    conn.close()


# --- ContactRouter: the latent case-sensitive comparison ----------------------

def test_router_drops_duplicate_emails_case_insensitively(db, monkeypatch):
    """Finding 2 in the issue: one person listed as primary and secondary in
    different casings must not be mailed twice per alert."""
    import sentinel.alerts.contact as contact_module
    from sentinel.alerts.contact import ContactRouter

    sent_to = []

    class _CaptureChannel:
        def __init__(self, host, port, username, password, sender, recipients, **kw):
            sent_to.append(tuple(recipients))

        def send(self, alert):
            pass

    monkeypatch.setattr(contact_module, "EmailChannel", _CaptureChannel)
    router = ContactRouter(db=db)
    router._smtp = {"host": "h", "port": 25, "username": "u",
                    "password": "p", "sender": "s@x.com"}

    class _Detection:
        rule, severity, confidence, message, evidence = "r", "HIGH", "n/a", "m", {}

    router.send(_Detection(), "acct", ["Azdatoo@Gmail.com", "azdatoo@gmail.com"], [])
    assert sent_to and sent_to[0] == ("Azdatoo@Gmail.com",), (
        "the same address in two casings is one recipient, first casing wins")
