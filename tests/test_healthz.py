"""/healthz (v1.11.2 batch, item 1): container liveness that means something.

The old compose healthcheck fetched the ops dashboard root unauthenticated.
It passed only because _gate waived sign-in on an empty operator table -
closing that hole turned the container unhealthy the moment the first
operator was created (2026-08-30), and the restart timer has been stopped
by hand since. And it never asserted the engine was alive: the dashboard
thread answered right through #79's six dead weeks.

/healthz is exempt from the operator gate BY DESIGN, carries no data, and
answers 200 only when the ENGINE completed a scheduler cycle within
3 x polling.fast_loop_seconds - the cycle cadence itself, never a literal.
Three directions: reachable with no session; every other path still
refused; a wedged engine turns the probe stale.
"""
from __future__ import annotations

import http.client
from datetime import datetime, timedelta, timezone

import pytest

from sentinel.config import RulesConfig
from sentinel.dashboard import serve_dashboard
from sentinel.engine import Engine


@pytest.fixture
def dash(db, cipher):
    srv = serve_dashboard(db, cipher, 0, rules=RulesConfig("config/rules.yaml"))
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, body


def _stamp(db, age_seconds):
    when = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    with db.cursor() as cur:
        cur.execute("INSERT INTO portal_meta (key, value) VALUES ('engine:last_cycle', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (when,))


# --- direction 1: reachable with no session, carrying nothing ---------------------------------

def test_healthz_answers_with_no_session_and_no_data(db, cipher, dash):
    _stamp(db, age_seconds=10)
    status, body = _get(dash, "/healthz")
    assert status == 200 and body == "ok\n"
    # no operator exists and no cookie was sent - and nothing leaked:
    words = body.lower()
    assert "account" not in words and "alert" not in words and "@" not in words


def test_a_fresh_engine_cycle_reads_ok_and_a_missing_stamp_reads_stale(db, cipher, dash):
    status, body = _get(dash, "/healthz")
    assert (status, body) == (503, "stale\n"), "no stamp ever written = the engine never completed a cycle"
    _stamp(db, age_seconds=1)
    assert _get(dash, "/healthz") == (200, "ok\n")


# --- direction 2: every other dashboard path is still refused ---------------------------------

def test_every_other_get_path_is_still_behind_the_gate(db, cipher, dash):
    from sentinel.operators import create_operator

    create_operator(db, "erwin", "correct-horse-battery")
    for path in ("/", "/metrics", "/metrics/raw", "/triage", "/add", "/account?id=1"):
        status, body = _get(dash, path)
        assert status == 401, f"{path} answered {status} without a session"
        assert "Operator sign-in" in body
    assert _get(dash, "/healthz")[0] in (200, 503), "the exemption is /healthz alone"


# --- direction 3: a wedged engine turns the container unhealthy --------------------------------

def test_a_wedged_engine_reads_stale_at_three_cycles_and_the_threshold_is_the_cadence(db, cipher, dash):
    """The stamp ages past 3 x polling.fast_loop_seconds and /healthz - the
    exact URL the compose healthcheck now probes - goes 503. The threshold
    is read from the running config: with the shipped 45 s the boundary is
    135 s, and a config with a different cadence moves it."""
    seconds = RulesConfig("config/rules.yaml").get_seconds("polling", "fast_loop_seconds")
    _stamp(db, age_seconds=3 * seconds - 5)
    assert _get(dash, "/healthz")[0] == 200, "two missed beats is a slow pass, not a wedge"
    _stamp(db, age_seconds=3 * seconds + 5)
    status, body = _get(dash, "/healthz")
    assert (status, body) == (503, "stale\n"), "three missed beats is a wedge"


def test_the_engine_stamps_the_cycle_and_a_raising_tick_does_not(db, cipher, monkeypatch):
    engine = Engine(db, cipher, RulesConfig("config/rules.yaml"), {})
    engine._scheduler_tick()
    with db.cursor() as cur:
        cur.execute("SELECT value FROM portal_meta WHERE key = 'engine:last_cycle'")
        first = cur.fetchone()["value"]
    assert first, "a completed cycle stamps"
    assert datetime.fromisoformat(first)

    import sentinel.engine as engine_module

    def boom(db_, rules_):
        raise RuntimeError("wedged")

    monkeypatch.setattr(engine_module, "prune_all", boom)
    engine._last_prune = None
    engine._scheduler_tick()  # prune failure is HANDLED inside the tick - still a completed cycle
    with db.cursor() as cur:
        cur.execute("SELECT value FROM portal_meta WHERE key = 'engine:last_cycle'")
        assert cur.fetchone()["value"] >= first

    # a WEDGE - the tick itself never completing - leaves the stamp aging
    monkeypatch.setattr(engine, "_schedule_cycle", lambda: (_ for _ in ()).throw(RuntimeError("dead")))
    stamped_before = cur = None
    with db.cursor() as cur:
        cur.execute("SELECT value FROM portal_meta WHERE key = 'engine:last_cycle'")
        stamped_before = cur.fetchone()["value"]
    import contextlib

    with contextlib.suppress(RuntimeError):
        engine._scheduler_tick()
    with db.cursor() as cur:
        cur.execute("SELECT value FROM portal_meta WHERE key = 'engine:last_cycle'")
        assert cur.fetchone()["value"] == stamped_before, "a cycle that did not complete does not stamp"


def test_the_compose_healthcheck_probes_healthz(tmp_path):
    from pathlib import Path

    compose = Path("docker-compose.yml").read_text()
    assert "http://127.0.0.1:8390/healthz" in compose
    assert "urlopen('http://127.0.0.1:8390'," not in compose, "the root probe rested on the auth hole"
