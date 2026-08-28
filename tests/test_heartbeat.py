"""#63 / decision 7: the dead-man's switch, push side.

The Guardian pushes a heartbeat to an external monitor; silence is the
signal. On restart it reconstructs the gap it slept through from the last
beat it pushed, records it, pages Minas, and the member digest tells
customers - in the digest, not as an alert.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sentinel.alerts.digest import build_member_digest
from sentinel.heartbeat import Heartbeat, gaps_since, last_beat, record_gap
from sentinel.portal import set_member_password

T0 = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


class _Pager:
    def __init__(self):
        self.pages = []

    def page(self, **kw):
        self.pages.append(kw)
        return 1


class _Resp:
    def __init__(self, ok=True):
        self.ok = ok

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError("503")


@pytest.fixture
def posts(monkeypatch):
    out = []

    def fake(url, **kw):
        out.append(url)
        return _Resp(ok=not url.endswith("/down"))

    monkeypatch.setattr("sentinel.net.post", fake)
    return out


def test_no_url_means_nothing_is_pushed_and_nothing_pages(db, posts):
    hb = Heartbeat(db, None, 300, operator_alerter=_Pager())
    assert not hb.configured and hb.evaluate(T0) is False and posts == []


def test_a_beat_is_pushed_on_the_interval_and_not_before(db, posts):
    hb = Heartbeat(db, "https://hc.example/ping", 300)
    assert hb.evaluate(T0) is True and posts == ["https://hc.example/ping"]
    assert hb.evaluate(T0 + timedelta(seconds=200)) is False and len(posts) == 1
    assert hb.evaluate(T0 + timedelta(seconds=300)) is True and len(posts) == 2
    assert last_beat(db) == T0 + timedelta(seconds=300)


def test_an_unreachable_monitor_pages_high_and_keeps_the_last_beat(db, posts):
    pager = _Pager()
    hb = Heartbeat(db, "https://hc.example/down", 300, operator_alerter=pager)
    assert hb.evaluate(T0) is False
    assert pager.pages[-1]["rule"] == "heartbeat_unreachable" and pager.pages[-1]["severity"] == "HIGH"
    assert last_beat(db) is None


def test_a_gap_is_reconstructed_on_start_from_the_last_beat(db, posts):
    hb = Heartbeat(db, "https://hc.example/ping", 300, operator_alerter=_Pager())
    hb.evaluate(T0)
    later = Heartbeat(db, "https://hc.example/ping", 300, operator_alerter=(pager := _Pager()))
    assert later.detect_gap_on_start(T0 + timedelta(minutes=9)) is None, "within two intervals: not a gap"
    gap = later.detect_gap_on_start(T0 + timedelta(minutes=45))
    assert gap and gap["minutes"] == 45 and gap["started_at"] == T0
    assert pager.pages[-1]["rule"] == "monitoring_gap"
    assert gaps_since(db, None)[0]["seconds"] == 45 * 60


def test_a_first_ever_start_has_no_gap_to_report(db):
    assert Heartbeat(db, "https://hc.example/ping", 300).detect_gap_on_start(T0) is None


def test_the_digest_tells_customers_about_a_gap_in_the_digest(db):
    from tests.test_customer_digest import _account

    _account(db, "acct-g", "alice@example.com")
    set_member_password(db, "alice@example.com", "pw-long-enough-1")
    record_gap(db, T0, T0 + timedelta(minutes=40), "restart")
    body, _ = build_member_digest(db, "alice@example.com", now=T0 + timedelta(hours=2))
    assert "Monitoring was interrupted from 2026-08-28 12:00 to 2026-08-28 12:40 UTC (40 minutes)" in body
    assert "Minas was alerted" in body


def test_the_engine_detects_the_gap_on_start_and_beats_on_the_tick(db, cipher, rules, monkeypatch, posts):
    from sentinel.engine import Engine

    monkeypatch.setenv("SENTINEL_HEARTBEAT_URL", "https://hc.example/ping")
    engine = Engine(db, cipher, rules, {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []})
    engine._scheduler_tick()
    assert posts == ["https://hc.example/ping"]
