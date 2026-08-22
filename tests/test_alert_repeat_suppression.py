"""#22's two remaining criteria: the dedup race is closed, and suppression
never becomes concealment.

The race: the old dispatch checked the cooldown in one lock block and
inserted the row in another, with the network sends between - two
simultaneous checks of the same condition could both slip through and both
page. Check and claim now share one cursor block, so concurrent dispatches
serialize on the database write lock: one row, one send, and the loser is
counted as a repeat.

The visibility: a repeat suppressed by the cooldown increments
`repeat_count` on the alert it repeated, and every portal listing renders
the count - one ongoing problem stays one alert that says how long it has
kept firing.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from sentinel.alerts.dispatch import dispatch
from sentinel.detectors.base import Detection
from tests.conftest import make_account

COOLDOWN = {"CRITICAL": 15}


def _detection(order_id="wd-1"):
    return Detection(
        rule="withdrawal_initiated", severity="CRITICAL", confidence="high", message="m",
        evidence={"order_id": order_id},
    )


class RecordingChannel:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


def _alert_rows(db, account_id):
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, delivered_to, repeat_count, last_repeat_at FROM alerts "
            "WHERE account_id = ? ORDER BY id", (account_id,))
        return cur.fetchall()


# --- the race -----------------------------------------------------------------

def test_two_simultaneous_checks_produce_one_alert_and_one_send(db):
    """The criterion, run for real: many threads dispatch the same detection
    through a start barrier. Exactly one row, exactly one delivery, and every
    loser is counted on that row as a repeat."""
    channel = RecordingChannel()
    channels = {"CRITICAL": [channel]}
    account_id = make_account(db)
    n = 8
    barrier = threading.Barrier(n)
    results = []

    def contender():
        barrier.wait()
        results.append(
            dispatch(db, channels, account_id, "a", _detection(), COOLDOWN))

    threads = [threading.Thread(target=contender) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = _alert_rows(db, account_id)
    assert len(rows) == 1, "two simultaneous checks slipped two alerts through"
    assert len(channel.sent) == 1
    assert sum(1 for r in results if r is not None) == 1
    assert rows[0]["repeat_count"] == n - 1, "every suppressed contender is counted"


# --- suppression is counted, never hidden -------------------------------------

def test_a_suppressed_repeat_is_counted_on_the_alert_it_repeated(db):
    channel = RecordingChannel()
    channels = {"CRITICAL": [channel]}
    account_id = make_account(db)

    first = dispatch(db, channels, account_id, "a", _detection(), COOLDOWN)
    assert isinstance(first, int)
    assert dispatch(db, channels, account_id, "a", _detection(), COOLDOWN) is None
    assert dispatch(db, channels, account_id, "a", _detection(), COOLDOWN) is None

    (row,) = _alert_rows(db, account_id)
    assert row["repeat_count"] == 2
    assert row["last_repeat_at"] is not None
    assert len(channel.sent) == 1, "counting a repeat must not deliver it"


def test_a_repeat_after_the_cooldown_is_a_new_alert_with_a_fresh_count(db, monkeypatch):
    channel = RecordingChannel()
    channels = {"CRITICAL": [channel]}
    account_id = make_account(db)
    dispatch(db, channels, account_id, "a", _detection(), COOLDOWN)
    # Age the stored row past the cooldown rather than sleeping through it.
    with db.cursor() as cur:
        old = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
        cur.execute("UPDATE alerts SET sent_at = ? WHERE account_id = ?", (old, account_id))

    second = dispatch(db, channels, account_id, "a", _detection(), COOLDOWN)

    assert isinstance(second, int)
    rows = _alert_rows(db, account_id)
    assert len(rows) == 2
    assert rows[-1]["repeat_count"] == 0, "a new alert starts its own count"


# --- the claim happens before the sends ---------------------------------------

def test_a_channel_that_dies_mid_send_still_leaves_the_detected_row(db):
    """#57's distinction, now guaranteed by ordering: the row is claimed with
    delivered_to = 0 before any network call, so a crash mid-delivery leaves
    'detected, reached nobody' rather than nothing."""
    class Boom:
        def send(self, alert):
            raise RuntimeError("channel down")

    account_id = make_account(db)
    alert_id = dispatch(db, {"CRITICAL": [Boom()]}, account_id, "a", _detection(), COOLDOWN)

    assert isinstance(alert_id, int)
    (row,) = _alert_rows(db, account_id)
    assert row["delivered_to"] == 0


def test_delivered_to_records_how_many_channels_accepted(db):
    c1, c2 = RecordingChannel(), RecordingChannel()
    account_id = make_account(db)
    dispatch(db, {"CRITICAL": [c1, c2]}, account_id, "a", _detection(), COOLDOWN)
    (row,) = _alert_rows(db, account_id)
    assert row["delivered_to"] == 2


# --- the portal shows the count ----------------------------------------------

def _member_account(db, email="alice@example.com"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES ('acct-r', 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', "
            "'2026-01-01', '2026-01-01')", (email,))
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id, last_success_at) VALUES (?, ?)",
                    (account_id, datetime.now(timezone.utc).isoformat()))
        return account_id


def test_the_home_and_history_pages_say_the_alert_is_still_firing(db):
    from sentinel.portal import _render_alert_history, _render_home

    channel = RecordingChannel()
    account_id = _member_account(db)
    dispatch(db, {"CRITICAL": [channel]}, account_id, "acct-r", _detection(), COOLDOWN)
    for _ in range(3):
        dispatch(db, {"CRITICAL": [channel]}, account_id, "acct-r", _detection(), COOLDOWN)

    home = _render_home(db, "alice@example.com")
    history = _render_alert_history(db, "alice@example.com", {})
    for page in (home, history):
        assert "repeated 3×" in page, "a suppressed repeat must stay visible"
        assert "still firing" in page


def test_an_unrepeated_alert_carries_no_repeat_note(db):
    from sentinel.portal import _render_home

    channel = RecordingChannel()
    account_id = _member_account(db)
    dispatch(db, {"CRITICAL": [channel]}, account_id, "acct-r", _detection(), COOLDOWN)
    assert "still firing" not in _render_home(db, "alice@example.com")
