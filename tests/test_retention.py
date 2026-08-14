"""Bounding the tables that grow on every poll (#60).

`prune_balance_snapshots` was the only retention anywhere. `events`, `alerts`,
`seen_withdrawals`, `seen_transfers`, `seen_fills`, `sms_deliveries`,
`operator_pages` and `permission_snapshots` all grew forever, most of them at
the fast loop's 45-second cadence — and this project has already had one outage
from a query going quadratic over a growing table (`baseline.py` still carries
the comment about the 2026-07-06 dashboard incident).

The interesting part isn't deleting old rows. It's which tables must **not** be
pruned, and which rows must survive inside the ones that are — because getting
either wrong turns a housekeeping job into a silent detection failure.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from sentinel.config import RulesConfig
from sentinel.retention import NEVER_PRUNE, prune_all, table_counts
from tests.fixtures.ccxt_responses import account_info_response


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


@pytest.fixture
def rules():
    return RulesConfig("config/rules.yaml")


def _account(db, label="ret"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, onboarded_at, updated_at) "
            "VALUES (?, 'independent', 'x', 'y', 'z', '2026-01-01', '2026-01-01')",
            (label,),
        )
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
        return account_id


# --- the ordinary case --------------------------------------------------------

def test_old_rows_go_and_recent_ones_stay(db, rules):
    account_id = _account(db)
    with db.cursor() as cur:
        for age in (5000, 1):
            cur.execute(
                "INSERT INTO events (account_id, event_type, severity, payload_json, created_at) "
                "VALUES (?, 'withdrawal_initiated', 'CRITICAL', '{}', ?)",
                (account_id, _ago(age)),
            )

    deleted = prune_all(db, rules)

    assert deleted["events"] == 1
    with db.cursor() as cur:
        cur.execute("SELECT count(*) c FROM events")
        assert cur.fetchone()["c"] == 1


def test_every_growing_table_is_covered(db, rules):
    """The list is the point. A table added later that nobody adds here grows
    forever and nothing says so."""
    from sentinel.retention import KEEP_LATEST_PER_ACCOUNT, PRUNABLE

    covered = set(PRUNABLE) | set(KEEP_LATEST_PER_ACCOUNT) | NEVER_PRUNE
    with db.cursor() as cur:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        actual = {r["name"] for r in cur.fetchall()}

    assert actual - covered == set(), f"tables with no retention decision: {actual - covered}"


def test_every_retention_decision_names_a_real_table(db, rules):
    """The other direction, and the one that fails silently.

    `prune_all` iterates `PRUNABLE` and issues `DELETE FROM <table> WHERE
    <column> < ?` per key. A key naming a table that was renamed or dropped
    matches nothing — no error, `rowcount` 0 — so the entry reads as active
    retention in the list, in `table_counts`, and to anyone auditing what this
    product keeps, while pruning nothing at all. The table it was *meant* to
    cover grows forever, and `test_every_growing_table_is_covered` above stays
    green throughout, because the stale key still counts as covering it.

    Same shape as the `[1.0.0]` CHANGELOG link definition that outlived its
    heading (see docs/AUTONOMOUS-LOG.md): two sets that are views of one list,
    containment asserted one way only, so an entry pointing at something that
    no longer exists is invisible. Both directions are meaningful here.
    """
    from sentinel.retention import KEEP_LATEST_PER_ACCOUNT, PRUNABLE

    covered = set(PRUNABLE) | set(KEEP_LATEST_PER_ACCOUNT) | NEVER_PRUNE
    with db.cursor() as cur:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        actual = {r["name"] for r in cur.fetchall()}

    assert covered - actual == set(), (
        f"retention decisions naming no such table: {sorted(covered - actual)}. "
        "A renamed or dropped table leaves the old key behind, where it prunes "
        "nothing and still reads as covered."
    )


def test_pruning_is_repeatable(db, rules):
    account_id = _account(db)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO events (account_id, event_type, severity, payload_json, created_at) "
            "VALUES (?, 'x', 'LOW', '{}', ?)", (account_id, _ago(5000)))

    assert prune_all(db, rules)["events"] == 1
    assert prune_all(db, rules)["events"] == 0


# --- what must never be pruned -----------------------------------------------

def test_baseline_tables_are_never_pruned(db, rules):
    """`known_addresses`, `traded_pairs` and `transfer_routes` are the account's
    baseline, not its history. Deleting an old row there doesn't free space, it
    manufactures a false positive: the next withdrawal to a five-year-old
    address becomes "never seen before", and the customer gets a CRITICAL about
    their own cold wallet."""
    account_id = _account(db)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO known_addresses (account_id, address, coin, source, first_seen_at) "
            "VALUES (?, 'bc1qancient', 'BTC', 'withdrawal_history', ?)", (account_id, _ago(5000)))
        cur.execute("INSERT INTO traded_pairs (account_id, symbol, first_seen_at) VALUES (?, 'BTC/USDT', ?)",
                    (account_id, _ago(5000)))
        cur.execute("INSERT INTO transfer_routes (account_id, route, first_seen_at) VALUES (?, 'spot->swap', ?)",
                    (account_id, _ago(5000)))

    prune_all(db, rules)

    with db.cursor() as cur:
        for table in ("known_addresses", "traded_pairs", "transfer_routes"):
            cur.execute(f"SELECT count(*) c FROM {table}")
            assert cur.fetchone()["c"] == 1, f"{table} was pruned - that is a false-positive generator"


def test_pruning_dedup_state_resurrects_the_alert(db, cipher, monkeypatch):
    """Why the dedup window has to be generous, demonstrated rather than
    asserted.

    `seen_withdrawals` is not history, it is the answer to "have I alerted on
    this". Delete a row while the exchange still returns that withdrawal, and
    the next poll re-alerts on a months-old movement. The retention period for
    these tables is therefore bounded **below by correctness**, not just by
    policy — it must exceed the longest history any exchange returns."""
    from sentinel.onboarding import onboard_account
    from sentinel.poller import AccountPoller
    from tests.fixtures.ccxt_responses import withdrawal_record

    class Recording:
        def __init__(self):
            self.sent = []

        def send(self, alert):
            self.sent.append(alert)

    def client(withdrawals=None):
        c = MagicMock()
        c.fetch_deposits.return_value = []
        c.fetch_withdrawals.return_value = withdrawals or []
        c.fetch_closed_orders.return_value = []
        c.fetch_balance.return_value = {"total": {"BTC": 1.0}}
        c.fetch_account_info.return_value = account_info_response()
        c.fetch_tickers.return_value = {}
        c.fetch_transfers.return_value = []
        return c

    monkeypatch.setattr("sentinel.onboarding.make_client", lambda *a, **k: client())
    result = onboard_account(db, cipher, label="resurrect", topology="independent",
                             api_key="k", secret="s", passphrase="p")
    with db.cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE id = ?", (result.account_id,))
        row = cur.fetchone()

    wd = withdrawal_record(order_id="wd-old", address="bc1qexistingaddress")
    monkeypatch.setattr("sentinel.poller.make_client", lambda *a, **k: client(withdrawals=[wd]))
    channel = Recording()
    poller = AccountPoller(db, cipher, RulesConfig("config/rules.yaml"),
                           {"CRITICAL": [channel], "HIGH": [channel], "MEDIUM": [channel], "LOW": [channel]})
    poller.run_fast_loop(row)
    assert len([a for a in channel.sent if a.rule == "withdrawal_initiated"]) == 1

    # Simulate a too-short dedup retention.
    with db.cursor() as cur:
        cur.execute("DELETE FROM seen_withdrawals")

    poller.run_fast_loop(row)

    # Asserted on the detection, not the delivery. The alert cooldown happens to
    # swallow the immediate repeat, which makes this look survivable - it isn't.
    # The detection fires again, writes a second forensic event, and would be
    # delivered the moment the cooldown lapses. On a real deployment the prune
    # and the re-poll are days apart, so there is no cooldown left to help.
    with db.cursor() as cur:
        cur.execute("SELECT count(*) c FROM events WHERE event_type = 'withdrawal_initiated'")
        redetected = cur.fetchone()["c"]

    assert redetected == 2, (
        "expected the months-old withdrawal to be re-detected - this test documents "
        "the hazard the generous dedup default exists to prevent"
    )


def test_the_dedup_window_is_generously_long(rules):
    """Bounded below by correctness. It must exceed the longest withdrawal or
    order history any supported exchange will return on a `since=None` fetch,
    and we do not control that number."""
    assert rules.get_int("retention", "dedup_days") >= 365


# --- what must survive inside a pruned table ---------------------------------

def test_the_latest_snapshot_per_account_always_survives(db, rules):
    """`detect_permission_state_change` returns nothing when there is no
    previous snapshot, so deleting the last one silently resets the baseline —
    a permission change spanning the prune would go undetected. The same is
    true of the balance baseline."""
    account_id = _account(db)
    with db.cursor() as cur:
        for age in (5000, 4000, 3000):
            cur.execute(
                "INSERT INTO permission_snapshots (account_id, authorities, ip_whitelist, snapshot_at) "
                "VALUES (?, '\"spot_account_r\"', '\"1.2.3.4\"', ?)", (account_id, _ago(age)))

    prune_all(db, rules)

    with db.cursor() as cur:
        cur.execute("SELECT count(*) c FROM permission_snapshots WHERE account_id = ?", (account_id,))
        assert cur.fetchone()["c"] == 1, "every snapshot was deleted - the baseline is gone"


def test_the_whole_latest_balance_snapshot_survives_not_just_one_coin(db, rules):
    """A balance snapshot is many rows sharing one timestamp, one per coin.
    Keeping "the newest row" would keep a single coin and silently shrink the
    baseline to it, so every other coin reads as having vanished."""
    account_id = _account(db)
    # One timestamp shared by every coin, which is what `record_balance_snapshot`
    # writes - it computes `now` once and reuses it for the whole snapshot.
    taken_at = _ago(400)
    with db.cursor() as cur:
        for coin in ("BTC", "ETH", "USDT"):
            cur.execute(
                "INSERT INTO balance_snapshots (account_id, coin, quantity, snapshot_at) VALUES (?, ?, '1', ?)",
                (account_id, coin, taken_at))

    prune_all(db, rules)

    with db.cursor() as cur:
        cur.execute("SELECT count(*) c FROM balance_snapshots WHERE account_id = ?", (account_id,))
        assert cur.fetchone()["c"] == 3, "the latest snapshot was partially deleted"


# --- visibility ---------------------------------------------------------------

def test_row_counts_are_reported(db, rules):
    account_id = _account(db)
    with db.cursor() as cur:
        cur.execute("INSERT INTO events (account_id, event_type, severity, payload_json, created_at) "
                    "VALUES (?, 'x', 'LOW', '{}', ?)", (account_id, _ago(1)))

    counts = table_counts(db)
    assert counts["events"] == 1
    assert "seen_fills" in counts


def test_table_sizes_are_exported_as_a_metric(db, rules):
    """Growth has to be observable before it is a problem - this project has
    already had one outage from a table nobody was watching."""
    from prometheus_client import CollectorRegistry

    from sentinel.metrics import Metrics

    registry = CollectorRegistry()
    metrics = Metrics(registry=registry)
    account_id = _account(db)
    with db.cursor() as cur:
        cur.execute("INSERT INTO events (account_id, event_type, severity, payload_json, created_at) "
                    "VALUES (?, 'x', 'LOW', '{}', ?)", (account_id, _ago(1)))

    metrics.set_table_rows(table_counts(db))

    assert registry.get_sample_value("sentinel_table_rows", {"table": "events"}) == 1


# --- wiring -------------------------------------------------------------------

def test_the_scheduler_actually_prunes(db, cipher):
    """The recurring fault in this codebase: behaviour tested, call site not.
    A retention job nobody calls is a table that grows forever."""
    from sentinel.engine import Engine

    account_id = _account(db)
    with db.cursor() as cur:
        cur.execute("INSERT INTO events (account_id, event_type, severity, payload_json, created_at) "
                    "VALUES (?, 'x', 'LOW', '{}', ?)", (account_id, _ago(5000)))

    engine = Engine(db, cipher, RulesConfig("config/rules.yaml"), {})
    engine._scheduler_tick()

    with db.cursor() as cur:
        cur.execute("SELECT count(*) c FROM events")
        assert cur.fetchone()["c"] == 0


def test_pruning_does_not_run_on_every_cycle(db, cipher, monkeypatch):
    """Full-table DELETEs on a 45-second loop is wasted I/O on the thing the
    fast loop is querying."""
    from sentinel.engine import Engine

    engine = Engine(db, cipher, RulesConfig("config/rules.yaml"), {})
    calls = {"n": 0}

    def counted(db_, rules_):
        calls["n"] += 1
        return {}

    monkeypatch.setattr("sentinel.engine.prune_all", counted)
    for _ in range(5):
        engine._scheduler_tick()

    assert calls["n"] == 1, f"pruned {calls['n']} times in five cycles"


def test_never_run_is_none_not_zero(db, cipher):
    """The monotonic lesson: `time.monotonic()` is seconds since boot, so `0`
    as "never pruned" would gate the first prune on the machine's uptime."""
    from sentinel.engine import Engine

    engine = Engine(db, cipher, RulesConfig("config/rules.yaml"), {})
    assert engine._last_prune is None
