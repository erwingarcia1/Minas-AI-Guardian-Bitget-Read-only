"""#81 / decision 12: the host timer that consumes the health verdict
nothing consumed for six weeks - with the cap that keeps a flap visible.

The real script runs against a stub `docker` whose health answer and
restart record the test controls. What these pin: one restart per
incident with the health state logged; a second unhealthy verdict inside
the cap window goes LOUD once (host log at err, motd marker) and stops
restarting; recovery resets the ladder but leaves the motd evidence; a
failed restart is loud without burning the cap.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "host" / "restart-on-unhealthy.sh"

DOCKER_STUB = """#!/usr/bin/env bash
# `docker compose -f X ps -q S` -> a container id; `docker inspect` -> the
# scripted health; `docker compose -f X restart S` -> recorded, exit per env.
case "$1" in
  compose)
    case "$3 $4" in
      *"ps"*) cat "$STUB_DIR/cid" 2>/dev/null || echo "cid123" ;;
    esac
    for a in "$@"; do
      if [ "$a" = restart ]; then
        echo "restart $(date -u +%s)" >> "$STUB_DIR/restarts"
        exit "${STUB_RESTART_EXIT:-0}"
      fi
    done
    ;;
  inspect)
    case "$*" in
      *json*) echo '{"Status":"unhealthy","FailingStreak":7}' ;;
      *) cat "$STUB_DIR/health" ;;
    esac
    ;;
