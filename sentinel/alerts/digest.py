from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sentinel.alerts.base import AlertChannel, AlertMessage, portal_action_line
from sentinel.db import Database

logger = logging.getLogger("sentinel.alerts.digest")

DIGEST_FREQUENCIES = ("daily", "weekly", "off")


def build_digest(db: Database, hours: int = 24) -> str | None:
    """Aggregates MEDIUM/LOW alerts from the window into one report.
    Returns None when there is nothing to say - no empty digests."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "SELECT a.severity, a.rule, a.message, a.sent_at, ac.label "
            "FROM alerts a JOIN accounts ac ON ac.id = a.account_id "
            "WHERE a.severity IN ('MEDIUM', 'LOW') AND a.sent_at >= ? "
            "ORDER BY ac.label, a.sent_at",
            (cutoff,),
        )
        rows = cur.fetchall()
    if not rows:
        return None

    lines = [f"Sentinel daily digest - {len(rows)} MEDIUM/LOW event(s) in the last {hours}h", ""]
    current_label = None
    for row in rows:
        if row["label"] != current_label:
            current_label = row["label"]
            lines.append(f"— {current_label} —")
        lines.append(f"  [{row['severity']}] {row['rule']} @ {row['sent_at'][:16]}: {row['message']}")
    return "\n".join(lines)


def send_digest(db: Database, channels: list[AlertChannel], hours: int = 24) -> bool:
    body = build_digest(db, hours)
    if body is None:
        return False
    alert = AlertMessage(
        account_label="all accounts",
        rule="daily_digest",
        severity="LOW",
        confidence="n/a",
        message=body,
        evidence={},
    )
    for channel in channels:
        channel.send(alert)
    return True


# --- the member proof-of-life digest (#21) -----------------------------------
#
# For a product whose success looks like silence, the digest is the main
# proof of life - so unlike the ops digest above, an all-quiet period is
# exactly when it must send: "no issues found, and that silence is real"
# is the message. Content follows the customer-copy rules: plain words,
# counts, no rule names, no evidence, no balances.

def _digest_mark(db: Database, email: str) -> dict:
    with db.cursor() as cur:
        cur.execute("SELECT value FROM portal_meta WHERE key = ?", (f"digest_mark:{email}",))
        row = cur.fetchone()
    return json.loads(row["value"]) if row else {}


def _set_digest_mark(db: Database, email: str, mark: dict) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO portal_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (f"digest_mark:{email}", json.dumps(mark)))


def digest_frequency(db: Database, email: str) -> str:
    """The member's choice, 'daily' for contacts who never set one. An
    unrecognized stored value reads as 'daily' rather than silently 'off' -
    a corrupted preference must not turn the proof of life off."""
    with db.cursor() as cur:
        cur.execute("SELECT digest_frequency FROM portal_members WHERE email = ?", (email,))
        row = cur.fetchone()
    value = row["digest_frequency"] if row else "daily"
    return value if value in DIGEST_FREQUENCIES else "daily"


def member_exists_for_digest(db: Database, email: str) -> bool:
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM portal_members WHERE email = ?", (email,))
        return cur.fetchone() is not None


def set_digest_frequency(db: Database, email: str, value: str) -> bool:
    """Store the member's choice. Returns True only on an actual change, so
    the caller's event row is a diff and never a re-run artifact."""
    if value not in DIGEST_FREQUENCIES:
        raise ValueError(f"unknown digest frequency '{value}'")
    with db.cursor() as cur:
        cur.execute(
            "UPDATE portal_members SET digest_frequency = ? "
            "WHERE email = ? AND digest_frequency != ?",
            (value, email, value))
        return cur.rowcount > 0


