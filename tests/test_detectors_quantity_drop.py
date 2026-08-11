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


# --- Windowing (G-7) -------------------------------------------------------
# The four tests above pass a window-less list and are deliberately left
# unchanged: they cover the arithmetic, not the window. These call the
# detector directly with window_start_ms, which is the defence that stops a
# caller handing over days of history again.

NOW_MS = 1_700_000_000_000
MINUTE_MS = 60_000


def _ts(record, ms):
    record["timestamp"] = ms
    return record


def test_fill_before_window_does_not_explain_drop():
    """The G-7 bug in miniature: a week-old sale must not cancel a fresh drop."""
    previous = {"BTC": Decimal("1.0")}
    current = {"BTC": Decimal("0.5")}
    stale = _ts(trade_record(symbol="BTC/USDT", side="sell", amount="0.5"), NOW_MS - 7 * 24 * 60 * MINUTE_MS)
    detections = detect_quantity_drop(
        previous, current, [], [stale], TOL_PCT, TOL_ABS, window_start_ms=NOW_MS - MINUTE_MS
    )
    assert len(detections) == 1
    assert detections[0].rule == "unexplained_quantity_decrease"
    assert detections[0].evidence["explained_outflow"] == "0"


def test_fill_inside_window_still_explains_drop():
    """The windowing must not break the legitimate case it exists to protect."""
    previous = {"BTC": Decimal("1.0")}
    current = {"BTC": Decimal("0.5")}
    fresh = _ts(trade_record(symbol="BTC/USDT", side="sell", amount="0.5"), NOW_MS - 10_000)
    detections = detect_quantity_drop(
        previous, current, [], [fresh], TOL_PCT, TOL_ABS, window_start_ms=NOW_MS - MINUTE_MS
    )
    assert detections == []


def test_withdrawal_before_window_does_not_explain_drop():
    """Withdrawals are windowed on the same terms as fills."""
    previous = {"BTC": Decimal("1.0")}
    current = {"BTC": Decimal("0.5")}
    stale = withdrawal_record(amount="0.4999", currency="BTC", timestamp=NOW_MS - 7 * 24 * 60 * MINUTE_MS)
    detections = detect_quantity_drop(
        previous, current, [stale], [], TOL_PCT, TOL_ABS, window_start_ms=NOW_MS - MINUTE_MS
    )
    assert len(detections) == 1


def test_fill_exactly_at_window_start_explains_drop():
    """The boundary is inclusive - a fill at the snapshot instant counts."""
    previous = {"BTC": Decimal("1.0")}
    current = {"BTC": Decimal("0.5")}
    boundary = _ts(trade_record(symbol="BTC/USDT", side="sell", amount="0.5"), NOW_MS - MINUTE_MS)
    detections = detect_quantity_drop(
        previous, current, [], [boundary], TOL_PCT, TOL_ABS, window_start_ms=NOW_MS - MINUTE_MS
    )
    assert detections == []


def test_untimestamped_fill_does_not_explain_drop(caplog):
    """A record we can't place in time must not be assumed safe, and the
    exclusion must be logged with the exchange so chronic omissions surface
    as data rather than as a customer muting their alerts."""
    previous = {"BTC": Decimal("1.0")}
    current = {"BTC": Decimal("0.5")}
    undated = trade_record(symbol="BTC/USDT", side="sell", amount="0.5")
    undated.pop("timestamp")

    with caplog.at_level("WARNING"):
        detections = detect_quantity_drop(
            previous, current, [], [undated], TOL_PCT, TOL_ABS,
            window_start_ms=NOW_MS - MINUTE_MS, exchange="bitget-main",
        )

    assert len(detections) == 1
    assert "bitget-main" in caplog.text
    assert "no timestamp" in caplog.text


def test_unparseable_timestamp_does_not_explain_drop(caplog):
    previous = {"BTC": Decimal("1.0")}
    current = {"BTC": Decimal("0.5")}
    bad = _ts(trade_record(symbol="BTC/USDT", side="sell", amount="0.5"), "not-a-number")

    with caplog.at_level("WARNING"):
        detections = detect_quantity_drop(
            previous, current, [], [bad], TOL_PCT, TOL_ABS,
            window_start_ms=NOW_MS - MINUTE_MS, exchange="bybit-main",
        )

    assert len(detections) == 1
    assert "bybit-main" in caplog.text
    assert "unparseable" in caplog.text


def test_near_miss_is_logged_when_a_wider_grace_would_suppress(caplog):
    """A fill just outside the window still alerts, but logs evidence that the
    grace may be too tight - so tuning comes from data, not guesswork."""
    previous = {"BTC": Decimal("1.0")}
    current = {"BTC": Decimal("0.5")}
    just_outside = _ts(trade_record(symbol="BTC/USDT", side="sell", amount="0.5"), NOW_MS - 2 * MINUTE_MS)

    with caplog.at_level("WARNING"):
        detections = detect_quantity_drop(
            previous, current, [], [just_outside], TOL_PCT, TOL_ABS,
            window_start_ms=NOW_MS - MINUTE_MS, exchange="okx-main",
        )

    assert len(detections) == 1, "a near miss must still alert, not be suppressed"
    assert "near miss" in caplog.text
    assert "okx-main" in caplog.text


def test_no_near_miss_log_for_a_genuinely_unexplained_drop(caplog):
    """A drop with no explaining activity at all is not a near miss, and must
    not emit a tuning hint that would train an operator to widen the grace."""
    previous = {"BTC": Decimal("1.0")}
    current = {"BTC": Decimal("0.5")}

    with caplog.at_level("WARNING"):
        detections = detect_quantity_drop(
            previous, current, [], [], TOL_PCT, TOL_ABS, window_start_ms=NOW_MS - MINUTE_MS
        )

    assert len(detections) == 1
    assert "near miss" not in caplog.text