esac
"""

LOGGER_STUB = """#!/usr/bin/env bash
echo "$@" >> "$STUB_DIR/journal"
"""


@pytest.fixture
def host(tmp_path):
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    (stub_bin / "docker").write_text(DOCKER_STUB)
    (stub_bin / "docker").chmod(0o755)
    (stub_bin / "logger").write_text(LOGGER_STUB)
    (stub_bin / "logger").chmod(0o755)
    for tool in ("bash", "date", "mkdir", "printf", "dirname", "cat"):
        real = shutil.which(tool)
        assert real, f"{tool} missing from the host"
        if not (stub_bin / tool).exists():
            (stub_bin / tool).symlink_to(real)

    (tmp_path / "health").write_text("unhealthy\n")
    env = {
        "PATH": str(stub_bin),
        "STUB_DIR": str(tmp_path),
        "MINAS_COMPOSE_FILE": str(tmp_path / "compose.yml"),
        "MINAS_STATE_FILE": str(tmp_path / "state"),
        "MINAS_HOST_LOG": str(tmp_path / "restarts.log"),
        "MINAS_MOTD_FILE": str(tmp_path / "motd"),
        "MINAS_CAP_WINDOW_SECONDS": "1800",
    }
    return {"env": env, "dir": tmp_path}


def _tick(host, **env_extra):
    env = {**host["env"], **env_extra}
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                          env=env, timeout=30)


def _restarts(host):
    f = host["dir"] / "restarts"
    return len(f.read_text().splitlines()) if f.exists() else 0


def test_an_unhealthy_container_is_restarted_once_with_the_health_state_logged(host):
    result = _tick(host)
    assert result.returncode == 0, result.stderr
    assert _restarts(host) == 1
    log = (host["dir"] / "restarts.log").read_text()
    assert "unhealthy - restarting" in log
    assert "FailingStreak" in log, "the health state that triggered it is the evidence"
    assert "restart of 'sentinel' completed" in log


def test_a_second_unhealthy_inside_the_window_goes_loud_once_and_stops_restarting(host):
    _tick(host)
    _tick(host)   # still unhealthy, seconds later - the restart did not cure
    assert _restarts(host) == 1, "the cap must stop the second restart"
    log = (host["dir"] / "restarts.log").read_text()
    assert "RESTART CAP REACHED" in log
    assert "A human is needed" in log
    motd = (host["dir"] / "motd").read_text()
    assert "restart cap reached" in motd
    assert "journalctl -t minas-restart-on-unhealthy" in motd, (
        "the marker must say where the record lives")

    _tick(host)   # third tick: already loud once - silence, not a drumbeat
    assert _restarts(host) == 1
    assert (host["dir"] / "restarts.log").read_text().count("RESTART CAP REACHED") == 1


def test_recovery_resets_the_ladder_but_keeps_the_motd_evidence(host):
    _tick(host)
    _tick(host)   # capped
    (host["dir"] / "health").write_text("healthy\n")
    _tick(host)   # recovery observed
    log = (host["dir"] / "restarts.log").read_text()
    assert "clearing state" in log
    assert "motd marker left for review" in log
    assert "restart cap reached" in (host["dir"] / "motd").read_text(), (
        "evidence of a flap is evidence - a human removes it, not the script")

    (host["dir"] / "health").write_text("unhealthy\n")
    _tick(host)   # a NEW incident gets its one restart again
    assert _restarts(host) == 2


def test_an_expired_window_allows_the_next_restart_without_the_cap(host):
    _tick(host)
    (host["dir"] / "state").write_text("LAST_RESTART=1\nCAPPED=0\n")  # long ago
    _tick(host)
    assert _restarts(host) == 2
    assert "RESTART CAP REACHED" not in (host["dir"] / "restarts.log").read_text()


def test_a_failed_restart_is_loud_but_does_not_burn_the_cap(host):
    result = _tick(host, STUB_RESTART_EXIT="1")
    assert result.returncode == 0, result.stderr
    log = (host["dir"] / "restarts.log").read_text()
    assert "RESTART FAILED" in log
    assert "A human is needed" in log
    state = (host["dir"] / "state").read_text() if (host["dir"] / "state").exists() else ""
    assert "CAPPED=1" not in state, (
        "a failed command may succeed next tick - the cap is for cures that don't hold")


def test_a_healthy_container_is_left_alone(host):
    (host["dir"] / "health").write_text("healthy\n")
    result = _tick(host)
    assert result.returncode == 0, result.stderr
    assert _restarts(host) == 0
    assert not (host["dir"] / "motd").exists()


def test_every_line_reaches_journald_as_well(host):
    _tick(host)
    journal = (host["dir"] / "journal").read_text()
    assert "minas-restart-on-unhealthy" in journal
    assert "restarting" in journal


def test_a_container_recreation_does_not_clear_the_cap(host):
    """Journald, 2026-08-30, replayed exactly: restart -> capped ("A human
    is needed") -> the deploy's NEW container reports 'starting' -> the old
    branch cleared the state here and a second restart followed two minutes
    later. Now: 'starting' on a new id keeps the cap (and says the container
    was replaced); a further unhealthy verdict stays capped with no second
    restart; only a HEALTHY verdict clears."""
    _tick(host)                                   # 02:36:47 restart
    _tick(host)                                   # 02:38:59 capped
    assert _restarts(host) == 1
    assert "RESTART CAP REACHED" in (host["dir"] / "restarts.log").read_text()

    (host["dir"] / "cid").write_text("cid456\n")   # the deploy replaced the container
    (host["dir"] / "health").write_text("starting\n")
    _tick(host)                                   # 02:43:09 'starting' on the new id
    log = (host["dir"] / "restarts.log").read_text()
    assert "REPLACED" in log and "keeping the cap" in log
    assert "clearing state" not in log, "'starting' is not recovery"

    (host["dir"] / "health").write_text("unhealthy\n")
    _tick(host)                                   # 02:45:13 would have been the second restart
    assert _restarts(host) == 1, "the cap must hold across a recreation"

    (host["dir"] / "health").write_text("healthy\n")
    _tick(host)                                   # actual recovery
    assert "clearing state" in (host["dir"] / "restarts.log").read_text()
    (host["dir"] / "health").write_text("unhealthy\n")
    _tick(host)                                   # a NEW incident gets its one restart
    assert _restarts(host) == 2


def test_starting_without_a_replacement_neither_clears_nor_logs(host):
    _tick(host)
    _tick(host)   # capped
    (host["dir"] / "health").write_text("starting\n")
    before = (host["dir"] / "restarts.log").read_text()
    _tick(host)
    after = (host["dir"] / "restarts.log").read_text()
    assert before == after, "'starting' on the same container decides nothing"
    (host["dir"] / "health").write_text("unhealthy\n")
    _tick(host)
    assert _restarts(host) == 1