def build_member_digest(db: Database, email: str, rules=None,
                        now: datetime | None = None) -> tuple[str, dict]:
    """The body and the bookkeeping mark to store once it is SENT.

    Three plain statements, per the criterion: accounts watched (the same
    judgement as the portal's /status page - the digest and the page must
    not disagree), checks performed (the measured counter's delta since the
    last digest), and issues found (alert counts since the last digest, in
    counts and severity words only)."""
    from sentinel.portal import member_status

    status = member_status(db, email, rules)
    accounts = status["accounts"]
    now = now or datetime.now(timezone.utc)
    mark = _digest_mark(db, email)
    prev_checks: dict = mark.get("checks", {})
    since = mark.get("date")

    with db.cursor() as cur:
        ids = [a["account_id"] for a in accounts]
        marks = ",".join("?" for _ in ids) or "NULL"
        cur.execute(
            f"SELECT p.account_id, p.checks_total FROM poll_state p "
            f"WHERE p.account_id IN ({marks})", ids)
        totals = {str(r["account_id"]): int(r["checks_total"] or 0) for r in cur.fetchall()}
        if since:
            cur.execute(
                f"SELECT severity, COUNT(*) AS n FROM alerts "
                f"WHERE account_id IN ({marks}) AND sent_at >= ? GROUP BY severity",
                [*ids, since])
        else:
            cur.execute(
                f"SELECT severity, COUNT(*) AS n FROM alerts "
                f"WHERE account_id IN ({marks}) GROUP BY severity", ids)
        by_severity = {r["severity"]: int(r["n"]) for r in cur.fetchall()}

    checks_delta = sum(max(0, totals.get(k, 0) - int(prev_checks.get(k, 0)))
                       for k in totals)
    period = f"since your last digest ({since[:10]})" if since else "since monitoring began"

    watched = sum(1 for a in accounts if a["watched"])
    lines = [f"Your Minas AI Guardian digest — {now.strftime('%d %B %Y')}", ""]
    lines.append(f"Accounts watched: {watched} of {len(accounts)}.")
    for a in accounts:
        if a["watched"]:
            lines.append(f"  - {a['label']}: watching")
        elif a["status"] == "disabled":
            # Motive-neutral on purpose: an account is disabled by an
            # operator, sometimes because monitoring broke - not necessarily
            # because the customer asked. We hold no record of who wanted
            # it, so the digest must not claim to.
            lines.append(f"  - {a['label']}: paused — not being checked; "
                         "ask us if you didn't expect this")
        else:
            lines.append(f"  - {a['label']}: NOT being watched — {a['reason']}")
    lines.append("")
    lines.append(f"Checks performed {period}: {checks_delta:,}.")
    lines.append("")

    urgent = by_severity.get("CRITICAL", 0) + by_severity.get("HIGH", 0)
    routine = by_severity.get("MEDIUM", 0) + by_severity.get("LOW", 0)
    if urgent == 0 and routine == 0:
        lines.append("Issues found: none — all quiet. That silence is real: "
                     "the checks above ran and found nothing to tell you.")
    else:
        parts = []
        if urgent:
            parts.append(f"{urgent} urgent alert(s) — you were told at the time")
        if routine:
            parts.append(f"{routine} routine notice(s)")
        lines.append("Issues found: " + "; ".join(parts) + ".")
    lines.append("")
    lines.append(portal_action_line())

    new_mark = {"date": now.isoformat(), "checks": totals}
    return "\n".join(lines), new_mark


def send_member_digests(db: Database, rules=None, now: datetime | None = None) -> int:
    """One proof-of-life digest per primary contact, per their choice.

    Secondary contacts get nothing here, on decision 1's rule: they hear
    only escalations and the closing line, never routine traffic. Weekly
    digests go out on Mondays (UTC); the stored mark makes a second run on
    the same day a no-op rather than a duplicate."""
    from sentinel.alerts.contact import ContactRouter
    from sentinel.portal import _PlainNotice

    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    with db.cursor() as cur:
        # Disabled accounts' contacts are INCLUDED: a customer whose
        # monitoring is dark is exactly who must keep hearing so - the
        # digest exists to make silence readable, and going quiet at the
        # moment monitoring stops is the opposite. The per-member 'off'
        # switch is the one way to silence it; a contact cleared at
        # offboarding has no email here and so no digest.
        cur.execute(
            "SELECT DISTINCT alert_email FROM accounts WHERE alert_email IS NOT NULL")
        emails = [r["alert_email"] for r in cur.fetchall()]

    sent = 0
    for email in emails:
        frequency = digest_frequency(db, email)
        if frequency == "off":
            continue
        if frequency == "weekly" and now.weekday() != 0:
            continue
        mark = _digest_mark(db, email)
        if (mark.get("date") or "")[:10] == today:
            continue  # already sent today - a re-run must not double-send
        try:
            body, new_mark = build_member_digest(db, email, rules, now=now)
            message = AlertMessage(
                account_label="your accounts",
                rule="proof_of_life_digest",
                severity="LOW",
                confidence="n/a",
                message=body,
                evidence={},
            )
            ContactRouter(db=db, rules=rules).send(
                _PlainNotice(message), "your accounts", [email], [])
            _set_digest_mark(db, email, new_mark)
            sent += 1
        except Exception as exc:  # noqa: BLE001 - one broken recipient must not cost the rest
            logger.error("member digest to %s failed: %s", email, exc)
    return sent
