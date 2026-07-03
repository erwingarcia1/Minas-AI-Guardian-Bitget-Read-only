from __future__ import annotations

import asyncio

from sentinel.ws_monitor import WebSocketMonitor


class FakeExchange:
    """Mimics a ccxt.pro exchange: watch_* blocks until an event or raises
    after injected failures. close() is tracked for watchdog assertions."""

    def __init__(self):
        self.closed_count = 0
        self.order_events = asyncio.Queue()

    async def watch_orders(self):
        return await self.order_events.get()

    async def watch_my_trades(self):
        await asyncio.sleep(3600)  # silent forever

    async def watch_balance(self):
        await asyncio.sleep(3600)

    async def close(self):
        self.closed_count += 1


def test_events_are_delivered_and_reconnect_survives_errors():
    async def scenario():
        exchange = FakeExchange()
        received = []
        monitor = WebSocketMonitor(exchange, lambda kind, payload: received.append((kind, payload)))
        task = asyncio.create_task(monitor.run())

        await exchange.order_events.put([{"id": "o1"}])
        await asyncio.sleep(0.05)
        assert ("order", [{"id": "o1"}]) in received

        monitor.stop()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())


def test_watchdog_detects_silent_stream_death():
    async def scenario():
        exchange = FakeExchange()
        monitor = WebSocketMonitor(exchange, lambda k, p: None, stale_after_seconds=0.2)
        task = asyncio.create_task(monitor.run())

        # No messages arrive at all -> watchdog must force a close/reconnect.
        await asyncio.sleep(0.5)
        assert exchange.closed_count >= 1

        monitor.stop()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
