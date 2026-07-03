from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sentinel.db import Database


class _DecimalEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def append_event(db: Database, account_id: int, event_type: str, severity: str, payload: dict) -> int:
    """Writes one row to the append-only event log. The application must
    never UPDATE or DELETE the events table - it is the forensic record that
    baselines and alert evidence are derived from."""
    now = datetime.now(timezone.utc).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO events (account_id, event_type, severity, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (account_id, event_type, severity, json.dumps(payload, cls=_DecimalEncoder), now),
        )
        return cur.lastrowid
