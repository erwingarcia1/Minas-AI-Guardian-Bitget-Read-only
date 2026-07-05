from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sentinel.exchange import BitgetClient, BybitClient, ExchangeClient, make_client
from sentinel.poller import _load_client


def test_make_client_dispatches_by_exchange():
    with patch("sentinel.exchange.ccxt.bitget"), patch("sentinel.exchange.ccxt.bybit"):
        assert isinstance(make_client("bitget", "k", "s", "p"), BitgetClient)
        assert isinstance(make_client("bybit", "k", "s"), BybitClient)


def test_make_client_rejects_unknown_exchange():
    with pytest.raises(ValueError):
        make_client("kraken", "k", "s")


def test_bybit_client_has_no_passphrase_and_ignores_one():
    with patch("sentinel.exchange.ccxt.bybit") as mock_bybit:
        BybitClient("k", "s")
        # constructed without a password field
        args, kwargs = mock_bybit.call_args
        assert "password" not in args[0]


def test_bybit_fetch_closed_orders_iterates_categories():
    with patch("sentinel.exchange.ccxt.bybit") as mock_bybit:
        ex = MagicMock()
        ex.fetch_closed_orders.side_effect = lambda **kw: [{"symbol": str(kw.get("params"))}]
        mock_bybit.return_value = ex
        client = BybitClient("k", "s")
        orders = client.fetch_closed_orders()
        # one call per product category (spot, linear, inverse, option)
        assert ex.fetch_closed_orders.call_count == 4
        assert len(orders) == 4


def test_bybit_fetch_balance_falls_back_across_wallet_types():
    with patch("sentinel.exchange.ccxt.bybit") as mock_bybit:
        ex = MagicMock()
        calls = {"n": 0}

        def balance(params=None):
            calls["n"] += 1
            if params.get("type") == "unified":
                raise RuntimeError("not a UTA account")
            return {"total": {"USDT": 1}, "type": params["type"]}

        ex.fetch_balance.side_effect = balance
        mock_bybit.return_value = ex
        client = BybitClient("k", "s")
        result = client.fetch_balance()
        assert result["type"] == "spot"  # unified failed, fell back to spot


def test_load_client_uses_row_exchange(db, cipher):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, onboarded_at, updated_at) VALUES "
            "('bb', 'bybit', 'independent', ?, ?, ?, '2026-01-01', '2026-01-01')",
            (cipher.encrypt("k"), cipher.encrypt("s"), cipher.encrypt("")),
        )
        cur.execute("SELECT * FROM accounts WHERE label='bb'")
        row = cur.fetchone()
    with patch("sentinel.exchange.ccxt.bybit"):
        client = _load_client(cipher, row)
    assert isinstance(client, BybitClient)
