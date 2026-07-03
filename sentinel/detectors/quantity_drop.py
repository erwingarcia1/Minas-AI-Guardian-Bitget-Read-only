from __future__ import annotations

from decimal import Decimal

from sentinel.detectors.base import Detection
from sentinel.numeric import to_decimal


def _explained_outflow_for_coin(coin: str, withdrawals: list[dict], fills: list[dict]) -> Decimal:
    outflow = Decimal("0")

    for w in withdrawals:
        if w.get("currency") != coin:
            continue
        outflow += to_decimal(w.get("amount"))
        fee = w.get("fee") or {}
        if fee.get("currency") == coin:
            outflow += to_decimal(fee.get("cost"))

    for f in fills:
        symbol = f.get("symbol") or ""
        base, _, quote = symbol.partition("/")
        side = f.get("side")
        # `fills` are CCXT unified Order records (from fetch_closed_orders,
        # which - unlike fetch_my_trades - doesn't require a per-call symbol
        # on Bitget). Use the EXECUTED quantity (`filled`), not the requested
        # order `amount`, which can differ on a partial fill. Trade-shaped
        # records (tests, or a future per-symbol trades pull) fall back to
        # `amount`/`cost` since they have no `filled` field.
        executed_qty = f.get("filled") if f.get("filled") is not None else f.get("amount")
        if side == "sell" and base == coin:
            outflow += to_decimal(executed_qty)
        elif side == "buy" and quote == coin:
            outflow += to_decimal(f.get("cost"))
        fee = f.get("fee") or {}
        if fee.get("currency") == coin:
            outflow += to_decimal(fee.get("cost"))

    return outflow


def detect_quantity_drop(
    previous_balances: dict[str, Decimal],
    current_balances: dict[str, Decimal],
    withdrawals_in_window: list[dict],
    fills_in_window: list[dict],
    tolerance_pct: Decimal,
    tolerance_absolute: Decimal,
) -> list[Detection]:
    """Flags a per-asset quantity decrease not explained by a withdrawal or
    a matching trade fill in the same window. Tracks quantity, not USD
    value, so a price drop is never mistaken for theft.

    `fills_in_window` is expected to be CCXT unified Order records (from
    BitgetClient.fetch_closed_orders), not Trade records - Bitget's
    fetchMyTrades requires a symbol per call on classic accounts, so it
    can't cover "every pair this account touched" in one shot the way
    fetch_closed_orders can. Confirmed against a live account during Phase 1
    testing, not assumed.

    Known Phase 1 limitation, documented rather than silently wrong: this
    does not reconcile futures liquidations, so it should only be wired on
    for spot/hold-style accounts in the pilot. See
    docs/phase0-capability-audit.md section 6 for the leveraged-futures gap
    and the Phase 2 fix (reconcile against contract account-bill liquidation
    entries once that businessType enum is confirmed)."""
    detections = []
    for coin, prev_qty in previous_balances.items():
        curr_qty = current_balances.get(coin, Decimal("0"))
        drop = prev_qty - curr_qty
        if drop <= 0:
            continue

        explained = _explained_outflow_for_coin(coin, withdrawals_in_window, fills_in_window)
        tolerance = max(tolerance_absolute, prev_qty * tolerance_pct / Decimal("100"))
        unexplained = drop - explained
        if unexplained > tolerance:
            detections.append(
                Detection(
                    rule="unexplained_quantity_decrease",
                    severity="CRITICAL",
                    confidence="high",
                    message=(
                        f"{coin} balance dropped by {drop} but only {explained} is explained "
                        f"by withdrawals/trades in this window ({unexplained} unaccounted for)"
                    ),
                    evidence={
                        "coin": coin,
                        "previous_quantity": str(prev_qty),
                        "current_quantity": str(curr_qty),
                        "drop": str(drop),
                        "explained_outflow": str(explained),
                        "unexplained_amount": str(unexplained),
                    },
                )
            )
    return detections
