"""Per-member alert preferences: channels per severity, and quiet hours (#24).

Decision 4 (2026-08-27), as built:

- **Quiet hours are off by default, non-critical only, in the member's own
  timezone, 22:00-07:00 when enabled.** A CRITICAL is never held. Anything
  held is QUEUED - one row per (member, alert) - and released into the
  member's next proof-of-life digest, never dropped.
- **Fail closed on the timezone.** Quiet hours cannot be enabled without a
  timezone the runtime knows (IANA, via zoneinfo), and a stored timezone the
  runtime no longer knows reads as "quiet hours off" at delivery time. A
  window defaulted to UTC would silence alerts at the wrong hours, which is
  the one failure quiet hours must not have.
- **Email is the floor.** The channel grid lets a member turn any channel
  off for any severity except email for CRITICAL: that one cannot be
  unchecked, and the writer refuses it. Removing the email itself is an
  onboarding-blocking error at both entry points (cli, dashboard).
- **No preference means what happened before preferences existed:** every
  channel on, no quiet hours. A member who never opens the page sees no
  change.

Notices and escalations bypass all of this: a password reset, a changed
destination, or "nobody acknowledged the CRITICAL" must reach the person
regardless of what they set for detections.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sentinel.db import Database

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
CHANNELS = ("email", "telegram", "sms")
FLOOR = ("CRITICAL", "email")  # the one cell that cannot be turned off
DEFAULT_QUIET_START = "22:00"
DEFAULT_QUIET_END = "07:00"


def default_grid() -> dict[str, dict[str, bool]]:
    return {sev: {ch: True for ch in CHANNELS} for sev in SEVERITIES}


def known_timezone(name: str | None) -> bool:
    if not name or not name.strip():
        return False
    try:
        ZoneInfo(name.strip())
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def _parse_hhmm(value: str) -> tuple[int, int]:
    hh, mm = value.strip().split(":")
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(value)
    return h, m


def member_prefs(db: Database, email: str) -> dict:
    """The member's preferences, with the pre-#24 behaviour as the default
    for anything unset. A stored grid missing a cell reads as ON for that
    cell: a corrupted preference must not turn a channel off."""
    email = (email or "").strip().lower()
    with db.cursor() as cur:
        cur.execute(
            "SELECT timezone, quiet_hours_enabled, quiet_start, quiet_end, channel_prefs_json "
            "FROM portal_members WHERE email = ?", (email,))
        row = cur.fetchone()
    grid = default_grid()
    if row is not None and row["channel_prefs_json"]:
        try:
            stored = json.loads(row["channel_prefs_json"])
            for sev in SEVERITIES:
                for ch in CHANNELS:
                    if isinstance(stored.get(sev, {}).get(ch), bool):
                        grid[sev][ch] = stored[sev][ch]
        except (ValueError, AttributeError, TypeError):
            grid = default_grid()
    grid[FLOOR[0]][FLOOR[1]] = True
    return {
        "email": email,
        "timezone": (row["timezone"] if row is not None else None) or None,
        "quiet_hours_enabled": bool(row["quiet_hours_enabled"]) if row is not None else False,
        "quiet_start": (row["quiet_start"] if row is not None else None) or DEFAULT_QUIET_START,
        "quiet_end": (row["quiet_end"] if row is not None else None) or DEFAULT_QUIET_END,
        "grid": grid,
    }


def set_member_prefs(db: Database, email: str, *, timezone_name: str | None,
                     quiet_hours_enabled: bool, quiet_start: str, quiet_end: str,
                     grid: dict[str, dict[str, bool]]) -> bool:
    """Store the member's choices. Raises ValueError on anything that cannot
    be honoured; returns True only on an actual change."""
    email = (email or "").strip().lower()
    tz = (timezone_name or "").strip() or None
    if tz is not None and not known_timezone(tz):
        raise ValueError(f"unknown timezone {tz!r}")
    if quiet_hours_enabled and tz is None:
        raise ValueError("quiet hours need your timezone first - they cannot be enabled without one")
    for value in (quiet_start, quiet_end):
        try:
            _parse_hhmm(value)
        except (ValueError, AttributeError):
            raise ValueError(f"quiet hours need times like 22:00, not {value!r}") from None
    if quiet_start.strip() == quiet_end.strip():
        raise ValueError("quiet hours need a start and an end that differ")
    clean = default_grid()
    for sev in SEVERITIES:
        for ch in CHANNELS:
            clean[sev][ch] = bool(grid.get(sev, {}).get(ch, True))
    if not clean[FLOOR[0]][FLOOR[1]]:
        raise ValueError("email for CRITICAL alerts cannot be switched off - at least one channel stays on")
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM portal_members WHERE email = ?", (email,))
        if cur.fetchone() is None:
            raise ValueError("no portal login for this address")
        cur.execute(
            "UPDATE portal_members SET timezone = ?, quiet_hours_enabled = ?, quiet_start = ?, "
            "quiet_end = ?, channel_prefs_json = ? WHERE email = ? AND ("
            "coalesce(timezone, '') != coalesce(?, '') OR quiet_hours_enabled != ? OR quiet_start != ? "
            "OR quiet_end != ? OR coalesce(channel_prefs_json, '') != ?)",
            (tz, 1 if quiet_hours_enabled else 0, quiet_start.strip(), quiet_end.strip(),
             json.dumps(clean, sort_keys=True), email,
             tz, 1 if quiet_hours_enabled else 0, quiet_start.strip(), quiet_end.strip(),
             json.dumps(clean, sort_keys=True)))
        return cur.rowcount > 0


def in_quiet_hours(prefs: dict, now: datetime | None = None) -> bool:
    """True when the member's clock is inside their window. Off, or a
    timezone the runtime does not know, is False: delivery proceeds."""
    if not prefs.get("quiet_hours_enabled"):
        return False
    tz = prefs.get("timezone")
    if not known_timezone(tz):
        return False
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(tz))
    minutes = local.hour * 60 + local.minute
    sh, sm = _parse_hhmm(prefs.get("quiet_start") or DEFAULT_QUIET_START)
    eh, em = _parse_hhmm(prefs.get("quiet_end") or DEFAULT_QUIET_END)
    start, end = sh * 60 + sm, eh * 60 + em
    if start == end:
        return False  # no window; the writer refuses this shape, and a stored one holds nothing
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end  # overnight window


def channel_allowed(prefs: dict, severity: str, channel: str) -> bool:
    if (severity, channel) == FLOOR:
        return True
    return bool(prefs["grid"].get(severity, {}).get(channel, True))


def hold_alert(db: Database, email: str, alert_id: int) -> bool:
    """Queue an alert for the member's next digest. Idempotent per pair."""
    if alert_id is None:
        return False
    with db.cursor() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO held_alerts (email, alert_id, held_at) VALUES (?, ?, ?)",
            ((email or "").strip().lower(), int(alert_id), datetime.now(timezone.utc).isoformat()))
        return cur.rowcount > 0


def release_held(db: Database, email: str, now: datetime | None = None) -> list:
    """The member's unreleased held alerts, marked released. Returns the
    alert rows (label, severity, sent_at) in customer-safe fields."""
    email = (email or "").strip().lower()
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "SELECT h.id AS hold_id, a.id AS alert_id, a.severity, a.sent_at, ac.label "
            "FROM held_alerts h JOIN alerts a ON a.id = h.alert_id JOIN accounts ac ON ac.id = a.account_id "
            "WHERE h.email = ? AND h.released_at IS NULL ORDER BY a.sent_at", (email,))
        rows = [dict(r) for r in cur.fetchall()]
        if rows:
            marks = ",".join("?" for _ in rows)
            cur.execute(f"UPDATE held_alerts SET released_at = ? WHERE id IN ({marks})",
                        [stamp, *[r["hold_id"] for r in rows]])
    return rows
