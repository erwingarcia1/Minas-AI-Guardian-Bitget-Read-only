from __future__ import annotations

import contextlib
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class Database:
    """Thin SQLite wrapper. Postgres replaces this in Phase 2 at scale;
    the schema is deliberately simple SQL so that move isn't a rewrite."""

    # Long enough to ride out a slow write on a contended file, short enough
    # that a genuinely wedged database still fails a poll rather than hanging a
    # worker forever. Contention here is milliseconds; anything near this
    # ceiling is a real problem and should surface as one.
    BUSY_TIMEOUT_MS = 5000

    def __init__(self, path: str | Path):
        self._path = Path(path)
        # `:memory:` is per-connection - a second connection is a second, empty
        # database - so it cannot use the thread-local pool below. Tests and
        # one-shot CLI use it; the poller never does.
        self._shared = str(self._path) == ":memory:"
        if not self._shared:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._all_conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        # Serialises writers. With WAL, readers no longer queue behind this -
        # which is the whole point: one shared connection meant every read in
        # the pool waited on every other read (G-45).
        self._write_lock = threading.Lock()
        self._conn = self._new_connection()
        if self._shared:
            self._local.conn = self._conn
        self._init_schema()

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self.BUSY_TIMEOUT_MS}")
        if not self._shared:
            # WAL is what lets a reader proceed while a write is in flight.
            # It is a property of the database file, so setting it on any
            # connection sets it for all of them - repeated here only so a
            # fresh file gets it before first use.
            conn.execute("PRAGMA journal_mode = WAL")
            # NORMAL is the documented companion to WAL: durable across process
            # crashes, and only at risk of losing the last transactions in a
            # power loss. Every table this writes is a snapshot or an
            # append-only observation that the next poll re-derives, so that
            # trade is worth the fsync per write it saves under a worker pool.
            conn.execute("PRAGMA synchronous = NORMAL")
        with self._conns_lock:
            self._all_conns.append(conn)
        return conn

    def _connection(self) -> sqlite3.Connection:
        """This thread's connection, created on first use.

        Per-thread rather than shared, so concurrent readers don't serialise.
        `sqlite3` objects are not safe to share across threads without care,
        and the previous single connection needed a lock around every access to
        be safe - which made the lock the bottleneck."""
        if self._shared:
            return self._conn
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_connection()
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        with open(SCHEMA_PATH, "r") as f:
            self._conn.executescript(f.read())
        # Columns added after the MVP schema shipped; ALTER is a no-op error
        # on databases that already have them. (CREATE TABLE IF NOT EXISTS never
        # alters an existing table, so multi-exchange support needs these on any
        # DB onboarded before the columns existed - e.g. the deployed Bitget one.)
        migrations = (
            "ALTER TABLE alerts ADD COLUMN escalated_at TEXT",
            "ALTER TABLE accounts ADD COLUMN exchange TEXT NOT NULL DEFAULT 'bitget'",
            "ALTER TABLE accounts ADD COLUMN alert_email TEXT",
            "ALTER TABLE accounts ADD COLUMN alert_phone TEXT",
            "ALTER TABLE accounts ADD COLUMN customer_name TEXT",
            "ALTER TABLE accounts ADD COLUMN alert_email_secondary TEXT",
            "ALTER TABLE accounts ADD COLUMN alert_phone_secondary TEXT",
            "ALTER TABLE accounts ADD COLUMN telegram_handle TEXT",
            "ALTER TABLE accounts ADD COLUMN telegram_handle_secondary TEXT",
            "ALTER TABLE accounts ADD COLUMN license TEXT",
            "ALTER TABLE accounts ADD COLUMN sms_alerts_enabled INTEGER NOT NULL DEFAULT 1",
        )
        for ddl in migrations:
            # Already-applied ALTERs raise; that is the no-op case, not a failure.
            with contextlib.suppress(sqlite3.OperationalError):
                self._conn.execute(ddl)
        self._conn.commit()

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        """A cursor on this thread's connection, committed on clean exit.

        The write lock is still held for the whole block. SQLite allows one
        writer at a time regardless, and taking it here means a busy write
        waits on a Python lock rather than burning the busy_timeout and
        surfacing as SQLITE_BUSY. What changed with WAL is that this no longer
        blocks *readers* on other connections, which is where the contention
        actually was: eight workers queueing to read the same baseline.

        It stays a single lock rather than a read/write split because the block
        is opaque - a caller can read and then write inside one `with`, and
        `record_seen_withdrawal` does exactly that. Classifying by intent would
        need every call site audited, and getting it wrong reintroduces a race
        in the code that decides whether a withdrawal is new."""
        conn = self._connection()
        with self._write_lock:
            cur = conn.cursor()
            try:
                yield cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()

    def close(self) -> None:
        """Closes every connection this database handed out, not just the
        caller's. A thread-local pool would otherwise leak file handles for
        each worker thread that ever touched it."""
        with self._conns_lock:
            conns, self._all_conns = self._all_conns, []
        for conn in conns:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
