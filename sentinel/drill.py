"""Fire drills (#37, decision 2): the product tests itself end to end.

A silent failure is invisible - an expired Telegram token, a dead SMS
account, a broken detector all look exactly like "nothing bad happened".
A drill injects a simulated theft and watches it travel the whole path:
detection -> the alerts row -> every channel -> the customer's own contacts,
and records when, whether it arrived, and how long detection-to-delivery
took. That row is the only concrete evidence a customer ever gets that
they are paying for something real.

**Scheduled drills run against the DEDICATED drill account only** - a
Minas-owned account flagged `is_drill` - never against a customer's. A
simulated theft written into a real customer's forensic log corrupts the
evidence that log exists to be, and a weekly `[DRILL]` text nobody asked
for trains people to skim the one channel that must never be skimmed.

**On-demand drills** run against one of the requesting member's own
accounts, because they asked: "test my alerts" from the portal, or
`sentinel drill --account` from the shell. Their preferences (quiet hours,
channel grid) are bypassed - they want the proof on their phone now.

**A failed drill pages Minas as an outage** (criterion 4). So does a
schedule with no drill account to run against: no drill is no evidence.

**Every surface says DRILL first.** The alert row carries rule='drill', the
message is labelled on the operator channels, the customer copy opens
with THIS IS A DRILL and carries no runbook, the escalator skips it, and
the drill acknowledges its own alert so no ladder ever starts.

The drill proves the machinery. It does not prove that a given customer's
address still accepts mail - so `delivery_recency` derives "last successful
delivery per channel" for a member from REAL traffic (contact_deliveries
for email and Telegram, sms_deliveries for SMS) and the portal surfaces
staleness. Real deliveries are the evidence for the customer; the drill is
the evidence for the pipeline.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sentinel.db import Database
from sentinel.detectors.base import Detection

logger = logging.getLogger("sentinel.drill")

DRILL_RULE = "drill"
STALE_DELIVERY_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- the dedicated account --------------------------------------------------------

def drill_account(db: Database):
    """The active dedicated drill account, or None."""
    with db.cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE is_drill = 1 AND status = 'active' ORDER BY id LIMIT 1")
        return cur.fetchone()


def mark_drill_account(db: Database, account_id: int, is_drill: bool = True) -> bool:
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET is_drill = ? WHERE id = ?", (1 if is_drill else 0, int(account_id)))
        return cur.rowcount == 1


# --- the delivery record -----------------------------------------------------------

def record_contact_delivery(db: Database, email: str, channel: str, alert_id: int | None) -> None:
    """Kept for callers that predate the general record (#103 item 2): an
    accepted email/telegram leg to a customer, written to `deliveries`;
    `contact_deliveries` is the view that reads it back."""
    from sentinel.alerts.delivery import record_delivery

    record_delivery(db, channel=channel, recipient=email, status="accepted", audience="customer",
                    kind="alert", alert_id=alert_id)


def delivery_recency(db: Database, email: str, now: datetime | None = None) -> dict:
    """{channel: {"last": iso|None, "stale": bool}} for email, telegram and
    sms - derived from what was actually delivered to this person, never
    from a drill. SMS is keyed on the phone paired with this address on any
    account row (the person, not the number)."""
    now = now or _now()
    email = (email or "").strip().lower()
    out = {}
    with db.cursor() as cur:
        for channel in ("email", "telegram"):
            cur.execute("SELECT max(delivered_at) AS last FROM contact_deliveries WHERE email = ? AND channel = ?",
                        (email, channel))
            out[channel] = cur.fetchone()["last"]
        cur.execute(
            "SELECT max(s.created_at) AS last FROM sms_deliveries s JOIN accounts a ON a.id = s.account_id "
            "WHERE s.status = 'sent' AND ((lower(a.alert_email) = ? AND s.phone = a.alert_phone) "
            "OR (lower(a.alert_email_secondary) = ? AND s.phone = a.alert_phone_secondary))",
            (email, email))
        out["sms"] = cur.fetchone()["last"]
    result = {}
    for channel, last in out.items():
        stale = True
        if last:
            try:
                stale = (now - datetime.fromisoformat(last)) > timedelta(days=STALE_DELIVERY_DAYS)
            except ValueError:
                stale = True
        result[channel] = {"last": last, "stale": stale}
    return result


# --- running one drill -----------------------------------------------------------------

def _contacts(row) -> tuple[list[str], list[str]]:
    keys = row.keys()

    def val(k):
        return row[k] if k in keys else None

    return [e for e in [val("alert_email")] if e], [p for p in [val("alert_phone")] if p]


def _record(db: Database, *, account_id, kind, requested_by, started_at, alert_id, delivered_at,
            delivered_to, result, detail) -> int:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO drills (account_id, kind, requested_by, started_at, alert_id, delivered_at, "
            "delivered_to, result, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, kind, requested_by, started_at.isoformat(), alert_id,
             delivered_at.isoformat() if delivered_at else None, int(delivered_to), result, detail))
        return cur.lastrowid


def run_drill(db: Database, channels: dict, contact_router, operator_alerter, rules=None, *,
              account_id: int | None = None, kind: str = "scheduled", requested_by: str | None = None,
              now: datetime | None = None) -> dict:
    """Inject the simulated theft, walk the whole path, record the result.
    Returns the drills row as a dict. Never raises: a drill that cannot run
    is a failed drill, recorded and paged, not an exception in a scheduler."""
    from sentinel.alerts.dispatch import dispatch
    from sentinel.operators import acknowledge_alert

    started = now or _now()
    if account_id is None:
        row = drill_account(db)
        if row is None:
            detail = "no drill account is configured - onboard one with `sentinel onboard ... --drill`"
            drill_id = _record(db, account_id=None, kind=kind, requested_by=requested_by, started_at=started,
                               alert_id=None, delivered_at=None, delivered_to=0, result="failed", detail=detail)
            logger.error("drill %s: %s", drill_id, detail)
            if operator_alerter is not None:
                operator_alerter.page(rule="drill_no_account", severity="HIGH", message=detail,
                                      evidence={"drill_id": drill_id})
            return {"id": drill_id, "result": "failed", "detail": detail, "delivered_to": 0,
                    "account_id": None, "alert_id": None, "latency_ms": None}
    else:
        with db.cursor() as cur:
            cur.execute("SELECT * FROM accounts WHERE id = ?", (int(account_id),))
            row = cur.fetchone()
        if row is None:
            raise ValueError("no such account")
    account_id = int(row["id"])
    label = str(row["label"])

    detection = Detection(
        rule=DRILL_RULE, severity="CRITICAL", confidence="drill",
        message=f"DRILL: a simulated withdrawal on {label} - a test of the alert path, not a real event",
        # The timestamp makes each drill its own dedup key: a drill must
        # never be suppressed as a repeat of the last one.
        evidence={"drill": True, "kind": kind, "started_at": started.isoformat()},
        is_drill=True, bypass_preferences=True,
    )
    cooldowns = {sev: 0 for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}
    alert_id = None
    delivered_to = 0
    detail = ""
    try:
        alert_id = dispatch(db, channels or {}, account_id, label, detection, cooldowns)
        if contact_router is not None and alert_id is not None:
            emails, phones = _contacts(row)
            contact_router.send(detection, label, emails, phones, account_id=account_id, alert_id=alert_id)
    except Exception as exc:  # noqa: BLE001 - a broken path is what a drill exists to find
        detail = f"the alert path raised: {exc}"
        logger.error("drill on %s failed: %s", label, exc)
    finished = _now() if now is None else now
    if alert_id is not None:
        with db.cursor() as cur:
            cur.execute("SELECT delivered_to FROM alerts WHERE id = ?", (alert_id,))
            delivered_to = int(cur.fetchone()["delivered_to"] or 0)
            cur.execute("SELECT count(*) AS n FROM contact_deliveries WHERE alert_id = ?", (alert_id,))
            delivered_to += int(cur.fetchone()["n"])
            cur.execute("SELECT count(*) AS n FROM sms_deliveries WHERE alert_id = ? AND status = 'sent'",
                        (alert_id,))
            delivered_to += int(cur.fetchone()["n"])
        # The drill acknowledges itself: no escalation ladder, no emergency
        # contact paged about a simulated theft.
        acknowledge_alert(db, alert_id, "drill", include_siblings=False)
    if alert_id is None and not detail:
        detail = "the alert was not stored - dispatch suppressed or failed it"
    result = "passed" if delivered_to > 0 else "failed"
    if result == "failed" and not detail:
        detail = "the alert was stored but reached nobody - no channel accepted it"
    drill_id = _record(db, account_id=account_id, kind=kind, requested_by=requested_by, started_at=started,
                       alert_id=alert_id, delivered_at=finished if delivered_to else None,
                       delivered_to=delivered_to, result=result, detail=detail or "delivered")
    latency_ms = int((finished - started).total_seconds() * 1000) if delivered_to else None
    if result == "failed":
        logger.error("drill %s on %s FAILED: %s", drill_id, label, detail)
        if operator_alerter is not None:
            # Criterion 4: a failed drill is an outage. Paged as one.
            operator_alerter.page(rule="drill_failed", severity="CRITICAL",
                                  message=f"Fire drill on {label} failed: {detail}",
                                  evidence={"drill_id": drill_id, "alert_id": alert_id, "kind": kind},
                                  account_id=account_id, dedup_scope=str(drill_id))
    else:
        logger.info("drill %s on %s passed: delivered to %d, %d ms", drill_id, label, delivered_to, latency_ms)
    return {"id": drill_id, "result": result, "detail": detail or "delivered", "delivered_to": delivered_to,
            "account_id": account_id, "alert_id": alert_id, "latency_ms": latency_ms}


# --- reading drills -----------------------------------------------------------------------

def last_drill(db: Database, kind: str | None = None, account_ids: list[int] | None = None):
    """Newest drill (as a dict with latency_ms), optionally by kind and/or
    restricted to some accounts."""
    clauses, params = [], []
    if kind:
        clauses.append("d.kind = ?")
        params.append(kind)
    if account_ids is not None:
        if not account_ids:
            return None
        clauses.append(f"d.account_id IN ({','.join('?' for _ in account_ids)})")
        params.extend(int(a) for a in account_ids)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with db.cursor() as cur:
        cur.execute(f"SELECT d.*, a.label FROM drills d LEFT JOIN accounts a ON a.id = d.account_id "
                    f"{where} ORDER BY d.started_at DESC, d.id DESC LIMIT 1", params)
        row = cur.fetchone()
    return _with_latency(row)


def recent_drills(db: Database, limit: int = 10) -> list[dict]:
    with db.cursor() as cur:
        cur.execute("SELECT d.*, a.label FROM drills d LEFT JOIN accounts a ON a.id = d.account_id "
                    "ORDER BY d.started_at DESC, d.id DESC LIMIT ?", (limit,))
        return [_with_latency(r) for r in cur.fetchall()]


def _with_latency(row):
    if row is None:
        return None
    d = dict(row)
    latency = None
    if d.get("delivered_at") and d.get("started_at"):
        try:
            latency = int((datetime.fromisoformat(d["delivered_at"]) - datetime.fromisoformat(d["started_at"]))
                          .total_seconds() * 1000)
        except ValueError:
            latency = None
    d["latency_ms"] = latency
    return d


# --- the schedule ------------------------------------------------------------------------------

class DrillSchedule:
    """The scheduler-tick half. Table-driven like the pause countdown: due
    when the newest SCHEDULED drill is older than the interval (or there
    has never been one), read fresh from `drills` every tick, so a restart
    resumes the cadence from the table rather than from memory."""

    def __init__(self, db: Database, rules, runner) -> None:
        self._db = db
        self._rules = rules
        self._runner = runner

    def enabled(self) -> bool:
        try:
            return bool(self._rules.get("drills", "enabled", default=True))
        except Exception:  # noqa: BLE001 - a broken config must not stop the tick
            return True

    def interval(self) -> timedelta:
        try:
            days = int(self._rules.get_int("drills", "interval_days", default=7))
        except Exception:  # noqa: BLE001
            days = 7
        return timedelta(days=max(1, days))

    def due(self, now: datetime | None = None) -> bool:
        if not self.enabled():
            return False
        now = now or _now()
        last = last_drill(self._db, kind="scheduled")
        if last is None:
            return True
        try:
            return now - datetime.fromisoformat(last["started_at"]) >= self.interval()
        except ValueError:
            return True

    def evaluate(self, now: datetime | None = None) -> dict | None:
        now = now or _now()
        if not self.due(now):
            return None
        try:
            return self._runner(now)
        except Exception as exc:  # noqa: BLE001 - the tick must survive a drill that raises
            logger.error("scheduled drill failed to run: %s", exc)
            return None
