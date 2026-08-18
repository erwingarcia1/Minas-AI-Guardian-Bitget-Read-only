"""#80: G-50 says offboarding is disable, never delete — and nothing wrote
`accounts.status`. The schema declared it, the dashboard styled it, the
poll loops filtered on it, and no code path could ever set it: a policy
with no mechanism. These tests pin the mechanism, and pin it at the level
the defect actually lived — the loops must stop visiting a disabled
account (execution), not merely the column change (registration).
"""
from __future__ import annotations

import pytest

from sentinel.cli import cmd_account_status
from sentinel.config import RulesConfig
from sentinel.engine import Engine
from sentinel.onboarding import set_account_status


def _account(db, label="acct", status="active"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, "
            "encrypted_secret, encrypted_passphrase, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, '2026-01-01', '2026-01-01')",
            (label, status),
        )
        return cur.lastrowid


def _events(db):
    with db.cursor() as cur:
        cur.execute("SELECT event_type, actor FROM events ORDER BY id")
        return [(r["event_type"], r["actor"]) for r in cur.fetchall()]


def _status(db, label):
    with db.cursor() as cur:
        cur.execute("SELECT status FROM accounts WHERE label = ?", (label,))
        return cur.fetchone()["status"]


# --- the mechanism -----------------------------------------------------------

def test_disable_stops_the_loops_visiting_the_account(db, cipher):
    """The execution guard: a disabled account disappears from the scheduler,
    not just from a SELECT a test wrote itself."""
    _account(db)
    engine = Engine(db, cipher, RulesConfig("config/rules.yaml"), {})
    engine._schedule_cycle()
    assert engine._queue.qsize() > 0, "sanity: an active account gets queued"

    assert set_account_status(db, "acct", "disabled", actor=None) is True

    engine2 = Engine(db, cipher, RulesConfig("config/rules.yaml"), {})
    engine2._schedule_cycle()
    assert engine2._queue.qsize() == 0


def test_reenable_restores_polling(db, cipher):
    _account(db, status="disabled")
    set_account_status(db, "acct", "active", actor=None)

    engine = Engine(db, cipher, RulesConfig("config/rules.yaml"), {})
    engine._schedule_cycle()
    assert engine._queue.qsize() > 0


def test_disable_writes_an_attributed_event(db):
    _account(db)
    set_account_status(db, "acct", "disabled", actor="erwin")
    assert ("account_disabled", "erwin") in _events(db)


def test_reenable_writes_its_own_event(db):
    _account(db, status="disabled")
    set_account_status(db, "acct", "active", actor="erwin")
    assert ("account_enabled", "erwin") in _events(db)


def test_disabling_twice_is_a_no_op_with_no_second_event(db):
    """An event log that records a change that did not happen is lying —
    the same rule as G-50's no-backfilled-actors."""
    _account(db)
    assert set_account_status(db, "acct", "disabled", actor="erwin") is True
    assert set_account_status(db, "acct", "disabled", actor="erwin") is False
    assert _events(db).count(("account_disabled", "erwin")) == 1


def test_unknown_label_raises(db):
    with pytest.raises(LookupError):
        set_account_status(db, "nope", "disabled", actor=None)


def test_only_declared_statuses_are_writable(db):
    """The CHECK constraint is the schema's guard; this is the code's — a
    typo'd status must fail before it reaches SQL."""
    _account(db)
    with pytest.raises(ValueError):
        set_account_status(db, "acct", "deleted", actor=None)


# --- the CLI verb ------------------------------------------------------------

class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _run_cli(db, monkeypatch, tmp_path, **kw):
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    return cmd_account_status(_Args(**kw))


def test_cli_disable_and_reenable(db, monkeypatch, tmp_path):
    _account(db)
    assert _run_cli(db, monkeypatch, tmp_path, label="acct", action="disable", as_operator=None) == 0
    assert _status(db, "acct") == "disabled"
    assert _run_cli(db, monkeypatch, tmp_path, label="acct", action="enable", as_operator=None) == 0
    assert _status(db, "acct") == "active"


def test_cli_requires_a_name_once_operators_exist(db, monkeypatch, tmp_path):
    """Same upgrade path as `ack` (G-50): attribution becomes mandatory the
    moment there is somebody to attribute to, and not before."""
    from sentinel.operators import create_operator
    create_operator(db, "erwin", "pw-123456")
    _account(db)

    assert _run_cli(db, monkeypatch, tmp_path, label="acct", action="disable", as_operator=None) == 2
    assert _status(db, "acct") == "active"

    assert _run_cli(db, monkeypatch, tmp_path, label="acct", action="disable", as_operator="erwin") == 0
    assert _status(db, "acct") == "disabled"
    assert ("account_disabled", "erwin") in _events(db)


def test_cli_unknown_label_is_exit_1(db, monkeypatch, tmp_path):
    assert _run_cli(db, monkeypatch, tmp_path, label="ghost", action="disable", as_operator=None) == 1
