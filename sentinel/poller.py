from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sentinel.alerts.dispatch import dispatch
from sentinel.baseline import (
    known_traded_pairs,
    known_transfer_routes,
    latest_balance_snapshot,
    latest_balance_snapshot_at,
    latest_permission_snapshot,
    load_fee_baseline,
    prune_balance_snapshots,
    record_balance_snapshot,
    record_known_address,
    record_permission_snapshot,
    record_seen_transfer,
    record_seen_withdrawal,
    record_traded_pair,
    record_transfer_route,
    save_fee_baseline,
)
from sentinel.config import RulesConfig
from sentinel.crypto import CredentialCipher
from sentinel.db import Database
from sentinel.detectors.burst import detect_withdrawal_burst
from sentinel.detectors.fee_anomaly import detect_fee_anomaly, sum_fees_by_coin, update_ema
from sentinel.detectors.heartbeat import detect_baseline_drift, detect_unreachable
from sentinel.detectors.new_api_key import detect_permission_state_change
from sentinel.detectors.new_pair import detect_new_pair_activity
from sentinel.detectors.off_market import detect_off_market_fills
from sentinel.detectors.quantity_drop import detect_quantity_drop
from sentinel.detectors.tamper import detect_tamper
from sentinel.detectors.transfer_pattern import detect_new_transfer_route
from sentinel.detectors.unseen_address import detect_unseen_address
from sentinel.detectors.withdrawal import detect_new_withdrawals
from sentinel.events import append_event
from sentinel.exchange import ExchangeClient, TamperSignal, make_client
from sentinel.numeric import to_decimal

logger = logging.getLogger("sentinel.poller")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _load_client(cipher: CredentialCipher, row) -> ExchangeClient:
    # `exchange` may be absent on rows read from a pre-migration DB snapshot;
    # default to bitget to match the historical single-exchange behavior.
    exchange = row["exchange"] if "exchange" in row.keys() else "bitget"
    return make_client(
        exchange,
        cipher.decrypt(row["encrypted_api_key"]),
        cipher.decrypt(row["encrypted_secret"]),
        cipher.decrypt(row["encrypted_passphrase"]),
    )


def _get_poll_state(db: Database, account_id: int) -> dict:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM poll_state WHERE account_id = ?", (account_id,))
        row = cur.fetchone()
    return dict(row) if row else {"consecutive_auth_failures": 0, "consecutive_failures": 0}


def _update_poll_state(db: Database, account_id: int, **fields) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with db.cursor() as cur:
        cur.execute(
            f"UPDATE poll_state SET {set_clause} WHERE account_id = ?",
            (*fields.values(), account_id),
        )


