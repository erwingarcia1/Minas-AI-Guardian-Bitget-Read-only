"""The daily database backup (scripts/host/backup.sh; #85; the owner,
2026-10-27: "daily backups that lasts 28 days").

The real script runs here against a stub `docker` on PATH that plays the
container: `compose exec ... python -` runs the script's own Python against
a real SQLite database in tmp_path, `compose cp` copies the file out, and
`compose exec ... rm` removes it. So the snapshot, the integrity check, the
compression, the permissions and the retention all run for real; only the
container boundary is scripted. The refusals are the point: a backup that
keeps a corrupt copy, or a partial one the liveness gate would read as
fresh, is worse than none.
"""
from __future__ import annotations

import gzip
import os
import sqlite3
import stat
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "host" / "backup.sh"

# The container's /tmp is $DOCKER_STUB_DIR/ctmp; its database is
# $DOCKER_STUB_DIR/sentinel.db. DOCKER_STUB_VERDICT, when set, replaces the
# integrity check's answer, playing a copy that came out corrupt.
_DOCKER_STUB = r"""#!/usr/bin/env bash
set -e
[ "$1" = compose ] && shift
[ "$1" = -f ] && shift 2
map() { echo "${1/#\/tmp\//$DOCKER_STUB_DIR/ctmp/}"; }
case "$1" in
  exec)
    shift; [ "$1" = -T ] && shift; shift  # -T, then the service
    if [ "$1" = python ]; then
      if [ -n "${DOCKER_STUB_VERDICT:-}" ]; then cat >/dev/null; echo "$DOCKER_STUB_VERDICT"; exit 0; fi
      SENTINEL_DB_PATH="$DOCKER_STUB_DIR/sentinel.db" exec python3 - "$(map "$3")"
    elif [ "$1" = rm ]; then
      rm -f "$(map "$3")"
    fi ;;
  cp)
    cp "$(map "${2#*:}")" "$3" ;;
esac
"""


@pytest.fixture
def host(tmp_path):
    state = tmp_path / "state"
    (state / "ctmp").mkdir(parents=True)
    db = sqlite3.connect(state / "sentinel.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, label TEXT)")
    db.executemany("INSERT INTO accounts (label) VALUES (?)", [("acct-1",), ("acct-2",)])
    db.commit()
    db.close()
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "docker").write_text(_DOCKER_STUB)
    (stubs / "docker").chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    env = dict(os.environ)
    env.update({
        "PATH": f"{stubs}:{env['PATH']}",
        "DOCKER_STUB_DIR": str(state),
        "MINAS_COMPOSE_FILE": str(tmp_path / "docker-compose.yml"),
        "MINAS_BACKUP_DIR": str(tmp_path / "backups"),
        "MINAS_BACKUP_WORK_DIR": str(work),
    })
    return type("Host", (), {"env": env, "state": state, "backups": tmp_path / "backups", "work": work})


def _run(host, **extra):
    return subprocess.run([str(SCRIPT)], capture_output=True, text=True, timeout=60,
                          env={**host.env, **extra})


def _copies(host):
    return sorted(host.backups.glob("sentinel-*.db.gz"))


def test_a_backup_is_a_whole_readable_copy_of_the_database(host, tmp_path):
    result = _run(host)
    assert result.returncode == 0, result.stderr
    [copy] = _copies(host)
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(copy.read_bytes()))
    rows = sqlite3.connect(restored).execute("SELECT label FROM accounts ORDER BY id").fetchall()
    assert rows == [("acct-1",), ("acct-2",)]
    assert "backup written" in result.stdout and "kept for 28 days" in result.stdout


def test_a_backup_is_readable_by_root_alone(host):
    """It holds every customer's contacts, alerts and encrypted keys."""
    assert _run(host).returncode == 0
    [copy] = _copies(host)
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    assert stat.S_IMODE(host.backups.stat().st_mode) == 0o700


def test_nothing_is_left_behind_in_the_container_or_the_work_area(host):
    assert _run(host).returncode == 0
    assert list((host.state / "ctmp").iterdir()) == []
    assert list(host.work.iterdir()) == []


def test_a_copy_that_fails_its_integrity_check_is_refused_and_nothing_is_kept(host):
    """Nothing partial in the backup directory either: the liveness gate
    counts any file there as a fresh backup."""
    result = _run(host, DOCKER_STUB_VERDICT="*** in database main ***\nPage 7 is never used")
    assert result.returncode != 0
    assert "integrity_check" in result.stderr
    assert not host.backups.exists() or list(host.backups.iterdir()) == []
    assert list(host.work.iterdir()) == []


def test_copies_older_than_28_days_go_and_nothing_else_does(host):
    host.backups.mkdir(mode=0o700)
    day = 24 * 3600
    old = host.backups / "sentinel-2026-01-01T033000Z.db.gz"
    recent = host.backups / "sentinel-2026-01-03T033000Z.db.gz"
    held = host.backups / "held-2026-09-28"
    held.mkdir()
    foreign = [held / "sentinel.db", host.backups / "manual-copy.db.gz"]
    for path, age_days in [(old, 29), (recent, 27), *[(f, 60) for f in foreign]]:
        path.write_bytes(b"x")
        then = time.time() - age_days * day
        os.utime(path, (then, then))
    result = _run(host)
    assert result.returncode == 0, result.stderr
    assert not old.exists(), "a copy older than 28 days is deleted"
    assert recent.exists(), "a copy younger than 28 days is kept"
    assert all(f.exists() for f in foreign), "files this script did not name are never touched"
    assert f"deleted (older than retention): {old}" in result.stdout
    assert len(_copies(host)) == 2


def test_the_retention_is_28_days_unless_overridden():
    source = SCRIPT.read_text()
    assert 'KEEP_DAYS="${MINAS_BACKUP_KEEP_DAYS:-28}"' in source


def test_the_timer_runs_the_script_daily_and_catches_up():
    service = (ROOT / "scripts" / "host" / "minas-backup.service").read_text()
    timer = (ROOT / "scripts" / "host" / "minas-backup.timer").read_text()
    assert "ExecStart=/opt/minas-guardian/scripts/host/backup.sh" in service
    assert "OnCalendar=*-*-* 03:30:00 UTC" in timer
    assert "Persistent=true" in timer and "Unit=minas-backup.service" in timer
    assert os.access(SCRIPT, os.X_OK)
