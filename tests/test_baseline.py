from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from sentinel.baseline import (
    known_transfer_routes,
    latest_balance_snapshot,
    prune_balance_snapshots,
    record_balance_snapshot,
    record_known_address,
    record_seen_withdrawal,
    seed_baseline,
)
from tests.conftest import make_account
from tests.fixtures.ccxt_responses import withdrawal_record


def test_seen_withdrawal_is_reported_new_only_once(db):
    account_id = make_account(db)
    record = withdrawal_record(order_id="wd-1")

    assert record_seen_withdrawal(db, account_id, record) is True
    assert record_seen_withdrawal(db, account_id, dict(record)) is False


def test_known_address_roundtrip(db):
    account_id = make_account(db)

    assert record_known_address(db, account_id, "bc1qabc", "BTC", "deposit_history") is True
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM known_addresses WHERE account_id = ? AND address = ?", (account_id, "bc1qabc"))
        assert cur.fetchone() is not None
    assert record_known_address(db, account_id, "bc1qabc", "BTC", "deposit_history") is False, "already known"


def test_latest_balance_snapshot_returns_newest_and_prune_keeps_it(db):
    account_id = make_account(db)
    record_balance_snapshot(db, account_id, {"USDT": 100})
    record_balance_snapshot(db, account_id, {"USDT": 50, "BTC": 1})

    assert latest_balance_snapshot(db, account_id) == {"USDT": Decimal("50"), "BTC": Decimal("1")}

    # Backdate a stale row past the retention window; prune must remove only it.
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO balance_snapshots (account_id, coin, quantity, snapshot_at) VALUES (?, 'ETH', '9', ?)",
            (account_id, old),
        )
    assert prune_balance_snapshots(db, account_id, keep_days=7) == 1
    assert latest_balance_snapshot(db, account_id) == {"USDT": Decimal("50"), "BTC": Decimal("1")}


def test_seed_baseline_seeds_pre_existing_transfer_routes(db):
    # Regression: routes the customer already used must be baseline, not
    # first-poll HIGH transfer_route_never_seen alerts (an April spot->p2p
    # transfer alerted in July on live Bitget accounts before this seed).
    account_id = make_account(db)
    client = MagicMock()
    client.fetch_balance.return_value = {"total": {"USDT": 100}}
    client.fetch_deposits.return_value = []
    client.fetch_withdrawals.return_value = []
    client.fetch_closed_orders.return_value = []
    client.fetch_transfers.return_value = [
        {"id": "t-1", "currency": "USDT", "amount": 216.0, "fromAccount": "spot", "toAccount": "p2p"}
    ]
    client.fetch_account_info.return_value = {"data": {"authorities": "readonly_r", "ips": ""}}

    seed_baseline(db, account_id, client)

    assert "spot->p2p" in known_transfer_routes(db, account_id)
