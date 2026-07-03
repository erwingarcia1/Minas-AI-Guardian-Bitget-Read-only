from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger("sentinel.ws")


class WebSocketMonitor:
    """Near-real-time order/fill/balance stream for one account via
    ccxt.pro's watch* methods (channels confirmed against source in
    docs/phase0-capability-audit.md: 'orders', 'fill', 'account').

    Design constraints from Phase 0:
    - Bitget has NO WS channel for withdrawals/deposits/transfers, so this
      never replaces the REST fast loop - it only accelerates trading
      signals between polls. REST remains the source of truth.
    - Bitget WS auth is a signed per-key login; connections cannot be
      pooled across customers. One monitor = one account = one connection.
    - Silent stream death is detected by a watchdog: no message (including
      heartbeats handled inside ccxt.pro) within `stale_after_seconds`
      forces a reconnect. Silence never gets interpreted as 'no events'.
    """

    def __init__(self, exchange, on_event, stale_after_seconds: float = 120.0, max_backoff: float = 300.0):
        # `exchange` is a ccxt.pro exchange instance (ccxt.pro.bitget) owned
        # by the caller; `on_event(kind, payload)` receives 'order'/'trade'/
        # 'balance' events for the detectors.
        self._exchange = exchange
        self._on_event = on_event
        self._stale_after = stale_after_seconds
        self._max_backoff = max_backoff
        self._last_message_at = time.monotonic()
        self._stop = asyncio.Event()

    def _mark_alive(self) -> None:
        self._last_message_at = time.monotonic()

    @property
    def seconds_since_last_message(self) -> float:
        return time.monotonic() - self._last_message_at

    async def _watch_loop(self, method_name: str, kind: str) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                method = getattr(self._exchange, method_name)
                result = await method()
                self._mark_alive()
                backoff = 1.0
                self._on_event(kind, result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect with backoff, never die
                logger.warning("%s stream error (%s); reconnecting in %.0fs", kind, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)

    async def _watchdog(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._stale_after / 4)
            if self.seconds_since_last_message > self._stale_after:
                logger.warning(
                    "no WS message for %.0fs - stream presumed dead, forcing reconnect",
                    self.seconds_since_last_message,
                )
                try:
                    await self._exchange.close()  # watch loops will reconnect
                except Exception as exc:  # noqa: BLE001
                    logger.warning("error closing stale connection: %s", exc)
                self._mark_alive()  # reset so we don't hammer close() in a loop

    async def run(self) -> None:
        tasks = [
            asyncio.create_task(self._watch_loop("watch_orders", "order")),
            asyncio.create_task(self._watch_loop("watch_my_trades", "trade")),
            asyncio.create_task(self._watch_loop("watch_balance", "balance")),
            asyncio.create_task(self._watchdog()),
        ]
        try:
            await self._stop.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await self._exchange.close()
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        self._stop.set()
