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

A HOST-level auth failure is the same finding one step earlier (2026-09-23).
Nine accounts that share nothing but this host failing authentication within
the same window is not nine tampered keys; the only thing those keys share
is the egress address. So when at least the same number of accounts START
failing authentication inside the same window, the tamper accusation is
held for the members (the event is recorded; no customer is told their key
was tampered with) and Minas is paged once with the shared cause and, where
the exchange named it, the requesting address: Bitget's refusal is
`Invalid IP,Current request IP 57.181.51.14`, Bybit's names no address. The
per-account tamper rule stays for the isolated case. Nothing calls out to
learn the address - it is read from the refusal the product already holds.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from sentinel.alerts.meaning import COVERAGE_RULES
from sentinel.db import Database
from sentinel.watching import unwatched_predicate_sql

logger = logging.getLogger("sentinel.alerts.fleet")

FLEET_RULE = "coverage_failure_fleet"
HOST_AUTH_RULE = "host_auth_failure"
TAMPER_RULE = "monitoring_key_tamper_suspected"
_MARKS = ", ".join("?" for _ in COVERAGE_RULES)
_MARKER = "fleet_coverage:paged"

# The requesting address as an exchange names it in a refusal. Bitget:
# "Invalid IP,Current request IP 57.181.51.14" (code 40018). Bybit's
# "Unmatched IP, please check your API key's bound IP addresses" names none.
# Anchored on the words "request ip" so an address elsewhere in a body is not
# mistaken for ours; IPv4 or IPv6.
_REQUEST_IP = re.compile(r"request\s*ip\s*[:=]?\s*((?:\d{1,3}\.){3}\d{1,3}|[0-9a-f]{0,4}(?::[0-9a-f]{0,4}){2,7})",
                         re.IGNORECASE)


def observed_request_ip(text: str | None) -> str | None:
    """The egress address an exchange named in its refusal, or None. Read
    from the words the product already holds - never from an outbound call."""
    if not text:
        return None
    match = _REQUEST_IP.search(text)
    return match.group(1) if match else None


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
            f"AND {unwatched_predicate_sql('p')}",
            (*sorted(COVERAGE_RULES), threshold),
        )
        rows = [dict(r) for r in cur.fetchall() if r["started_at"]]
    return sorted(rows, key=lambda r: r["started_at"])


def fleet_incident(db: Database, rules, now: datetime | None = None) -> dict | None:
    """The largest cluster of coverage starts inside the window, if it has at
    least min_accounts members; else None."""
    min_accounts, window = _settings(rules)
    starts = coverage_starts(db, _unwatched_threshold(rules))
    best = _largest_cluster(starts, window, min_accounts)
    if best is None:
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


def _largest_cluster(starts: list[dict], window: float, min_accounts: int) -> list[dict] | None:
    """The largest run of `started_at` stamps (sorted) inside `window`
    seconds of its earliest member, if it has at least min_accounts; an
    unparseable stamp is skipped, not the end of the list."""
    if len(starts) < min_accounts:
        return None
    stamps = []
    for r in starts:
        try:
            stamps.append((datetime.fromisoformat(r["started_at"]), r))
        except (TypeError, ValueError):
            continue
    best: list[dict] = []
    for i, (t0, _) in enumerate(stamps):
        cluster = [r for t, r in stamps[i:] if (t - t0).total_seconds() <= window]
        if len(cluster) > len(best):
            best = cluster
    return best if len(best) >= min_accounts else None


def auth_failure_starts(db: Database) -> list[dict]:
    """Every ACTIVE account in a current run of authentication failures, with
    the moment that run started and the exchange's last words for it."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT ac.id, ac.label, ac.exchange, p.auth_failing_since AS started_at, p.last_auth_error "
            "FROM accounts ac JOIN poll_state p ON p.account_id = ac.id "
            "WHERE ac.status = 'active' AND p.auth_failing_since IS NOT NULL")
        rows = [dict(r) for r in cur.fetchall()]
    return sorted(rows, key=lambda r: r["started_at"])


def others_in_auth_failure(db: Database, account_id: int) -> list[str]:
    """Labels of every OTHER active account currently in a run of auth
    failures. The one-sweep hold (2026-09-23) reads this: a tamper
    accusation that would go out while another account is failing waits
    one more check, so a cluster that is forming can form first. A genuine
    tamper is not more urgent for arriving one sweep earlier; a false
    accusation costs a customer relationship."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT ac.label FROM accounts ac JOIN poll_state p ON p.account_id = ac.id "
            "WHERE ac.status = 'active' AND p.auth_failing_since IS NOT NULL AND ac.id != ? ORDER BY ac.label",
            (int(account_id),))
        return [r["label"] for r in cur.fetchall()]


