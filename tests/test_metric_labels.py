"""#55: `sentinel_untimestamped_records_total`'s `exchange` label carried the
account label.

The counter exists so a response-format change reaches an operator before a
customer finds it as a missed alert. The one field that would say *which
integration to go and look at* said which customer noticed instead — and the
series grew a new dimension for every account signed, where the intended one
has exactly four values.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from prometheus_client import CollectorRegistry

from sentinel.detectors.quantity_drop import detect_quantity_drop
from sentinel.metrics import Metrics

TOL_PCT = Decimal("0.5")
TOL_ABS = Decimal("0.00000001")


def _undated_fill():
    return {"symbol": "BTC/USDT", "side": "sell", "amount": "0.5", "cost": "30000", "timestamp": None}


def test_the_exchange_label_carries_the_exchange(db):
    """Not the account label. An alarm on this series has to name the
    integration that changed, and its cardinality must stay bounded by the
    number of exchanges we support rather than the number of customers."""
    registry = CollectorRegistry()
    metrics = Metrics(registry=registry)

    detect_quantity_drop(
        {"BTC": Decimal("1")},
        {"BTC": Decimal("0.5")},
        [],
        [_undated_fill()],
        TOL_PCT,
        TOL_ABS,
        window_start_ms=1_700_000_000_000,
        exchange="bitget",
        account_label="minas-pilot-btc",
        metrics=metrics,
    )

    assert registry.get_sample_value(
        "sentinel_untimestamped_records_total", {"exchange": "bitget", "source": "fill"}
    ) == 1
    assert registry.get_sample_value(
        "sentinel_untimestamped_records_total",
        {"exchange": "minas-pilot-btc", "source": "fill"},
    ) is None, "the account label must not appear as an exchange"


def test_the_log_line_still_names_the_account(caplog):
    """The operator still needs to know *whose* account produced the undated
    record — that just belongs in the log line, not in a metric dimension. The
    two were sharing one argument, which is how they drifted."""
    import logging

    with caplog.at_level(logging.WARNING, logger="sentinel.detectors.quantity_drop"):
        detect_quantity_drop(
            {"BTC": Decimal("1")},
            {"BTC": Decimal("0.5")},
            [],
            [_undated_fill()],
            TOL_PCT,
            TOL_ABS,
            window_start_ms=1_700_000_000_000,
            exchange="bitget",
            account_label="minas-pilot-btc",
        )

    assert any("minas-pilot-btc" in r.getMessage() for r in caplog.records), (
        "the warning should still identify the account"
    )


def test_the_poller_passes_the_accounts_exchange(db, cipher, monkeypatch):
    """End to end through the poller, which is where the two were conflated:
    `exchange=label` read the account label out of the row and passed it as the
    exchange."""
    from sentinel.config import RulesConfig
    from sentinel.onboarding import onboard_account
    from sentinel.poller import AccountPoller
    from tests.fixtures.ccxt_responses import account_info_response

    def client(fills=None, balance_total=None):
        c = MagicMock()
        c.fetch_deposits.return_value = []
        c.fetch_withdrawals.return_value = []
        c.fetch_closed_orders.return_value = fills or []
        c.fetch_balance.return_value = {"total": balance_total or {"BTC": 1.0}}
        c.fetch_account_info.return_value = account_info_response()
        c.fetch_tickers.return_value = {}
        c.fetch_transfers.return_value = []
        return c

    monkeypatch.setattr("sentinel.onboarding.make_client", lambda *a, **k: client())
    result = onboard_account(
        db, cipher, label="minas-pilot-btc", topology="independent",
        api_key="k", secret="s", passphrase="p", exchange="bitget",
    )
    with db.cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE id = ?", (result.account_id,))
        account_row = cur.fetchone()

    registry = CollectorRegistry()
    metrics = Metrics(registry=registry)
    poller = AccountPoller(
        db, cipher, RulesConfig("config/rules.yaml"),
        {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []}, metrics=metrics,
    )

    monkeypatch.setattr("sentinel.poller.make_client", lambda *a, **k: client())
    poller.run_fast_loop(account_row)  # establishes a balance snapshot
    monkeypatch.setattr(
        "sentinel.poller.make_client",
        lambda *a, **k: client(fills=[_undated_fill()], balance_total={"BTC": 0.5}),
    )
    poller.run_fast_loop(account_row)

    assert registry.get_sample_value(
        "sentinel_untimestamped_records_total", {"exchange": "bitget", "source": "fill"}
    ), "nothing was counted under the exchange"
    assert registry.get_sample_value(
        "sentinel_untimestamped_records_total",
        {"exchange": "minas-pilot-btc", "source": "fill"},
    ) is None, "the account label leaked into the exchange dimension"
