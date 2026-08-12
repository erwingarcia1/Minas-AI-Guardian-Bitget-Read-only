from __future__ import annotations

import logging
import queue
import random
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal

from sentinel.circuit import BreakerRegistry
from sentinel.config import RulesConfig
from sentinel.crypto import CredentialCipher
from sentinel.db import Database
from sentinel.poller import AccountPoller
from sentinel.ratelimit import RateLimiters

logger = logging.getLogger("sentinel.engine")


@dataclass(order=True)
class Task:
    """A unit of per-account work. Idempotent by construction: fast/slow
    loops diff against stored state, so re-running one is harmless."""

    due_at: float
    account_id: int = field(compare=False)
    kind: str = field(compare=False)  # 'fast' | 'slow'


class Engine:
    """Scheduler -> queue -> worker pool. In-process threads + queue.Queue
    for Phase 2; the Task shape and idempotency contract are designed so a
    Redis-backed distributed queue is a drop-in replacement when one process
    stops being enough (multi-host scale-out is the documented path in
    docs/deployment.md)."""

    def __init__(
        self,
        db: Database,
        cipher: CredentialCipher,
        rules: RulesConfig,
        channels: dict,
        metrics=None,
        escalator=None,
        operator_alerter=None,
    ):
        self._db = db
        self._cipher = cipher
        self._rules = rules
        self._metrics = metrics
        self._escalator = escalator
        # Paging scoped to Minas (G-49). Held here so the conditions that need
        # it - the untimestamped counter rising (#52), a failed drill (#37),
        # the scheduler falling behind (#42) - have somewhere to raise from.
        # Those callers belong to their own issues; this is the route.
        self._operator_alerter = operator_alerter
        self._queue: queue.Queue[Task] = queue.Queue()
        self._stop = threading.Event()

        def _rate(key: str, default: str) -> float:
            return float(rules.get_decimal("rate_limits", key, default=Decimal(default)))

        self.limiters = RateLimiters(
            per_account_rps=_rate("per_account_rps", "5"),
            per_account_burst=_rate("per_account_burst", "10"),
            ip_rps=_rate("egress_ip_rps", "60"),
            ip_burst=_rate("egress_ip_burst", "100"),
        )
        self.breakers = BreakerRegistry(
            failure_threshold=rules.get_int("circuit_breaker", "failure_threshold", default=3),
            base_backoff_seconds=rules.get_seconds("circuit_breaker", "base_backoff_seconds", default=60),
            max_backoff_seconds=rules.get_seconds("circuit_breaker", "max_backoff_seconds", default=3600),
        )
        from sentinel.alerts.contact import ContactRouter

        self._poller = AccountPoller(
            db, cipher, rules, channels, limiter=self.limiters, metrics=metrics,
            contact_router=ContactRouter(db=db, rules=rules),
        )
        self._last_slow: dict[int, float] = {}

    # --- scheduler -----------------------------------------------------------

    def _active_accounts(self) -> list:
        with self._db.cursor() as cur:
            cur.execute("SELECT * FROM accounts WHERE status = 'active'")
            return cur.fetchall()

    def _schedule_cycle(self) -> None:
        jitter = self._rules.get_seconds("polling", "jitter_seconds", default=10)
        slow_interval = self._rules.get_seconds("polling", "slow_loop_seconds", default=600)
        now = time.monotonic()
        active = self._active_accounts()
        if self._metrics is not None:
            self._metrics.set_active_accounts(len(active))
        for row in active:
            account_id = row["id"]
            self._queue.put(Task(now + random.uniform(0, jitter), account_id, "fast"))
            if now - self._last_slow.get(account_id, 0) >= slow_interval:
                self._queue.put(Task(now + random.uniform(0, jitter), account_id, "slow"))
                self._last_slow[account_id] = now

    def _scheduler_loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._schedule_cycle()
                if self._escalator is not None:
                    self._escalator.check(self._db)
            except Exception as exc:  # noqa: BLE001 - scheduler must survive anything
                logger.error("scheduler cycle failed: %s", exc)
            # Republished every cycle rather than only on change: a scrape that
            # starts after the rejection still sees the 0, and a restart that
            # re-reads a still-broken file re-asserts it.
            if self._metrics is not None:
                self._metrics.set_rules_config_valid(self._rules.is_valid)
            fast_interval = self._rules.get_seconds("polling", "fast_loop_seconds", default=45)
            self._stop.wait(max(1.0, fast_interval - (time.monotonic() - started)))

    # --- workers -----------------------------------------------------------

    def _fetch_account(self, account_id: int):
        with self._db.cursor() as cur:
            cur.execute("SELECT * FROM accounts WHERE id = ? AND status = 'active'", (account_id,))
            return cur.fetchone()

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                task = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                delay = task.due_at - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                breaker = self.breakers.for_account(task.account_id)
                if not breaker.allow():
                    logger.info("breaker open, skipping account %d", task.account_id)
                    continue
                row = self._fetch_account(task.account_id)
                if row is None:
                    continue
                before_failures = self._consecutive_failures(task.account_id)
                if task.kind == "fast":
                    self._poller.run_fast_loop(row)
                else:
                    self._poller.run_slow_loop(row)
                if self._consecutive_failures(task.account_id) > before_failures:
                    breaker.record_failure()
                else:
                    breaker.record_success()
            except Exception as exc:  # noqa: BLE001 - one task must never kill a worker
                logger.error("task failed account=%d kind=%s: %s", task.account_id, task.kind, exc)
                self.breakers.for_account(task.account_id).record_failure()
            finally:
                self._queue.task_done()

    def _consecutive_failures(self, account_id: int) -> int:
        with self._db.cursor() as cur:
            cur.execute(
                "SELECT consecutive_failures + consecutive_auth_failures AS total "
                "FROM poll_state WHERE account_id = ?",
                (account_id,),
            )
            row = cur.fetchone()
        return row["total"] if row else 0

    # --- lifecycle -----------------------------------------------------------

    def run(self, pool_size: int | None = None) -> None:
        size = pool_size or self._rules.get_int("workers", "pool_size", default=8)
        workers = [threading.Thread(target=self._worker_loop, daemon=True, name=f"worker-{i}") for i in range(size)]
        for w in workers:
            w.start()
        scheduler = threading.Thread(target=self._scheduler_loop, daemon=True, name="scheduler")
        scheduler.start()
        logger.info("engine running: %d workers", size)
        try:
            while not self._stop.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def run_one_cycle(self) -> None:
        """Synchronous single pass (used by tests and the soak harness)."""
        self._schedule_cycle()
        while not self._queue.empty():
            task = self._queue.get()
            try:
                breaker = self.breakers.for_account(task.account_id)
                if not breaker.allow():
                    continue
                row = self._fetch_account(task.account_id)
                if row is None:
                    continue
                before_failures = self._consecutive_failures(task.account_id)
                if task.kind == "fast":
                    self._poller.run_fast_loop(row)
                else:
                    self._poller.run_slow_loop(row)
                if self._consecutive_failures(task.account_id) > before_failures:
                    breaker.record_failure()
                else:
                    breaker.record_success()
            finally:
                self._queue.task_done()

    def stop(self) -> None:
        self._stop.set()