def host_auth_incident(db: Database, rules, now: datetime | None = None) -> dict | None:
    """The largest cluster of accounts whose auth failures STARTED inside the
    window, if it has at least min_accounts members; else None. Same two
    settings as the coverage incident: it is the same phenomenon, seen at
    the second failed check instead of the fifth."""
    min_accounts, window = _settings(rules)
    best = _largest_cluster(auth_failure_starts(db), window, min_accounts)
    if best is None:
        return None
    first = best[0]["started_at"]
    ips = sorted({ip for ip in (observed_request_ip(r.get("last_auth_error")) for r in best) if ip})
    refusals = {}
    for r in best:
        if r.get("last_auth_error") and r["exchange"] not in refusals:
            refusals[r["exchange"]] = str(r["last_auth_error"])[:160]
    return {
        "key": f"host-auth:{first[:16]}",
        "started_at": first,
        "account_ids": [int(r["id"]) for r in best],
        "labels": [r["label"] for r in best],
        "exchanges": sorted({r["exchange"] for r in best}),
        "observed_request_ips": ips,
        "refusals": refusals,
        "count": len(best),
        "window_seconds": window,
        "min_accounts": min_accounts,
    }


def in_host_auth_incident(db: Database, rules, account_id: int) -> bool:
    incident = host_auth_incident(db, rules)
    return bool(incident) and int(account_id) in incident["account_ids"]


def _already_accused(db: Database, account_ids: list[int]) -> list[str]:
    """Members whose tamper accusation went out before the cluster formed
    (the first one or two can, when the failures span sweeps): named on the
    page, because the operator may want to tell those customers."""
    if not account_ids:
        return []
    marks = ", ".join("?" for _ in account_ids)
    with db.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT ac.label FROM alerts a JOIN accounts ac ON ac.id = a.account_id "
            f"WHERE a.rule = ? AND a.repeat_of IS NULL AND a.account_id IN ({marks}) "
            f"AND a.sent_at >= (SELECT min(p.auth_failing_since) FROM poll_state p WHERE p.account_id IN ({marks}))",
            (TAMPER_RULE, *account_ids, *account_ids))
        return sorted(r["label"] for r in cur.fetchall())


def page_host_auth_incident(db: Database, alerter, incident: dict) -> int | None:
    """One page per host-level auth incident, keyed on its start minute (the
    same marker as the coverage page). Names the shared cause and the
    address the exchange named, and says no customer was accused."""
    if alerter is None or incident["key"] in _paged(db):
        return None
    labels = ", ".join(incident["labels"])
    exchanges = ", ".join(incident["exchanges"])
    ips = incident["observed_request_ips"]
    if ips:
        address = (f"The exchange named the requesting address in its refusal: {', '.join(ips)}. "
                   "Check every affected key's IP allowlist against it")
    else:
        address = ("No exchange named the requesting address in its refusal (Bybit does not; Bitget "
                   "does), so check the host's egress address against every affected key's IP allowlist")
    accused = _already_accused(db, incident["account_ids"])
    told = (f"Tamper accusations already sent before the cluster formed: {', '.join(accused)}."
            if accused else "No customer has been told their key was tampered with.")
    page_id = alerter.page(
        rule=HOST_AUTH_RULE, severity="HIGH",
        message=(f"{incident['count']} accounts on {len(incident['exchanges'])} exchange(s) ({exchanges}) were refused "
                 f"authentication within {int(incident['window_seconds'] // 60)} minutes of each other, from "
                 f"{incident['started_at'][:16].replace('T', ' ')} UTC: {labels}. The only thing those keys share is "
                 f"this host, so this is one cause, not {incident['count']}. {address}. {told} Their coverage "
                 "notices go out as usual, and the per-account tamper alert is held for them until their checks "
                 "succeed again."),
        evidence={"count": incident["count"], "accounts": labels, "exchanges": exchanges,
                  "observed_request_ips": ", ".join(ips), "refusals": incident["refusals"],
                  "already_accused": ", ".join(accused), "started_at": incident["started_at"],
                  "window_seconds": int(incident["window_seconds"]), "min_accounts": incident["min_accounts"]},
        dedup_scope=f"host-auth:{incident['key']}",
    )
    _mark_paged(db, incident["key"])
    logger.warning("host-level auth failure %s: %d accounts on %s (%s), address %s - paged once as #%s",
                   incident["key"], incident["count"], exchanges, labels, ", ".join(ips) or "unnamed", page_id)
    return page_id


def _mark_paged(db: Database, key: str) -> None:
    paged = _paged(db) | {key}
    with db.cursor() as cur:
        cur.execute("INSERT INTO portal_meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (_MARKER, json.dumps(sorted(paged))))


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


def page_fleet_incident(db: Database, alerter, incident: dict, rules=None) -> int | None:
    """One page per incident, ever: the key is the start minute, recorded in
    portal_meta once the page has gone out. A later evaluation with the same
    start pages nothing, whatever the pager's cooldown says."""
    if alerter is None or incident["key"] in _paged(db):
        return None
    # The shared cause was already paged as a host-level auth failure for
    # these same accounts: a second page five checks later about the same
    # nine is the 2026-09-21 shape in miniature. Recorded as paged, not sent.
    host = host_auth_incident(db, rules) if rules is not None else None
    if host and host["key"] in _paged(db) and set(incident["account_ids"]) <= set(host["account_ids"]):
        _mark_paged(db, incident["key"])
        logger.info("fleet coverage incident %s subsumed by host auth incident %s", incident["key"], host["key"])
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
    _mark_paged(db, incident["key"])
    logger.warning("fleet coverage incident %s: %d accounts (%s) - paged once as #%s",
                   incident["key"], incident["count"], labels, page_id)
    return page_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
