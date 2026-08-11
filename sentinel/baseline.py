from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sentinel.db import Database
from sentinel.exchange import ExchangeClient
from sentinel.identity import fill_identity, withdrawal_identity
from sentinel.numeric import to_decimal


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_known_address(db: Database, account_id: int, address: str | None, coin: str | None, source: str) -> bool:
    """Returns True if the address was newly added to the baseline."""
    if not address:
        return False
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO known_addresses (account_id, address, coin, source, first_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(account_id, address) DO NOTHING
            """,
            (account_id, address, coin, source, _now()),
        )
        return cur.rowcount == 1


def is_known_address(db: Database, account_id: int, address: str) -> bool:
    with db.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM known_addresses WHERE account_id = ? AND address = ?",
            (account_id, address),
        )
        return cur.fetchone() is not None


def record_seen_withdrawal(db: Database, account_id: int, record: dict) -> bool:
    """Records a withdrawal (CCXT unified transaction dict) if not already
    seen. Returns True if it was newly recorded, False if already known -
    the poller uses that to decide whether to fire the withdrawal-created
    detection.

    A record the exchange sent without any reference used to return False here,
    which the poller reads as "already seen" - so it never reached the
    withdrawal-created or unseen-address detectors, and money left the account
    with nobody told (G-11). `withdrawal_identity` derives a key from the fields
    that don't move as the withdrawal settles, so an unreferenced withdrawal is
    alerted once rather than never."""
    order_id = withdrawal_identity(record)
    status = str(record.get("status") or "unknown")
    coin = record.get("currency")
    size = str(record.get("amount"))
    address = record.get("address")
    with db.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM seen_withdrawals WHERE account_id = ? AND order_id = ?",
            (account_id, order_id),
        )
        if cur.fetchone() is not None:
            return False
        cur.execute(
            """
            INSERT INTO seen_withdrawals
                (account_id, order_id, status, coin, size, address, raw_json, first_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, order_id, status, coin, size, address, json.dumps(record, default=str), _now()),
        )
    return True


def record_balance_snapshot(db: Database, account_id: int, balances: dict[str, object]) -> None:
    now = _now()
    with db.cursor() as cur:
        for coin, qty in balances.items():
            cur.execute(
                "INSERT INTO balance_snapshots (account_id, coin, quantity, snapshot_at) VALUES (?, ?, ?, ?)",
                (account_id, coin, str(to_decimal(qty)), now),
            )


def latest_balance_snapshot(db: Database, account_id: int) -> dict[str, object]:
    """Most recent quantity per coin, as Decimal."""
    balances, _ = latest_balance_snapshot_at(db, account_id)
    return balances


def latest_balance_snapshot_at(db: Database, account_id: int) -> tuple[dict[str, object], str | None]:
    """Most recent quantity per coin, plus the ISO timestamp that snapshot was
    taken at (None when the account has no snapshot yet).

    The timestamp is what bounds the quantity-drop explanation window: a drop
    is measured from this snapshot, so only activity at or after it can
    explain that drop. `last_fast_loop_at` is NOT a safe substitute - it is
    written near the top of the fast loop while the snapshot is written at the
    bottom, and onboarding's seed_baseline records a snapshot without any poll
    state at all."""
    with db.cursor() as cur:
        # The MAX() subquery must be non-correlated (parameterized, not
        # b.account_id): correlating it re-evaluates the MAX per row, which is
        # O(n^2) on a table that grows every fast loop - it wedged the live
        # dashboard within ~10 hours of polling (2026-07-06 outage).
        cur.execute(
            """
            SELECT coin, quantity, snapshot_at FROM balance_snapshots
            WHERE account_id = ? AND snapshot_at = (
                SELECT MAX(snapshot_at) FROM balance_snapshots WHERE account_id = ?
            )
            """,
            (account_id, account_id),
        )
        rows = cur.fetchall()
    balances = {row["coin"]: to_decimal(row["quantity"]) for row in rows}
    snapshot_at = rows[0]["snapshot_at"] if rows else None
    return balances, snapshot_at


def prune_balance_snapshots(db: Database, account_id: int, keep_days: int = 7) -> int:
    """Deletes snapshots older than the retention window. Snapshots exist to
    answer 'what changed since the last poll?' - week-old rows only slow that
    down. Returns the number of rows deleted."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM balance_snapshots WHERE account_id = ? AND snapshot_at < ?",
            (account_id, cutoff),
        )
        return cur.rowcount


def record_permission_snapshot(db: Database, account_id: int, authorities: object, ip_whitelist: object) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO permission_snapshots (account_id, authorities, ip_whitelist, snapshot_at) "
            "VALUES (?, ?, ?, ?)",
            (account_id, json.dumps(authorities, default=str), json.dumps(ip_whitelist, default=str), _now()),
        )


def latest_permission_snapshot(db: Database, account_id: int) -> dict | None:
    with db.cursor() as cur:
        cur.execute(
            "SELECT authorities, ip_whitelist FROM permission_snapshots "
            "WHERE account_id = ? ORDER BY snapshot_at DESC LIMIT 1",
            (account_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "authorities": json.loads(row["authorities"]) if row["authorities"] else None,
        "ip_whitelist": json.loads(row["ip_whitelist"]) if row["ip_whitelist"] else None,
    }


def record_traded_pair(db: Database, account_id: int, symbol: str) -> bool:
    """Returns True if this symbol is new to the account's trading baseline."""
    if not symbol:
        return False
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO traded_pairs (account_id, symbol, first_seen_at) VALUES (?, ?, ?) "
            "ON CONFLICT(account_id, symbol) DO NOTHING",
            (account_id, symbol, _now()),
        )
        return cur.rowcount == 1


