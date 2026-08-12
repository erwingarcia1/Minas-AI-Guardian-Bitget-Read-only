"""G-45 (#49): the database under many simultaneous checks, and never checking
one account twice at once.

Two defects, both of which get worse with exactly the thing the issue is about —
more customers.

**Contention.** One `sqlite3` connection was shared across the whole worker
pool, and `Database.cursor()` took a single lock around it. Every *read*
therefore serialised against every other read and every write, so eight workers
polling eight accounts spent their time queueing for a connection rather than
talking to exchanges. The issue notes this has already caused one incident.

**Double-checking.** The scheduler enqueued a fast task per active account
every `fast_loop_seconds` with no regard for whether the previous one had
finished. A poll slower than the interval - a slow exchange, an open circuit
breaker, a backed-up queue - meant the same account being polled concurrently
by two workers, both reading the same baseline, both judging the same records,
and both racing to write the same `poll_state`.
"""

from __future__ import annotations

import threading

import pytest

from sentinel.config import RulesConfig
from sentinel.db import Database
from sentinel.engine import Engine, Task

# --- contention --------------------------------------------------------------

def test_the_database_runs_in_wal_mode(tmp_path):
    """WAL is what lets readers proceed while a write is in flight. Without it
    a single writer blocks every reader in the pool."""
    db = Database(tmp_path / "wal.db")
    try:
        with db.cursor() as cur:
            cur.execute("PRAGMA journal_mode")
            assert cur.fetchone()[0].lower() == "wal"
    finally:
        db.close()


def test_a_busy_timeout_is_set(tmp_path):
    """With concurrent writers, SQLite returns SQLITE_BUSY rather than waiting
    unless told otherwise. A zero timeout turns ordinary contention into a
    failed poll."""
    db = Database(tmp_path / "busy.db")
    try:
        with db.cursor() as cur:
            cur.execute("PRAGMA busy_timeout")
            assert cur.fetchone()[0] > 0
    finally:
        db.close()


