from __future__ import annotations

from sentinel.baseline import is_known_address, record_known_address, record_seen_withdrawal
from tests.conftest import make_account
from tests.fixtures.ccxt_responses import withdrawal_record


def test_seen_withdrawal_is_reported_new_only_once(db):
    account_id = make_account(db)
    record = withdrawal_record(order_id="wd-1")

    assert record_seen_withdrawal(db, account_id, record) is True
    assert record_seen_withdrawal(db, account_id, dict(record)) is False


def test_known_address_roundtrip(db):
    account_id = make_account(db)

    assert is_known_address(db, account_id, "bc1qabc") is False
    record_known_address(db, account_id, "bc1qabc", "BTC", "deposit_history")
    assert is_known_address(db, account_id, "bc1qabc") is True