class AccountPoller:
    """Runs the fast (money movement + trading) and slow (config/security
    snapshot) loops for a single account. Driven either sequentially by the
    CLI's `run` command or concurrently by the worker pool in
    sentinel/engine.py; per-call rate limiting is handled by the engine via
    the optional `limiter`."""

    def __init__(self, db: Database, cipher: CredentialCipher, rules: RulesConfig, channels: dict, limiter=None, metrics=None, contact_router=None):
        self._db = db
        self._cipher = cipher
        self._rules = rules
        self._channels = channels
        self._limiter = limiter
        self._metrics = metrics
        self._contact_router = contact_router

    def _account_contact(self, account_id: int) -> tuple[list[str], list[str]]:
        with self._db.cursor() as cur:
            cur.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
            row = cur.fetchone()
        if row is None:
            return [], []
        keys = row.keys()

        def val(k):
            return row[k] if k in keys else None

        emails = [val("alert_email"), val("alert_email_secondary")]
        phones = [val("alert_phone"), val("alert_phone_secondary")]
        return [e for e in emails if e], [p for p in phones if p]

    def _emit(self, account_id: int, label: str, detection) -> None:
        append_event(self._db, account_id, detection.rule, detection.severity, detection.evidence)
        if self._metrics is not None:
            self._metrics.detection_fired(detection.rule, detection.severity)
        cooldowns = {
            sev: self._rules.get_int("alerting", "cooldown_minutes", sev, default=60)
            for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
        }
        alert_id = dispatch(self._db, self._channels, account_id, label, detection, cooldowns)
        if alert_id is not None:
            if self._metrics is not None:
                self._metrics.alert_sent(detection.severity)
            # Per-account destination fires only when the alert actually fired
            # (dedup/cooldown passed), reusing dispatch's dedup semantics. The
            # router is called even with no contacts on file so a CRITICAL
            # with no SMS destination is logged for follow-up (AC: missing
            # phone handling), and the alert id links sms_deliveries rows
            # back to the stored alert.
            if self._contact_router is not None:
                emails, phones = self._account_contact(account_id)
                self._contact_router.send(
                    detection, label, emails, phones,
                    account_id=account_id, alert_id=alert_id,
                )

    def _acquire(self, account_id: int, calls: float) -> None:
        if self._limiter is not None:
            self._limiter.acquire_for_call(account_id, calls)

    def run_fast_loop(self, account_row) -> None:
        account_id = account_row["id"]
        label = account_row["label"]
        state = _get_poll_state(self._db, account_id)
        last_run_iso = state.get("last_fast_loop_at")

        # Hints for per-symbol exchanges (Binance): pairs this account is known
        # to have traded, plus coins held on the previous poll - so a coin sold
        # to zero still gets its sell fills pulled and reconciled.
        pair_hints = sorted(known_traded_pairs(self._db, account_id))
        coin_hints = sorted(latest_balance_snapshot(self._db, account_id))

        def charge(calls: float = 1.0) -> None:
            # Rate-limiter hook for clients that fan one logical pull into
            # several HTTP calls (Bybit/OKX categories, Binance per-symbol) -
            # keeps the egress-IP budget honest about real request volume.
            self._acquire(account_id, calls)

        try:
            client = _load_client(self._cipher, account_row)
            self._acquire(account_id, 4)
            deposits = client.fetch_deposits()
            withdrawals = client.fetch_withdrawals()
            fills = client.fetch_closed_orders(symbol_hints=pair_hints, coin_hints=coin_hints, acquire=charge)
            balance = client.fetch_balance()
        except TamperSignal as exc:
            self._handle_auth_failure(account_id, label, state, exc)
            return
        except Exception as exc:  # noqa: BLE001 - one bad account must never stall the fleet
            self._handle_generic_failure(account_id, label, state, exc)
            return

        _update_poll_state(
            self._db,
            account_id,
            consecutive_auth_failures=0,
            consecutive_failures=0,
            last_success_at=_now_iso(),
            last_fast_loop_at=_now_iso(),
        )

        # --- Withdrawals: creation, unseen address, burst -------------------
        new_withdrawals = [w for w in withdrawals if record_seen_withdrawal(self._db, account_id, w)]

        for detection in detect_new_withdrawals(new_withdrawals):
            self._emit(account_id, label, detection)

        with self._db.cursor() as cur:
            cur.execute("SELECT address FROM known_addresses WHERE account_id = ?", (account_id,))
            known = {r["address"] for r in cur.fetchall()}

        for detection in detect_unseen_address(new_withdrawals, known):
            self._emit(account_id, label, detection)

        if new_withdrawals:
            window_minutes = self._rules.get_int("detectors", "withdrawal_burst", "window_minutes", default=60)
            min_count = self._rules.get_int("detectors", "withdrawal_burst", "min_count", default=3)
            with self._db.cursor() as cur:
                cur.execute(
                    "SELECT first_seen_at FROM seen_withdrawals WHERE account_id = ?",
                    (account_id,),
                )
                times = [datetime.fromisoformat(r["first_seen_at"]) for r in cur.fetchall()]
            for detection in detect_withdrawal_burst(times, window_minutes, min_count, _now()):
                self._emit(account_id, label, detection)

        # Record this cycle's addresses into history only after judging them
        # against prior history, so a withdrawal is never compared to itself.
        new_deposit_addresses = []
        for record in deposits:
            if record_known_address(self._db, account_id, record.get("address"), record.get("currency"), "deposit_history"):
                new_deposit_addresses.append(record.get("address"))
        for w in new_withdrawals:
            record_known_address(self._db, account_id, w.get("address"), w.get("currency"), "withdrawal_history")

        for detection in detect_baseline_drift(new_deposit_addresses):
            self._emit(account_id, label, detection)

        # --- Trading: new pairs, off-market fills, fee anomaly --------------
        known_pairs = known_traded_pairs(self._db, account_id)
        for detection in detect_new_pair_activity(fills, known_pairs):
            self._emit(account_id, label, detection)
        for fill in fills:
            record_traded_pair(self._db, account_id, fill.get("symbol"))

        # Only fills newer than the previous fast loop are re-judged for
        # off-market execution and fee spend; older ones were already seen.
        recent_fills = fills
        if last_run_iso:
            last_run_ms = datetime.fromisoformat(last_run_iso).timestamp() * 1000
            recent_fills = [f for f in fills if (f.get("timestamp") or 0) >= last_run_ms]

        if recent_fills:
            symbols = sorted({f.get("symbol") for f in recent_fills if f.get("symbol")})
            reference_prices: dict[str, Decimal] = {}
            if symbols:
                tickers: dict = {}
                try:
                    self._acquire(account_id, 1)
                    tickers = client.fetch_tickers(symbols)
                except Exception as exc:  # noqa: BLE001 - public data failure must not kill the loop
                    # Bybit rejects a batch that mixes spot and derivatives
                    # symbols; fall back per-symbol so off-market detection
                    # still runs instead of silently skipping the cycle.
                    logger.warning("batch ticker fetch failed for %s (%s) - falling back to per-symbol", label, exc)
                    for sym in symbols[:10]:
                        try:
                            self._acquire(account_id, 1)
                            tickers[sym] = client.fetch_ticker(sym)
                        except Exception as sym_exc:  # noqa: BLE001
                            logger.warning("ticker fetch failed for %s/%s: %s", label, sym, sym_exc)
                for sym, ticker in tickers.items():
                    last = to_decimal(ticker.get("last") or ticker.get("close"))
                    if last > 0:
                        reference_prices[sym] = last

            max_dev = self._rules.get_decimal("detectors", "off_market", "max_deviation_pct", default=Decimal("10"))
            for detection in detect_off_market_fills(recent_fills, reference_prices, max_dev):
                self._emit(account_id, label, detection)

            window_fees = sum_fees_by_coin(recent_fills)
            ema = load_fee_baseline(self._db, account_id)
            multiplier = self._rules.get_decimal("detectors", "fee_anomaly", "multiplier", default=Decimal("5"))
            floor = self._rules.get_decimal("detectors", "fee_anomaly", "min_fee_floor", default=Decimal("1"))
            alpha = self._rules.get_decimal("detectors", "fee_anomaly", "ema_alpha", default=Decimal("0.2"))
            for detection in detect_fee_anomaly(window_fees, ema, multiplier, floor):
                self._emit(account_id, label, detection)
            for coin, spent in window_fees.items():
                ema[coin] = update_ema(ema.get(coin), spent, alpha)
            if window_fees:
                save_fee_baseline(self._db, account_id, ema)

        # --- Transfers: unfamiliar routes -----------------------------------
        totals = balance.get("total", {}) if isinstance(balance, dict) else {}
        current_balances = {k: v for k, v in totals.items() if v}

        max_coins = self._rules.get_int("detectors", "transfers", "max_coins_per_cycle", default=5)
        top_coins = sorted(current_balances, key=lambda c: to_decimal(current_balances[c]), reverse=True)[:max_coins]
        new_transfers = []
        for coin in top_coins:
            try:
                self._acquire(account_id, 1)
                for transfer in client.fetch_transfers(coin, acquire=charge):
                    if record_seen_transfer(self._db, account_id, transfer):
                        new_transfers.append(transfer)
            except Exception as exc:  # noqa: BLE001 - per-coin; a bad coin must not kill the loop
                logger.warning("transfer fetch failed for %s/%s: %s", label, coin, exc)

        if new_transfers:
            routes = known_transfer_routes(self._db, account_id)
            for detection in detect_new_transfer_route(new_transfers, routes):
                self._emit(account_id, label, detection)
            for transfer in new_transfers:
                route = f"{transfer.get('fromAccount') or '?'}->{transfer.get('toAccount') or '?'}"
                record_transfer_route(self._db, account_id, route)

        # --- Balance: unexplained quantity decrease --------------------------
        previous_balances, snapshot_at = latest_balance_snapshot_at(self._db, account_id)
        current_decimal = {k: to_decimal(v) for k, v in current_balances.items()}

        tolerance_pct = self._rules.get_decimal("detectors", "quantity_drop", "tolerance_pct")
        tolerance_abs = self._rules.get_decimal("detectors", "quantity_drop", "tolerance_absolute")

        # The drop is measured from the previous balance snapshot, so only
        # activity at or after that snapshot can explain it. `withdrawals` and
        # `fills` are fetched with since=None and carry the exchange's full
        # default history - days of it - so they must be windowed here. Without
        # this, a legitimate sale from last week cancels out a theft from the
        # last 45 seconds and nothing is ever alerted (G-7).
        window_start_ms = None
        if snapshot_at is not None:
            grace = self._rules.get_decimal(
                "detectors", "quantity_drop", "explanation_grace_seconds", default=Decimal("60")
            )
            window_start = datetime.fromisoformat(snapshot_at) - timedelta(seconds=float(grace))
            window_start_ms = int(window_start.timestamp() * 1000)

        for detection in detect_quantity_drop(
            previous_balances,
            current_decimal,
            withdrawals,
            fills,
            tolerance_pct,
            tolerance_abs,
            window_start_ms=window_start_ms,
            exchange=label,
            metrics=self._metrics,
        ):
            self._emit(account_id, label, detection)

        record_balance_snapshot(self._db, account_id, current_balances)
        if self._metrics is not None:
            self._metrics.poll_ok()

    def run_slow_loop(self, account_row) -> None:
        account_id = account_row["id"]
        label = account_row["label"]
        try:
            client = _load_client(self._cipher, account_row)
            self._acquire(account_id, 1)
            info = client.fetch_account_info()
        except TamperSignal as exc:
            state = _get_poll_state(self._db, account_id)
            self._handle_auth_failure(account_id, label, state, exc)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("slow loop failed for %s: %s", label, exc)
            return

        payload = info.get("data", info) if isinstance(info, dict) else info
        current = {
            "authorities": payload.get("authorities") if isinstance(payload, dict) else None,
            "ip_whitelist": payload.get("ips") if isinstance(payload, dict) else None,
        }
        previous = latest_permission_snapshot(self._db, account_id)
        for detection in detect_permission_state_change(previous, current):
            self._emit(account_id, label, detection)

        record_permission_snapshot(self._db, account_id, current["authorities"], current["ip_whitelist"])
        # Housekeeping on the slow cadence: balance snapshots older than the
        # retention window only bloat the table the fast loop queries.
        prune_balance_snapshots(self._db, account_id)
        _update_poll_state(self._db, account_id, last_slow_loop_at=_now_iso())

    def _handle_auth_failure(self, account_id: int, label: str, state: dict, exc: Exception) -> None:
        failures = state.get("consecutive_auth_failures", 0) + 1
        _update_poll_state(self._db, account_id, consecutive_auth_failures=failures, consecutive_failures=0)
        threshold = self._rules.get_int("detectors", "tamper", "consecutive_auth_failures_threshold", default=2)
        for detection in detect_tamper(failures, threshold):
            self._emit(account_id, label, detection)
        if self._metrics is not None:
            self._metrics.poll_failed(auth=True)
        logger.warning("auth/permission failure for %s (%d consecutive): %s", label, failures, exc)

    def _handle_generic_failure(self, account_id: int, label: str, state: dict, exc: Exception) -> None:
        failures = state.get("consecutive_failures", 0) + 1
        _update_poll_state(self._db, account_id, consecutive_failures=failures)
        threshold = self._rules.get_int("detectors", "heartbeat", "consecutive_failures_threshold", default=5)
        for detection in detect_unreachable(failures, threshold):
            self._emit(account_id, label, detection)
        if self._metrics is not None:
            self._metrics.poll_failed(auth=False)
        logger.warning("poll failure for %s (%d consecutive): %s", label, failures, exc)


def run_forever(db: Database, cipher: CredentialCipher, rules: RulesConfig, channels: dict) -> None:
    """Sequential single-process loop, kept for small deployments and
    debugging. The scaled path is sentinel/engine.py (worker pool +
    rate limiting + circuit breakers)."""
    poller = AccountPoller(db, cipher, rules, channels)
    last_slow_loop: dict[int, float] = {}

    while True:
        with db.cursor() as cur:
            cur.execute("SELECT * FROM accounts WHERE status = 'active'")
            accounts = cur.fetchall()

        for account_row in accounts:
            poller.run_fast_loop(account_row)
            account_id = account_row["id"]
            slow_interval = rules.get_seconds("polling", "slow_loop_seconds", default=600)
            now = time.monotonic()
            if now - last_slow_loop.get(account_id, 0) >= slow_interval:
                poller.run_slow_loop(account_row)
                last_slow_loop[account_id] = now

        jitter = rules.get_seconds("polling", "jitter_seconds", default=0)
        fast_interval = rules.get_seconds("polling", "fast_loop_seconds", default=45)
        time.sleep(fast_interval + random.uniform(0, jitter))
