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

    def __init__(self, path: str | Path):
        self._path = Path(path)
        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # One connection shared across the worker pool: serialize access.
        self._write_lock = threading.Lock()
        self._init_schema()

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
        with self._write_lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def close(self) -> None:
        self._conn.close()
