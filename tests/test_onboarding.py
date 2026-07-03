from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sentinel.exchange import BitgetClient, TamperSignal
from sentinel.onboarding import OnboardingRejected, onboard_account, verify_read_only
from tests.fixtures.ccxt_responses import account_info_response


def _client_with(
    balance_result=None,
    balance_error=None,
    account_info_result=None,
    account_info_error=None,
    deposits=None,
    withdrawals=None,
    trades=None,
):
    """A BitgetClient with its network-touching methods replaced by mocks -
    never hits a live exchange."""
    client = BitgetClient("key", "secret", "pass")
    client.fetch_balance = (
        MagicMock(side_effect=balance_error)
        if balance_error is not None
        else MagicMock(return_value=balance_result or {"total": {"USDT": 100}})
    )
    client.fetch_account_info = (
        MagicMock(side_effect=account_info_error)
        if account_info_error is not None
        else MagicMock(return_value=account_info_result or account_info_response())
    )
    client.fetch_deposits = MagicMock(return_value=deposits or [])
    client.fetch_withdrawals = MagicMock(return_value=withdrawals or [])
    client.fetch_my_trades = MagicMock(return_value=trades or [])
    return client


def test_read_only_key_is_accepted():
    client = _client_with(account_info_result=account_info_response(authorities="spot_account_r,spot_order_r"))
    ok, reason = verify_read_only(client)
    assert ok, reason


def test_key_with_trade_write_scope_is_rejected():
    client = _client_with(account_info_result=account_info_response(authorities="spot_account_r,spot_trade_w"))
    ok, reason = verify_read_only(client)
    assert not ok


def test_key_with_withdraw_keyword_is_rejected():
    client = _client_with(account_info_result=account_info_response(authorities="withdraw_w"))
    ok, reason = verify_read_only(client)
    assert not ok


def test_missing_authorities_field_fails_closed():
    resp = account_info_response()
    resp["data"].pop("authorities")
    client = _client_with(account_info_result=resp)
    ok, reason = verify_read_only(client)
    assert not ok
    assert "authorities" in reason


def test_balance_call_failure_rejects():
    client = _client_with(balance_error=TamperSignal("invalid key"))
    ok, reason = verify_read_only(client)
    assert not ok


def test_onboard_account_persists_encrypted_on_success(db, cipher, monkeypatch):
    def fake_client_ctor(api_key, secret, password):
        return _client_with(account_info_result=account_info_response(authorities="spot_account_r"))

    monkeypatch.setattr("sentinel.onboarding.BitgetClient", fake_client_ctor)

    result = onboard_account(
        db, cipher, label="acct-1", topology="independent", api_key="k", secret="s", passphrase="p"
    )
    assert result.label == "acct-1"

    with db.cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE id = ?", (result.account_id,))
        row = cur.fetchone()
    assert row["encrypted_api_key"] != "k"
    assert cipher.decrypt(row["encrypted_api_key"]) == "k"


def test_onboard_account_rejects_write_scope_and_persists_nothing(db, cipher, monkeypatch):
    def fake_client_ctor(api_key, secret, password):
        return _client_with(account_info_result=account_info_response(authorities="spot_trade_w"))

    monkeypatch.setattr("sentinel.onboarding.BitgetClient", fake_client_ctor)

    with pytest.raises(OnboardingRejected):
        onboard_account(
            db, cipher, label="acct-2", topology="independent", api_key="k", secret="s", passphrase="p"
        )

    with db.cursor() as cur:
        cur.execute("SELECT * FROM accounts")
        rows = cur.fetchall()
    assert len(rows) == 0
