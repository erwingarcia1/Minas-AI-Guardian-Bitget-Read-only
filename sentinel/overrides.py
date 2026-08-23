"""Per-account threshold overrides (#17, G-13): an active trader and a
long-term holder each get appropriate alerting.

One table holds the overrides; `AccountRules` overlays them on the global
config with the same read API, so a detector call site cannot tell which
layer answered - and an account with no override row behaves exactly as
before, byte for byte. Overridable settings are the SCALAR leaves the
schema already declares (a threshold is a number; routing tables and
channel lists are not per-customer sensitivity), validated against the
schema's own defaults so a typo'd path fails with a named error instead of
silently never matching.

Writes are attributed (the `--as` gate, via set-threshold) and every
actual change lands on the account's activity page as "Alert sensitivity
changed" - where the alerts about an account fire is a setting whose
silent change is an attack, and so is how easily they fire.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from sentinel.db import Database
from sentinel.rules_schema import default_for

logger = logging.getLogger("sentinel.overrides")


def validate_setting(setting: str) -> None:
    """The dotted path must name a scalar leaf the schema already declares.
    Raises ValueError otherwise - never a silent never-matching row."""
    segments = [s for s in setting.split(".") if s]
    if len(segments) < 2:
        raise ValueError(f"setting must be dotted, e.g. detectors.withdrawal_burst.min_count "
                         f"(got {setting!r})")
    default = default_for(*segments)
    if default is None:
        raise ValueError(f"unknown setting {setting!r} - it has no place in the schema")
    if isinstance(default, (list, dict)):
        raise ValueError(f"{setting!r} is a {type(default).__name__}, not a threshold - "
                         f"only scalar settings can be overridden per account")


def overrides_for(db: Database, account_id: int) -> dict[str, str]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT setting, value FROM account_threshold_overrides WHERE account_id = ?",
            (account_id,))
        return {r["setting"]: r["value"] for r in cur.fetchall()}


def _account_id(db: Database, label: str) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT id FROM accounts WHERE label = ?", (label,))
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"no account labelled {label!r}")
    return int(row["id"])


def set_override(db: Database, label: str, setting: str, value: str,
                 actor: str | None) -> str | None:
    """Set (or replace) one override. Returns the previous value (None when
    new). The event row is written only on an actual change - re-running
    the same value must not manufacture a forensic entry."""
    validate_setting(setting)
    account_id = _account_id(db, label)
    now = datetime.now(timezone.utc).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "SELECT value FROM account_threshold_overrides "
            "WHERE account_id = ? AND setting = ?", (account_id, setting))
        row = cur.fetchone()
        previous = row["value"] if row else None
        if previous == value:
            return previous
        cur.execute(
            "INSERT INTO account_threshold_overrides (account_id, setting, value, set_by, set_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(account_id, setting) DO UPDATE SET value = excluded.value, "
            "set_by = excluded.set_by, set_at = excluded.set_at",
            (account_id, setting, value, actor, now))
    from sentinel.events import append_event

    append_event(db, account_id, "threshold_override_changed", "LOW",
                 {"setting": setting, "from": previous, "to": value}, actor=actor)
    return previous


def clear_override(db: Database, label: str, setting: str, actor: str | None) -> bool:
    """Remove one override, back to the defaults. Returns whether one
    existed; nothing is recorded for clearing what was never set."""
    validate_setting(setting)
    account_id = _account_id(db, label)
    with db.cursor() as cur:
        cur.execute(
            "SELECT value FROM account_threshold_overrides "
            "WHERE account_id = ? AND setting = ?", (account_id, setting))
        row = cur.fetchone()
        if row is None:
            return False
        cur.execute(
            "DELETE FROM account_threshold_overrides WHERE account_id = ? AND setting = ?",
            (account_id, setting))
    from sentinel.events import append_event

    append_event(db, account_id, "threshold_override_changed", "LOW",
                 {"setting": setting, "from": row["value"], "to": None}, actor=actor)
    return True


class AccountRules:
    """The global rules with one account's overrides on top - the same read
    API the call sites already use, so which layer answered is invisible.
    An unparseable stored value falls back to the global layer with a log
    line: a corrupted override must weaken sensitivity to the DEFAULT, not
    to nothing."""

    def __init__(self, rules, db: Database, account_id: int) -> None:
        self._rules = rules
        self._db = db
        self._account_id = account_id

    def _override(self, keys: tuple) -> str | None:
        setting = ".".join(str(k) for k in keys)
        with self._db.cursor() as cur:
            cur.execute(
                "SELECT value FROM account_threshold_overrides "
                "WHERE account_id = ? AND setting = ?", (self._account_id, setting))
            row = cur.fetchone()
        return row["value"] if row else None

    def _coerced(self, keys, cast):
        raw = self._override(keys)
        if raw is None:
            return None
        try:
            return cast(raw)
        except (ValueError, ArithmeticError):
            logger.error("account %d: override %s=%r is unparseable - using the global value",
                         self._account_id, ".".join(map(str, keys)), raw)
            return None

    def get_int(self, *keys, **kw):
        value = self._coerced(keys, int)
        return value if value is not None else self._rules.get_int(*keys, **kw)

    def get_decimal(self, *keys, **kw):
        value = self._coerced(keys, Decimal)
        return value if value is not None else self._rules.get_decimal(*keys, **kw)

    def get_seconds(self, *keys, **kw):
        value = self._coerced(keys, float)
        return value if value is not None else self._rules.get_seconds(*keys, **kw)

    def get(self, *keys, **kw):
        raw = self._override(keys)
        return raw if raw is not None else self._rules.get(*keys, **kw)