def test_concurrent_readers_and_writers_all_succeed(tmp_path):
    """The behavioural test. Sixteen threads hammering the same database the
    way the worker pool does - each one writing its own rows and reading them
    back - must all complete without a SQLITE_BUSY, a locked database, or a
    cross-thread connection error."""
    db = Database(tmp_path / "hammer.db")
    errors: list[Exception] = []
    threads = []

    def worker(n: int):
        try:
            for i in range(20):
                with db.cursor() as cur:
                    cur.execute(
                        "INSERT INTO accounts (label, topology, encrypted_api_key, encrypted_secret, "
                        "encrypted_passphrase, onboarded_at, updated_at) "
                        "VALUES (?, 'independent', 'x', 'y', 'z', '2026-01-01', '2026-01-01')",
                        (f"acct-{n}-{i}",),
                    )
                with db.cursor() as cur:
                    cur.execute("SELECT count(*) c FROM accounts")
                    cur.fetchone()
        except Exception as exc:  # noqa: BLE001 - the whole point is to collect them
            errors.append(exc)

    try:
        for n in range(16):
            t = threading.Thread(target=worker, args=(n,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert errors == [], f"concurrent access failed: {errors[:3]}"
        with db.cursor() as cur:
            cur.execute("SELECT count(*) c FROM accounts")
            assert cur.fetchone()["c"] == 16 * 20, "writes were lost under concurrency"
    finally:
        db.close()


def test_each_thread_gets_its_own_connection(tmp_path):
    """The mechanism behind the above. One shared connection means one lock
    around every read; per-thread connections let WAL do its job."""
    db = Database(tmp_path / "conns.db")
    seen = []

    def grab():
        seen.append(id(db._connection()))

    try:
        threads = [threading.Thread(target=grab) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(set(seen)) == 4, "worker threads are sharing one connection"
    finally:
        db.close()


def test_an_in_memory_database_still_shares_one_connection(tmp_path):
    """`:memory:` is per-connection: a second connection is a second, empty
    database. Thread-local connections must not be applied there, or a
    `Database(":memory:")` silently loses every row it wrote."""
    db = Database(":memory:")
    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO accounts (label, topology, encrypted_api_key, encrypted_secret, "
                "encrypted_passphrase, onboarded_at, updated_at) "
                "VALUES ('mem', 'independent', 'x', 'y', 'z', '2026-01-01', '2026-01-01')"
            )

        found = []

        def read():
            with db.cursor() as cur:
                cur.execute("SELECT count(*) c FROM accounts")
                found.append(cur.fetchone()["c"])

        t = threading.Thread(target=read)
        t.start()
        t.join()
        assert found == [1], "an in-memory database was not visible from another thread"
    finally:
        db.close()


# --- never twice at once -----------------------------------------------------

def _account(db, label):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, status, onboarded_at, updated_at) "
            "VALUES (?, 'independent', 'x', 'y', 'z', 'active', '2026-01-01', '2026-01-01')",
            (label,),
        )
        return cur.lastrowid


def _engine(db, cipher):
    return Engine(db, cipher, RulesConfig("config/rules.yaml"), {})


def test_a_second_scheduling_pass_does_not_requeue_a_pending_account(db, cipher):
    """The defect. The scheduler runs every `fast_loop_seconds` regardless of
    whether the last poll finished, so a poll slower than the interval - a slow
    exchange, an open breaker, a backed-up queue - gets a second worker on the
    same account."""
    _account(db, "acct-1")
    engine = _engine(db, cipher)

    engine._schedule_cycle()
    first = engine._queue.qsize()
    engine._schedule_cycle()
    engine._schedule_cycle()

    assert engine._queue.qsize() == first, (
        "the same account was queued again while its earlier task was still pending"
    )


def test_the_account_is_schedulable_again_once_its_task_completes(db, cipher):
    """The guard must release, or an account is polled once and then never
    again - which would turn a contention fix into a monitoring outage."""
    _account(db, "acct-2")
    engine = _engine(db, cipher)

    engine._schedule_cycle()
    assert engine._queue.qsize() >= 1

    while not engine._queue.empty():
        task = engine._queue.get()
        engine._release(task.account_id, task.kind)

    engine._schedule_cycle()
    assert engine._queue.qsize() >= 1, "the account was never scheduled again"


def test_fast_and_slow_loops_for_one_account_do_not_block_each_other(db, cipher):
    """They read different things and write different state, so a pending slow
    loop must not stop the fast loop that watches for withdrawals."""
    _account(db, "acct-3")
    engine = _engine(db, cipher)

    engine._schedule_cycle()
    kinds = []
    while not engine._queue.empty():
        kinds.append(engine._queue.get().kind)

    assert "fast" in kinds and "slow" in kinds


def test_a_failed_task_still_releases_the_account(db, cipher):
    """If an exception left the guard held, one bad poll would stop that
    account being checked for the lifetime of the process - a silent, permanent
    monitoring gap for one customer."""
    account_id = _account(db, "acct-4")
    engine = _engine(db, cipher)

    engine._claim(account_id, "fast")
    try:
        raise RuntimeError("poll blew up")
    except RuntimeError:
        engine._release(account_id, "fast")

    assert engine._claim(account_id, "fast") is True


def test_the_worker_releases_the_account_even_when_the_poll_raises(db, cipher, monkeypatch):
    """Same guarantee, through the real worker path rather than by hand."""
    account_id = _account(db, "acct-5")
    engine = _engine(db, cipher)

    def boom(row):
        raise RuntimeError("exchange exploded")

    monkeypatch.setattr(engine._poller, "run_fast_loop", boom)

    engine._claim(account_id, "fast")
    engine._queue.put(Task(0.0, account_id, "fast"))

    # The synchronous path lets the error out - the worker thread is what
    # swallows it, deliberately, so one bad account can't kill a worker. What
    # matters here is that the claim is released either way.
    with pytest.raises(RuntimeError):
        engine._drain_once()

    assert engine._claim(account_id, "fast") is True, (
        "a raising poll left the account permanently unschedulable"
    )
