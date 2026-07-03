from __future__ import annotations

from decimal import Decimal

from sentinel.detectors.quantity_drop import detect_quantity_drop
from tests.fixtures.ccxt_responses import trade_record, withdrawal_record

TOL_PCT = Decimal("0.5")
TOL_ABS = Decimal("0.00000001")


def test_unexplained_drop_triggers_critical():
    previous = {"BTC": Decimal("1.0")}
    current = {"BTC": Decimal("0.5")}
    detections = detect_quantity_drop(previous, current, [], [], TOL_PCT, TOL_ABS)
    assert len(detections) == 1
    assert detections[0].rule == "unexplained_quantity_decrease"
    assert detections[0].severity == "CRITICAL"


def test_drop_explained_by_withdrawal_does_not_trigger():
    previous = {"BTC": Decimal("1.0")}
    current = {"BTC": Decimal("0.5")}
    wd = withdrawal_record(amount="0.4999", currency="BTC")  # + 0.0001 fee = 0.5
    detections = detect_quantity_drop(previous, current, [wd], [], TOL_PCT, TOL_ABS)
    assert detections == []


def test_drop_explained_by_sell_trade_does_not_trigger():
    previous = {"BTC": Decimal("1.0")}
    current = {"BTC": Decimal("0.9")}
    trade = trade_record(symbol="BTC/USDT", side="sell", amount="0.1", cost="6000")
    detections = detect_quantity_drop(previous, current, [], [trade], TOL_PCT, TOL_ABS)
    assert detections == []


def test_price_drop_alone_does_not_trigger():
    # Quantity unchanged - a price crash must never look like theft, since
    # this detector tracks per-asset quantity, not USD value.
    previous = {"BTC": Decimal("1.0")}
    current = {"BTC": Decimal("1.0")}
    detections = detect_quantity_drop(previous, current, [], [], TOL_PCT, TOL_ABS)
    assert detections == []
