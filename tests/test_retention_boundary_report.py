"""#78: the boundary report cried wolf on its first production run.

`prune_all` runs hourly; rows age across their window continuously in
between, so at any instant the oldest surviving row can legitimately be up
to one prune interval past its boundary. The report flagged ANY negative
remainder as PAST and its day-granularity formatting rendered minutes of
overshoot as "PAST 0d" — a day-scale-sounding breach that was steady state.
A tool built to be trusted must not alarm on its own measurement window:
these tests pin the behaviour at exactly the boundary, in both directions.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sentinel.db import Database

ROOT = Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location(
    "retention_boundary", ROOT / "scripts" / "retention-boundary.py")
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)


def _ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


@pytest.fixture
def seeded(tmp_path):
    db = Database(tmp_path / "seed.db")
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, "
            "encrypted_secret, encrypted_passphrase, status, onboarded_at, updated_at) "
            "VALUES ('a', 'bitget', 'independent', 'x', 'y', 'z', 'active', ?, ?)",
            (_ago(days=400), _ago(days=400)),
        )
    db.close()
    return tmp_path / "seed.db"


def _run(db_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["retention-boundary.py", "--db", str(db_path)])
    code = report.main()
    return code, capsys.readouterr().out


def _snapshot(db_path, age: timedelta, coin="BTC"):
    db = Database(db_path)
    with db.cursor() as cur:
        # A fresh newest-per-account row alongside, so the aged row is
        # genuinely prunable rather than the kept baseline.
        cur.execute("INSERT INTO balance_snapshots (account_id, coin, quantity, snapshot_at) "
                    "VALUES (1, ?, '1', ?)", (coin, (datetime.now(timezone.utc) - age).isoformat()))
        cur.execute("INSERT INTO balance_snapshots (account_id, coin, quantity, snapshot_at) "
                    "VALUES (1, ?, '1', ?)", (coin, _ago(minutes=1)))
    db.close()


def test_overshoot_within_one_prune_interval_is_not_a_boundary_crossing(seeded, monkeypatch, capsys):
    """7-day window, row 7d + 30min old, hourly prune: the next pass deletes
    it. Steady state, exit 0 — the first-production-run false alarm."""
    _snapshot(seeded, timedelta(days=7, minutes=30))
    code, out = _run(seeded, monkeypatch, capsys)
    assert code == 0
    assert "PAST" not in out
    assert "within one prune interval" in out


def test_overshoot_beyond_one_prune_interval_is_flagged(seeded, monkeypatch, capsys):
    """The report must still catch a real backlog — tolerance is one prune
    interval, not a blank cheque."""
    _snapshot(seeded, timedelta(days=9))
    code, out = _run(seeded, monkeypatch, capsys)
    assert code == 1
    assert "PAST 2d" in out


def test_subday_overshoot_prints_hours_not_zero_days(seeded, monkeypatch, capsys):
    """'PAST 0d' reads as a day-scale breach rounded down. Past the
    tolerance but under a day must say hours."""
    _snapshot(seeded, timedelta(days=7, hours=5))
    code, out = _run(seeded, monkeypatch, capsys)
    assert code == 1
    assert "PAST 5h" in out
    assert "PAST 0d" not in out


def test_a_row_inside_its_window_is_untouched_by_the_tolerance(seeded, monkeypatch, capsys):
    _snapshot(seeded, timedelta(days=3))
    code, out = _run(seeded, monkeypatch, capsys)
    assert code == 0
    assert "in 4d" in out
