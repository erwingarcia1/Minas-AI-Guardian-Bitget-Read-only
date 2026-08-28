"""The dead-man's switch, push side (#63, decision 7).

Nothing in-process can report the process dying, so the Guardian pushes a
heartbeat to an EXTERNAL monitor on a schedule and that monitor alerts
when the pushes stop. The vendor is the owner's choice; this module is
everything on our side of it, behind one setting:

    SENTINEL_HEARTBEAT_URL   the monitor's check-in URL (unset: nothing is pushed)

- Pushes every `uptime.heartbeat_interval_seconds` (default 300, so a
  15-minute detection window on the vendor's side is three missed beats).
- A wedged scheduler stops pushing - that is the point: the vendor sees
  silence whether the process is dead, hung, or crash-looping.
- The last successful push is persisted. On startup, if it is older than
  twice the interval, the process records a MONITORING GAP - "from the last
  beat to now" - pages Minas, and the member digest tells customers
  (decision 7: in the digest, not as an alert). The gap is lower-bounded:
  the process cannot know what happened while it was not running, only
  that it was not.
- The vendor being unreachable pages HIGH: an outage monitor that cannot
  be reached is an outage monitor that is off.

What this cannot do, said plainly: the alert from the vendor to the owner
does not route through the Guardian and is not exercised by this code -
#63's own criterion asks for a drill of that path, which needs the vendor.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sentinel.db import Database

logger = logging.getLogger("sentinel.heartbeat")

LAST_KEY = "heartbeat:last"
DEFAULT_INTERVAL = 300


def _now() -> datetime:
    return datetime.now(timezone.utc)


def last_beat(db: Database) -> datetime | None:
    with db.cursor() as cur:
        cur.execute("SELECT value FROM portal_meta WHERE key = ?", (LAST_KEY,))
        row = cur.fetchone()
    if row is None:
        return None
    try:
        return datetime.fromisoformat(row["value"])
    except ValueError:
        return None


def _record_beat(db: Database, when: datetime) -> None:
    with db.cursor() as cur:
        cur.execute("INSERT INTO portal_meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (LAST_KEY, when.isoformat()))


def record_gap(db: Database, started_at: datetime, ended_at: datetime, detected_by: str) -> int:
    with db.cursor() as cur:
        cur.execute("INSERT INTO monitoring_gaps (started_at, ended_at, seconds, detected_by) VALUES (?, ?, ?, ?)",
                    (started_at.isoformat(), ended_at.isoformat(),
                     int((ended_at - started_at).total_seconds()), detected_by))
        return cur.lastrowid


def gaps_since(db: Database, since: str | None) -> list[dict]:
    with db.cursor() as cur:
        if since:
            cur.execute("SELECT * FROM monitoring_gaps WHERE ended_at >= ? ORDER BY started_at", (since,))
        else:
            cur.execute("SELECT * FROM monitoring_gaps ORDER BY started_at")
        return [dict(r) for r in cur.fetchall()]


class Heartbeat:
    def __init__(self, db: Database, url: str | None, interval_seconds: int = DEFAULT_INTERVAL,
                 operator_alerter=None) -> None:
        self._db = db
        self._url = (url or "").strip() or None
        self._interval = timedelta(seconds=max(30, int(interval_seconds)))
        self._alerter = operator_alerter
        self._last_attempt: datetime | None = None

    @property
    def configured(self) -> bool:
        return self._url is not None

    def detect_gap_on_start(self, now: datetime | None = None) -> dict | None:
        """Called once at startup. A last beat older than twice the interval
        means the process was not running (or not beating) since then."""
        now = now or _now()
        last = last_beat(self._db)
        if last is None or now - last <= 2 * self._interval:
            return None
        gap_id = record_gap(self._db, last, now, "restart")
        minutes = int((now - last).total_seconds() // 60)
        logger.error("monitoring gap: no heartbeat from %s to %s (%d min) - recorded as gap %s",
                     last.isoformat(), now.isoformat(), minutes, gap_id)
        if self._alerter is not None:
            self._alerter.page(rule="monitoring_gap", severity="HIGH",
                               message=f"The Guardian was not running (or not beating) for {minutes} minutes, "
                                       f"from {last.isoformat()[:19]} to {now.isoformat()[:19]} UTC. Customers were "
                                       "not being watched in that window; the digest will tell them.",
                               evidence={"gap_id": gap_id, "started_at": last.isoformat(),
                                         "ended_at": now.isoformat()}, dedup_scope=str(gap_id))
        return {"id": gap_id, "started_at": last, "ended_at": now, "minutes": minutes}

    def evaluate(self, now: datetime | None = None) -> bool:
        """Push if due. True when a beat was sent and accepted."""
        if not self.configured:
            return False
        now = now or _now()
        if self._last_attempt is not None and now - self._last_attempt < self._interval:
            return False
        self._last_attempt = now
        from sentinel import net

        try:
            resp = net.post(self._url, timeout=10.0)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - the monitor being down must not stop the monitored
            logger.error("heartbeat push failed: %s", exc)
            if self._alerter is not None:
                self._alerter.page(rule="heartbeat_unreachable", severity="HIGH",
                                   message=f"The dead-man's switch could not be reached: {exc}. Until it can, "
                                           "nobody outside this process will notice it dying.",
                                   evidence={"error": str(exc)})
            return False
        _record_beat(self._db, now)
        return True
