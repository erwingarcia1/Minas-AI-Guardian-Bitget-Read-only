from __future__ import annotations

import contextlib
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _migration_already_applied(exc: sqlite3.OperationalError) -> bool:
    """True only for the ordinary no-op: a column that is already there.

    Every open runs every migration, so re-adding an existing column raising is
    expected. The problem was that it raises the *same exception class* as a
    migration that genuinely failed - a renamed table, a locked database, a
    typo in the DDL - and suppressing the class made those indistinguishable.
    A database that had not actually migrated then started the service anyway,
    and every query written against the new shape failed somewhere far from the
    cause (#58, group D).

    Matching on the message is narrow on purpose. An error this function does
    not recognise is a reason to stop, not to guess: the cost of stopping is a
    failed start an operator can read, and the cost of guessing is a schema
    nobody knows is wrong."""
    return "duplicate column name" in str(exc).lower()


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
            "ALTER TABLE alerts ADD COLUMN delivered_to INTEGER NOT NULL DEFAULT 0",
            # Nullable on purpose (G-50): a detector firing has no person behind
            # it, and existing rows are not backfilled with a guess.
            "ALTER TABLE alerts ADD COLUMN acknowledged_by TEXT",
            # Set when a customer had snoozed this alert, so it was detected and
            # recorded but deliberately not delivered. Distinct from
            # delivered_to = 0, which means we tried and reached nobody (#57).
            "ALTER TABLE alerts ADD COLUMN snoozed_until TEXT",
            # Bumped whenever a member's password changes. Baked into every
            # session cookie and checked on every request, so raising it
            # retires every cookie ever issued to that member - the only way to
            # "sign out all other sessions" when sessions are stateless HMACs
            # with nothing server-side to delete (#33).
            "ALTER TABLE portal_members ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 1",
            # #24 alert preferences; NULL/defaults reproduce pre-#24 behaviour.
            "ALTER TABLE portal_members ADD COLUMN timezone TEXT",
            "ALTER TABLE portal_members ADD COLUMN quiet_hours_enabled INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE portal_members ADD COLUMN quiet_start TEXT NOT NULL DEFAULT '22:00'",
            "ALTER TABLE portal_members ADD COLUMN quiet_end TEXT NOT NULL DEFAULT '07:00'",
            "ALTER TABLE portal_members ADD COLUMN channel_prefs_json TEXT",
            "ALTER TABLE operators ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE events ADD COLUMN actor TEXT",
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
            # The exchange's own id for this account, learned from an
            # account-info payload (#69). Nullable, and NULL means "we have not
            # been told yet" rather than "no id" - every account onboarded
            # before this column existed starts NULL and fills itself in on its
            # next slow loop. Self-serve key replacement refuses while it is
            # NULL, because with nothing to compare against a replacement key
            # could point us at a different account entirely.
            "ALTER TABLE accounts ADD COLUMN exchange_uid TEXT",
            # #90: contact columns store the canonical form - emails
            # lowercased, blanks as NULL (the falsy-sentinel rule: '' stored
            # as a value is an absence some queries can accidentally match).
            # Writers canonicalize at the boundary now; these catch every row
            # written before that, and - being idempotent UPDATEs that run on
            # every open - re-catch anything a future non-canonical writer
            # slips in before the guard tests catch the writer itself.
            "UPDATE accounts SET alert_email = NULLIF(lower(trim(alert_email)), '') "
            "WHERE alert_email IS NOT NULL",
            "UPDATE accounts SET alert_email_secondary = NULLIF(lower(trim(alert_email_secondary)), '') "
            "WHERE alert_email_secondary IS NOT NULL",
            "UPDATE accounts SET alert_phone = NULLIF(trim(alert_phone), '') "
            "WHERE alert_phone IS NOT NULL",
            "UPDATE accounts SET alert_phone_secondary = NULLIF(trim(alert_phone_secondary), '') "
            "WHERE alert_phone_secondary IS NOT NULL",
            # #22: a repeat suppressed by the cooldown is counted on the alert
            # it repeated - withheld from the channels, never from the record.
            "ALTER TABLE alerts ADD COLUMN repeat_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE alerts ADD COLUMN last_repeat_at TEXT",
            # #21: "checks performed" in the digest is a measured number, not
            # an estimate from the polling cadence - incremented on every
            # successful fast-loop check.
            "ALTER TABLE poll_state ADD COLUMN checks_total INTEGER NOT NULL DEFAULT 0",
            # #21: the member's proof-of-life digest choice. Text rather than
            # a foreign table for three values; writers validate.
            "ALTER TABLE portal_members ADD COLUMN digest_frequency TEXT NOT NULL DEFAULT 'daily'",
        )
        for ddl in migrations:
            # Re-adding an existing column raises, and that IS the ordinary
            # no-op - every open runs every migration. But it raises the same
            # exception class as a migration that genuinely failed: a renamed
            # table, a locked database, a typo in the DDL. Suppressing the
            # class made those indistinguishable, so a database that had not
            # actually migrated started the service anyway and every query
            # written against the new shape failed somewhere far from the
            # cause (#58, group D).
            #
            # Match on the message rather than the class. Narrow on purpose: an
            # error this code does not recognise is a reason to stop, not to
            # guess.
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError as exc:
                if not _migration_already_applied(exc):
                    raise
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

    def release_thread_connection(self) -> None:
        """Close and forget the calling thread's connection.

        For thread-per-request servers (the dashboard and the portal): a
        ThreadingHTTPServer thread dies after its connection, but `_all_conns`
        holds a strong reference to the thread's SQLite connection, so nothing
        was ever freed and every request cost the process a file descriptor
        until it could not open ANY socket - the exchange API's and SMTP's
        included (#79). The engine's worker pool is exempt from the problem
        because its threads are fixed and reuse their connections; it must
        never call this per task, which would turn reuse into churn.

        Safe to call from a thread that never touched the pool, and the next
        use from a released thread transparently opens a fresh connection."""
        if self._shared:
            return  # :memory: - one shared connection, closing it kills the DB
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        self._local.conn = None
        with self._conns_lock, contextlib.suppress(ValueError):
            self._all_conns.remove(conn)
        with contextlib.suppress(sqlite3.Error):
            conn.close()

    def close(self) -> None:
        """Closes every connection this database handed out, not just the
        caller's. A thread-local pool would otherwise leak file handles for
        each worker thread that ever touched it."""
        with self._conns_lock:
            conns, self._all_conns = self._all_conns, []
        for conn in conns:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
