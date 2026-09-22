"""A fleet-wide coverage failure is ONE event, not one per account (2026-09-22).

On 2026-09-21 at 19:10 UTC the instance's public address changed and nine
accounts' keys - allowlisted to the old address on two exchanges - were
refused in the same minute. The product treated that as nine facts: nine
customers emailed every hour, nine operator pages an hour (144 in sixteen
hours), 145 coverage repeats. The cause was one thing, and one page would
have said so.

Simultaneity is the signal. Nothing here reads error text or infers a shared
cause: N or more accounts entering coverage failure inside a short window
IS the event, and the operator reads the page and decides what it was.

Two effects while such an incident is open:
  - Minas receives ONE page naming every account and the count, keyed on
    the incident's start so it is sent once (a marker in portal_meta, the
    liveness gate's page-once pattern), and the per-account
    account_unwatchable_too_long alarm stays quiet for its members.
  - Each customer is told ONCE - their first coverage alert goes out exactly
    as before - and the daily coverage repeats are suppressed for members of
    the incident until it ends (the account polls successfully again, which
    resets its counters and drops it from the set).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sentinel.alerts.meaning import COVERAGE_RULES
from sentinel.db import Database

logger = logging.getLogger("sentinel.alerts.fleet")

FLEET_RULE = "coverage_failure_fleet"
_MARKS = ", ".join("?" for _ in COVERAGE_RULES)
_MARKER = "fleet_coverage:paged"


def _settings(rules) -> tuple[int, float]:
    try:
        min_accounts = int(rules.get_int("operator_alerting", "fleet_coverage_min_accounts", default=3))
    except Exception:  # noqa: BLE001 - a bad setting must not hide the fleet
        min_accounts = 3
    try:
        window = float(rules.get_seconds("operator_alerting", "fleet_coverage_window_seconds", default=600))
    except Exception:  # noqa: BLE001
        window = 600.0
    return max(2, min_accounts), window


def _unwatched_threshold(rules) -> int:
    try:
        return int(rules.get_int("detectors", "heartbeat", "consecutive_failures_threshold", default=5))
    except Exception:  # noqa: BLE001
        return 5


def coverage_starts(db: Database, threshold: int) -> list[dict]:
    """Every ACTIVE account currently in coverage failure (either failure
    counter at or over the heartbeat threshold - an auth failure is a
    failure) with the moment its current failure was first told: the newest
    non-repeat coverage alert. An account with no such alert yet has not
    been told, so it is not counted - the first notice must go out."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT ac.id, ac.label, "
            "  (SELECT max(a.sent_at) FROM alerts a WHERE a.account_id = ac.id AND a.repeat_of IS NULL "
            f"     AND a.rule IN ({_MARKS})) AS started_at "
            "FROM accounts ac JOIN poll_state p ON p.account_id = ac.id "
            "WHERE ac.status = 'active' "
            "AND (COALESCE(p.consecutive_failures, 0) >= ? OR COALESCE(p.consecutive_auth_failures, 0) >= ?)",
            (*sorted(COVERAGE_RULES), threshold, threshold),
        )
        rows = [dict(r) for r in cur.fetchall() if r["started_at"]]
    return sorted(rows, key=lambda r: r["started_at"])


def fleet_incident(db: Database, rules, now: datetime | None = None) -> dict | None:
    """The largest cluster of coverage starts inside the window, if it has at
    least min_accounts members; else None."""
    min_accounts, window = _settings(rules)
    starts = coverage_starts(db, _unwatched_threshold(rules))
    if len(starts) < min_accounts:
        return None
    stamps = []
    for r in starts:
        try:
            stamps.append((datetime.fromisoformat(r["started_at"]), r))
        except ValueError:
            continue
    best: list[dict] = []
    for i, (t0, _) in enumerate(stamps):
        cluster = [r for t, r in stamps[i:] if (t - t0).total_seconds() <= window]
        if len(cluster) > len(best):
            best = cluster
    if len(best) < min_accounts:
        return None
    first = best[0]["started_at"]
    return {
        "key": first[:16],  # the incident's start minute - one page per incident
        "started_at": first,
        "account_ids": [int(r["id"]) for r in best],
        "labels": [r["label"] for r in best],
        "count": len(best),
        "window_seconds": window,
        "min_accounts": min_accounts,
    }


def in_fleet_incident(db: Database, rules, account_id: int) -> bool:
    incident = fleet_incident(db, rules)
    return bool(incident) and int(account_id) in incident["account_ids"]


def _paged(db: Database) -> set[str]:
    with db.cursor() as cur:
        cur.execute("SELECT value FROM portal_meta WHERE key = ?", (_MARKER,))
        row = cur.fetchone()
    try:
        return set(json.loads(row["value"])) if row else set()
    except (TypeError, ValueError):
        return set()


def page_fleet_incident(db: Database, alerter, incident: dict) -> int | None:
    """One page per incident, ever: the key is the start minute, recorded in
    portal_meta once the page has gone out. A later evaluation with the same
    start pages nothing, whatever the pager's cooldown says."""
    if alerter is None or incident["key"] in _paged(db):
        return None
    labels = ", ".join(incident["labels"])
    page_id = alerter.page(
        rule=FLEET_RULE, severity="HIGH",
        message=(f"{incident['count']} accounts entered coverage failure within "
                 f"{int(incident['window_seconds'] // 60)} minutes of each other, from "
                 f"{incident['started_at'][:16].replace('T', ' ')} UTC: {labels}. One event, not "
                 f"{incident['count']}: something they share changed (an address, an exchange, a route). "
                 "Each customer was told once; their repeats and the per-account alarms are held "
                 "until their checks succeed again."),
        evidence={"count": incident["count"], "accounts": labels, "started_at": incident["started_at"],
                  "window_seconds": int(incident["window_seconds"]), "min_accounts": incident["min_accounts"]},
        dedup_scope=f"fleet:{incident['key']}",
    )
    paged = _paged(db) | {incident["key"]}
    with db.cursor() as cur:
        cur.execute("INSERT INTO portal_meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (_MARKER, json.dumps(sorted(paged))))
    logger.warning("fleet coverage incident %s: %d accounts (%s) - paged once as #%s",
                   incident["key"], incident["count"], labels, page_id)
    return page_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