def known_traded_pairs(db: Database, account_id: int) -> set[str]:
    with db.cursor() as cur:
        cur.execute("SELECT symbol FROM traded_pairs WHERE account_id = ?", (account_id,))
        return {r["symbol"] for r in cur.fetchall()}


def record_seen_fill(db: Database, account_id: int, fill: dict) -> bool:
    """Returns True the first time this fill is offered, False every time
    after. The poller uses it to price-check and fee-check a fill once,
    whether or not the exchange dated it."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO seen_fills (account_id, fill_key, symbol, first_seen_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(account_id, fill_key) DO NOTHING",
            (account_id, fill_identity(fill), fill.get("symbol"), _now()),
        )
        return cur.rowcount == 1


def record_seen_transfer(db: Database, account_id: int, transfer: dict) -> bool:
    """Returns True if this transfer record hasn't been processed before."""
    transfer_id = str(transfer.get("id") or "")
    if not transfer_id:
        return False
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO seen_transfers (account_id, transfer_id, raw_json, first_seen_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(account_id, transfer_id) DO NOTHING",
            (account_id, transfer_id, json.dumps(transfer, default=str), _now()),
        )
        return cur.rowcount == 1


def record_transfer_route(db: Database, account_id: int, route: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO transfer_routes (account_id, route, first_seen_at) VALUES (?, ?, ?) "
            "ON CONFLICT(account_id, route) DO NOTHING",
            (account_id, route, _now()),
        )


def known_transfer_routes(db: Database, account_id: int) -> set[str]:
    with db.cursor() as cur:
        cur.execute("SELECT route FROM transfer_routes WHERE account_id = ?", (account_id,))
        return {r["route"] for r in cur.fetchall()}


def load_fee_baseline(db: Database, account_id: int) -> dict:
    with db.cursor() as cur:
        cur.execute("SELECT ema_json FROM fee_baseline WHERE account_id = ?", (account_id,))
        row = cur.fetchone()
    if row is None:
        return {}
    return {coin: to_decimal(v) for coin, v in json.loads(row["ema_json"]).items()}


def save_fee_baseline(db: Database, account_id: int, ema: dict) -> None:
    payload = json.dumps({coin: str(v) for coin, v in ema.items()})
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO fee_baseline (account_id, ema_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(account_id) DO UPDATE SET ema_json = excluded.ema_json, updated_at = excluded.updated_at",
            (account_id, payload, _now()),
        )


def seed_baseline(db: Database, account_id: int, client: ExchangeClient) -> None:
    """Called once at onboarding. Builds the initial 'known good' picture so
    the first fast-loop poll doesn't fire CRITICAL alerts for history that
    predates monitoring (pre-existing withdrawals/addresses are baseline,
    not anomalies)."""
    balance = client.fetch_balance()
    totals = balance.get("total", {}) if isinstance(balance, dict) else {}
    record_balance_snapshot(db, account_id, {k: v for k, v in totals.items() if v})

    for record in client.fetch_deposits():
        record_known_address(db, account_id, record.get("address"), record.get("currency"), "deposit_history")

    for record in client.fetch_withdrawals():
        record_known_address(db, account_id, record.get("address"), record.get("currency"), "withdrawal_history")
        record_seen_withdrawal(db, account_id, record)

    # Seed the trading-pair baseline from order history so active traders
    # (incl. AI futures accounts) don't false-positive on day one.
    try:
        for order in client.fetch_closed_orders():
            record_traded_pair(db, account_id, order.get("symbol"))
    except Exception:  # noqa: BLE001 - order history is a best-effort seed
        pass

    # Seed transfer history the same way: internal routes the customer already
    # uses (spot->funding, spot->p2p, ...) are baseline, not first-poll HIGH
    # alerts. Seeds twice the poller's default sweep width (detectors.transfers
    # .max_coins_per_cycle, default 5) so a modest raise of that knob doesn't
    # expose unseeded coins whose old transfers would false-alert.
    nonzero = {k: v for k, v in totals.items() if v}
    top_coins = sorted(nonzero, key=lambda c: to_decimal(nonzero[c]), reverse=True)[:10]
    for coin in top_coins:
        try:
            for transfer in client.fetch_transfers(coin):
                record_seen_transfer(db, account_id, transfer)
                route = f"{transfer.get('fromAccount') or '?'}->{transfer.get('toAccount') or '?'}"
                record_transfer_route(db, account_id, route)
        except Exception:  # noqa: BLE001 - transfer history is a best-effort seed
            continue

    info = client.fetch_account_info()
    payload = info.get("data", info) if isinstance(info, dict) else info
    if isinstance(payload, dict):
        record_permission_snapshot(db, account_id, payload.get("authorities"), payload.get("ips"))
