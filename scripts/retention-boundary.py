#!/usr/bin/env python3
"""Read-only retention boundary report.

For every table `prune_all` governs, print the configured window, the oldest
row's date, and how many days until that row crosses its retention boundary.
The table list is imported from `sentinel.retention` (PRUNABLE and
KEEP_LATEST_PER_ACCOUNT) and the windows come from `RulesConfig` — the same
constants and the same fallback chain the pruner uses — so a table or setting
added later appears here automatically and the report cannot disagree with
the deletion job about what the windows are.

Read-only by construction: the database is opened with SQLite's `mode=ro`
URI, so this process cannot write, delete, or create anything — the engine
enforces it, not this script's good intentions. It deliberately does not use
`sentinel.db.Database`, which applies the schema (a write) on every open.

For the KEEP_LATEST_PER_ACCOUNT tables the newest snapshot per account is
the comparison baseline and is kept regardless of age, so the boundary
arithmetic there uses the oldest row the pruner would actually consider —
the same NOT IN predicate `prune_all` deletes with — and says so in the
notes when the overall-oldest row is an exempt baseline.

Run from the repository root (paths default to the repo layout):

    .venv/bin/python scripts/retention-boundary.py
    .venv/bin/python scripts/retention-boundary.py --db /data/sentinel.db

Exit codes: 0 — every answered table is inside its window; 1 — at least one
row is past its boundary; 2 — no boundary crossed but at least one table
could not be answered (missing table, unreadable database). An unanswered
question is never reported as a pass.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent.parent))

from sentinel.config import RulesConfig
from sentinel.retention import ALERT_CHILDREN, KEEP_LATEST_PER_ACCOUNT, PRUNABLE


def _open_readonly(path: str) -> sqlite3.Connection:
    # mode=ro (not immutable=1): the VM's database is live, and immutable
    # would let SQLite serve stale pages if a write landed mid-read.
    uri = f"file:{quote(str(Path(path)), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _span(days: float) -> str:
    """'5h' under a day, '3d' from there up. 'PAST 0d' read as a day-scale
    breach rounded down — the opposite of the minutes it actually was (#78)."""
    if days < 1:
        return f"{days * 24:.0f}h"
    return f"{days:.0f}d"


def _parse(ts: str) -> datetime:
    parsed = datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _oldest(conn: sqlite3.Connection, table: str, column: str,
            exempt_latest: bool) -> str | None:
    """MIN(column), over the rows the pruner would consider."""
    sql = f"SELECT MIN({column}) m FROM {table}"
    if exempt_latest:
        # Mirror prune_all's predicate exactly: the newest snapshot per
        # account survives whatever its age, so it is not approaching any
        # boundary and must not be counted as if it were.
        sql += (
            f" WHERE (account_id, {column}) NOT IN "
            f"(SELECT account_id, MAX({column}) FROM {table} GROUP BY account_id)"
        )
    return conn.execute(sql).fetchone()["m"]


_WAL_HELP = """\
The production database runs in WAL mode (sentinel/db.py sets it), so a
read-only connection still needs to read the -wal and -shm companion files.
If this script fails with "unable to open database file" against a database
that plainly exists, that is the failure — not a script bug. Run as a user
who can read all three files (on the VM: sudo, or the service's own user).
Do not work around it with immutable=1 on a live database: SQLite would
ignore concurrent writes and could serve stale or torn reads.
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=_WAL_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("SENTINEL_DB_PATH", "./sentinel_data/sentinel.db"),
        help="database path (default: $SENTINEL_DB_PATH or ./sentinel_data/sentinel.db)",
    )
    parser.add_argument(
        "--rules",
        default=os.environ.get("SENTINEL_RULES_PATH", "./config/rules.yaml"),
        help="rules.yaml path (default: $SENTINEL_RULES_PATH or ./config/rules.yaml)",
    )
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"error: database not found: {args.db}", file=sys.stderr)
        print("(mode=ro cannot create one, which is the point)", file=sys.stderr)
        return 2

    if not Path(args.rules).exists():
        # RulesConfig falls back to rules_schema.DEFAULTS — the same values
        # prune_all would run on against the same missing file — but the
        # operator should know the numbers below came from code, not config.
        print(f"warning: rules file not found: {args.rules} — "
              "windows below are the built-in defaults", file=sys.stderr)
        import logging
        logging.getLogger("sentinel.config").disabled = True

    rules = RulesConfig(args.rules)
    try:
        conn = _open_readonly(args.db)
        # Force a real read now: with WAL, connect() can succeed and the
        # first query still fail on an unreadable -shm/-wal companion file.
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        print(f"error: cannot open {args.db} read-only: {exc}", file=sys.stderr)
        if "unable to open database file" in str(exc):
            print(file=sys.stderr)
            print(_WAL_HELP, file=sys.stderr, end="")
        return 2

    now = datetime.now(timezone.utc)
    # The pruner's own cadence is the report's tolerance: between hourly
    # passes the oldest surviving row legitimately drifts up to one interval
    # past its window (#78).
    tolerance_days = max(rules.get_int("retention", "prune_interval_minutes"), 0) / 1440
    crossed = 0
    unanswered = 0
    rows: list[tuple[str, str, str, str, str]] = []

    everything = [(t, c, s, False) for t, (c, s) in PRUNABLE.items()] + [
        (t, c, s, True) for t, (c, s) in KEEP_LATEST_PER_ACCOUNT.items()
    ]
    for table, column, setting, exempt_latest in everything:
        days = rules.get_int("retention", setting)
        window = f"{days}d" if days > 0 else "forever"
        note = "oldest excludes each account's newest (kept as baseline)" if exempt_latest else ""

        try:
            oldest = _oldest(conn, table, column, exempt_latest)
        except sqlite3.OperationalError as exc:
            unanswered += 1
            rows.append((table, window, "?", "?", f"UNANSWERED: {exc}"))
            continue

        if oldest is None:
            what = "no prunable rows" if exempt_latest else "empty"
            rows.append((table, window, "-", "-", f"{what}" + (f"; {note}" if note else "")))
            continue

        oldest_dt = _parse(oldest)
        if days <= 0:
            rows.append((table, window, oldest_dt.date().isoformat(), "-",
                         "0 means keep forever" + (f"; {note}" if note else "")))
            continue

        remaining = (oldest_dt + timedelta(days=days) - now).total_seconds() / 86400
        if remaining >= 0:
            boundary = f"in {_span(remaining)}"
        elif -remaining <= tolerance_days:
            # Rows age across their window continuously between prune runs,
            # so overshoot up to one prune interval is steady state — the
            # next pass deletes this row. Flagging it as PAST made the
            # report cry wolf on its first production run (#78).
            boundary = f"due ({_span(-remaining)} past; within one prune interval)"
        else:
            crossed += 1
            boundary = f"PAST {_span(-remaining)}"
        rows.append((table, window, oldest_dt.date().isoformat(), boundary, note))

    conn.close()

    widths = [max(len(r[i]) for r in rows + [("TABLE", "WINDOW", "OLDEST", "BOUNDARY", "NOTES")])
              for i in range(5)]
    header = ("TABLE", "WINDOW", "OLDEST", "BOUNDARY", "NOTES")
    print("  ".join(h.ljust(w) for h, w in zip(header, widths, strict=True)).rstrip())
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths, strict=True)).rstrip())

    print()
    print(f"{', '.join(ALERT_CHILDREN)}: also deleted whenever their parent "
          "alert crosses alerts_days (the cascade in prune_all), so their "
          "effective window is min(shown, alerts_days).")

    if crossed:
        print(f"\n{crossed} table(s) have rows PAST their boundary — "
              "retention is not keeping up; see #73 for why that can be silent.")
        return 1
    if unanswered:
        print(f"\n{unanswered} table(s) could not be answered — "
              "not a pass; run against the right database.")
        return 2
    print("\nAll answered tables are inside their windows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
