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
