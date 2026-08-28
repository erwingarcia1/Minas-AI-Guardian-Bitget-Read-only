"""A member's own name for a connection (#8, criterion 6).

Connection labels are operator slugs - `erwin-garcia-3`, `binance-jaypee`.
A member with four Binance accounts receiving an alert could not tell which
one it concerned. The member may now name each connection from the portal
("Binance - savings"), and that name is used everywhere a PERSON reads it:
alert emails and Telegram copies, the digest, the portal, the incident
page, the CSV export.

Two rules, both load-bearing:

- The operator slug stays the INTERNAL identity. Operators, the ops
  dashboard, the operator-format alert, the CLI and every log line keep
  the slug: it is unique, stable, and what `--account <label>` means.
- The member's name is DISPLAY ONLY. It is never a lookup key: nothing
  selects, joins, dedups or authorises on it, and it need not be unique.
  tests/test_member_names.py greps the package for a `member_name` in any
  WHERE clause so that stays true.
"""
from __future__ import annotations

import re
import unicodedata

from sentinel.db import Database

MAX_LENGTH = 40


def display_label(row) -> str:
    """What a person reads: the member's name for the connection when one
    is set, else the slug. Works on any row-like with a `label`; a row
    without the `member_name` column (a fixture, an old query) just shows
    the slug."""
    keys = row.keys() if hasattr(row, "keys") else ()
    name = row["member_name"] if "member_name" in keys else None
    name = (name or "").strip()
    return name or str(row["label"])


def clean_name(name: str | None) -> str | None:
    """A printable, single-line, bounded name; None to clear. Raises
    ValueError with a sentence the member can act on."""
    if name is None:
        return None
    text = unicodedata.normalize("NFC", str(name)).strip()
    if not text:
        return None
    if len(text) > MAX_LENGTH:
        raise ValueError(f"a connection name is at most {MAX_LENGTH} characters")
    if any(unicodedata.category(ch)[0] == "C" for ch in text):
        raise ValueError("a connection name is one line of plain text")
    if re.search(r"[<>]", text):
        raise ValueError("a connection name cannot contain < or >")
    return text


def set_member_name(db: Database, account_id: int, name: str | None, *, actor: str) -> bool:
    """Store the member's name for one connection (None clears it) and
    record the change as an event the member sees on /activity. Returns
    True when something changed. The caller has already checked that
    `actor` is the account's primary contact - this function trusts its
    caller the way every writer here does, and looks the account up BY
    ID, never by any name."""
    cleaned = clean_name(name)
    with db.cursor() as cur:
        cur.execute("SELECT id, label, member_name FROM accounts WHERE id = ?", (int(account_id),))
        row = cur.fetchone()
        if row is None:
            return False
        before = (row["member_name"] or "").strip() or None
        if before == cleaned:
            return False
        cur.execute("UPDATE accounts SET member_name = ? WHERE id = ?", (cleaned, int(account_id)))
    from sentinel.events import append_event

    append_event(db, int(account_id), "account_named", "INFO",
                 {"from": before, "to": cleaned, "slug": row["label"]}, actor=actor)
    return True
