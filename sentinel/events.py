from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sentinel.db import Database

logger = logging.getLogger("sentinel.events")


class _DecimalEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def append_event(
    db: Database, account_id: int, event_type: str, severity: str, payload: dict,
    actor: str | None = None,
) -> int:
    """Writes one row to the append-only event log. The application must
    never UPDATE or DELETE the events table - it is the forensic record that
    baselines and alert evidence are derived from.

    `actor` is the operator username where a person did this, and None where
    nothing did - a detector firing has nobody behind it (G-50). It stays
    nullable for exactly that reason: filling it in with a guess would make the
    log say a person acted when none did, which is worse than saying nothing."""
    now = datetime.now(timezone.utc).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO events (account_id, event_type, severity, payload_json, created_at, actor) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, event_type, severity, json.dumps(payload, cls=_DecimalEncoder), now, actor),
        )
        return cur.lastrowid


def record_config_changes(db: Database, changes, actor: str | None = None) -> int:
    """One row per changed threshold (G-12, #16). Returns the number written.

    `actor` is the operator who made the change where the change came through a
    path that carries an identity, and None where it did not. A file edited on
    a server carries no identity, and `None` is the truthful answer - see the
    note on `config_changes` in schema.sql for why it is not filled in."""
    now = datetime.now(timezone.utc).isoformat()
    rows = [(setting, before, after, actor, now) for setting, before, after in changes]
    if not rows:
        return 0
    with db.cursor() as cur:
        cur.executemany(
            "INSERT INTO config_changes (setting, old_value, new_value, actor, changed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
    for setting, before, after in changes:
        logger.info(
            "config change: %s %s -> %s (%s)",
            setting, before, after, actor or "unattributed - edited on disk",
        )
    return len(rows)
