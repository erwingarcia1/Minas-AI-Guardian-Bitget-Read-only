"""The 24-hour cancellable pause (#98, decision 15).

An immediate self-serve pause is the button an attacker with a stolen
session presses before moving funds; every genuine reason for a pause
tolerates a day. So the member path is request -> countdown -> automatic
execution, with each property load-bearing:

  - THE TABLE IS THE COUNTDOWN. A pending pause is a `pause_requests` row
    with its target time; the executor runs from the scheduler tick and
    reads the table, so a deploy or a #81 restart mid-countdown changes
    nothing, and a deadline that passed while the process was down
    executes on the next startup - never skipped, never lost.
  - Cancellable by the member (portal) or by Minas (CLI, attributed).
  - Every notification is LINKLESS (decision 16) and says contact Minas -
    the recovery path that still works for an owner locked out of the
    portal because the attacker changed the password.
  - Every contact is emailed at request time and again near expiry.
  - Re-requesting restarts a full 24 hours; the clock never shortens.
  - The immediate path stays human: an operator's `disable-account`,
    attributed. The anonymous path is slow and loud; the fast path
    requires a human who knows the customer.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sentinel.db import Database

logger = logging.getLogger("sentinel.pause")

PAUSE_COUNTDOWN_HOURS = 24
# "Again near expiry": one reminder when less than this remains. Two hours
# is late enough to matter and early enough to act on by phone.
EXPIRY_NOTICE_HOURS = 2

CONTACT_MINAS = (
    "If you did not request this, contact Minas directly - by phone or by "
    "writing to the address you already have. We never include links in "
    "these messages, so anything that looks like this but carries a link "
    "is not from us."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def pending_pause(db: Database, account_id: int):
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM pause_requests WHERE account_id = ? AND status = 'pending' "
            "ORDER BY id DESC LIMIT 1", (account_id,))
        return cur.fetchone()


def pending_pauses_for(db: Database, account_ids: list[int]) -> dict[int, dict]:
    if not account_ids:
        return {}
    marks = ",".join("?" for _ in account_ids)
    with db.cursor() as cur:
        cur.execute(
            f"SELECT * FROM pause_requests WHERE account_id IN ({marks}) "
            f"AND status = 'pending'", account_ids)
        return {int(r["account_id"]): dict(r) for r in cur.fetchall()}


def request_pause(db: Database, account_id: int, member_email: str,
                  now: datetime | None = None) -> str:
    """Start (or restart) the countdown. Returns the execute_at timestamp.

    A re-request supersedes any pending one and restarts a FULL 24 hours -
    the clock never shortens, so repeated requests cannot creep the
    deadline closer."""
    now = now or _now()
    execute_at = (now + timedelta(hours=PAUSE_COUNTDOWN_HOURS)).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE pause_requests SET status = 'cancelled', cancelled_by = ?, cancelled_at = ? "
            "WHERE account_id = ? AND status = 'pending'",
            (f"superseded by a new request from {member_email}", now.isoformat(), account_id))
        cur.execute(
            "INSERT INTO pause_requests (account_id, requested_by, requested_at, execute_at) "
            "VALUES (?, ?, ?, ?)",
            (account_id, member_email, now.isoformat(), execute_at))
    from sentinel.events import append_event

    append_event(db, account_id, "pause_requested", "LOW",
                 {"requested_by": member_email, "execute_at": execute_at}, actor=None)
    _notify_contacts(
        db, account_id,
        subject_rule="pause_requested",
        text=(
            "A pause of monitoring for '{label}' was requested and will take effect "
            f"at {execute_at[:16]} UTC - about {PAUSE_COUNTDOWN_HOURS} hours from the "
            "request - unless cancelled first. While paused, nothing on this account "
            "is watched. It can be cancelled any time before then from the member "
            "portal, or by asking Minas. " + CONTACT_MINAS
        ),
    )
    return execute_at


def cancel_pause(db: Database, account_id: int, by: str,
                 now: datetime | None = None, *,
                 who: str = "the account's member") -> bool:
    """Cancel the pending countdown. `by` is the member email or the
    operator name - the row says who; `who` is how the closing notice names
    them ("the account's member" or "Minas"). Returns whether one was
    pending.

    The closing notice is decision 1's principle applied here: everyone who
    was alarmed by the request notice hears the resolution - a warning with
    no ending trains people to ignore the next warning."""
    now = now or _now()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE pause_requests SET status = 'cancelled', cancelled_by = ?, cancelled_at = ? "
            "WHERE account_id = ? AND status = 'pending'",
            (by, now.isoformat(), account_id))
        if cur.rowcount == 0:
            return False
    from sentinel.events import append_event

    append_event(db, account_id, "pause_cancelled", "LOW", {"cancelled_by": by}, actor=None)
    _notify_contacts(
        db, account_id,
        subject_rule="pause_cancelled",
        text=(
            "The pause you were told about for '{label}' was CANCELLED by "
            f"{who}. Nothing changes: monitoring continues exactly as before. "
            "If you did not expect this cancellation, or the original request, "
            + CONTACT_MINAS
        ),
    )
    return True


def _notify_contacts(db: Database, account_id: int, *, subject_rule: str, text: str) -> None:
    """Every contact, linkless, guarded - a dead mail server must not fail
    the request or the countdown."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT label, alert_email, alert_email_secondary FROM accounts WHERE id = ?",
            (account_id,))
        row = cur.fetchone()
    if row is None:
        return
    emails = [e for e in (row["alert_email"], row["alert_email_secondary"]) if e]
    if not emails:
        return
    from sentinel.alerts.base import AlertMessage
    from sentinel.alerts.contact import ContactRouter
    from sentinel.portal import _PlainNotice

    message = AlertMessage(
        account_label=row["label"], rule=subject_rule, severity="LOW",
        confidence="n/a", message=text.format(label=row["label"]), evidence={},
    )
    try:
        ContactRouter(db=db).send(_PlainNotice(message), row["label"], emails, [])
    except Exception as exc:  # noqa: BLE001 - the countdown stands regardless
        logger.error("pause notice for account %d failed: %s", account_id, exc)


class PauseMaintenance:
    """The scheduler-tick half: execute due pauses and send near-expiry
    reminders. Table-driven on purpose - `evaluate` reads pause_requests
    fresh every tick, so the first tick after any restart resumes every
    countdown exactly where the table says it is."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def evaluate(self, now: datetime | None = None) -> int:
        now = now or _now()
        executed = self._execute_due(now)
        self._send_expiry_notices(now)
        return executed

    def _execute_due(self, now: datetime) -> int:
        with self._db.cursor() as cur:
            cur.execute(
                "SELECT p.*, a.label FROM pause_requests p JOIN accounts a ON a.id = p.account_id "
                "WHERE p.status = 'pending' AND p.execute_at <= ?", (now.isoformat(),))
            due = cur.fetchall()
        executed = 0
        for row in due:
            try:
                self._execute_one(row, now)
                executed += 1
            except Exception as exc:  # noqa: BLE001 - one account's failure must not strand the rest
                logger.error("executing the pause for %s failed: %s", row["label"], exc)
        return executed

    def _execute_one(self, row, now: datetime) -> None:
        from sentinel.events import append_event
        from sentinel.onboarding import set_account_status

        # Disable first, mark the row executed second. A crash between the
        # two leaves a pending row over an already-disabled account, and the
        # next tick's retry is harmless: set_account_status is idempotent
        # (already-disabled returns False and writes no second event). The
        # other order would mark a pause executed that never happened.
        set_account_status(self._db, row["label"], "disabled",
                          actor=f"pause countdown (requested by {row['requested_by']})")
        with self._db.cursor() as cur:
            cur.execute(
                "UPDATE pause_requests SET status = 'executed', executed_at = ? WHERE id = ?",
                (now.isoformat(), row["id"]))
        append_event(self._db, int(row["account_id"]), "pause_executed", "LOW",
                     {"requested_by": row["requested_by"],
                      "requested_at": row["requested_at"]}, actor=None)
        _notify_contacts(
            self._db, int(row["account_id"]),
            subject_rule="pause_executed",
            text=(
                "The requested pause for '{label}' has taken effect: the account is "
                "no longer being watched. Monitoring resumes when Minas re-enables "
                "the account - ask us whenever you are ready. " + CONTACT_MINAS
            ),
        )

    def _send_expiry_notices(self, now: datetime) -> None:
        cutoff = (now + timedelta(hours=EXPIRY_NOTICE_HOURS)).isoformat()
        with self._db.cursor() as cur:
            cur.execute(
                "SELECT * FROM pause_requests WHERE status = 'pending' "
                "AND expiry_notified_at IS NULL AND execute_at <= ?", (cutoff,))
            near = cur.fetchall()
        for row in near:
            with self._db.cursor() as cur:
                cur.execute(
                    "UPDATE pause_requests SET expiry_notified_at = ? "
                    "WHERE id = ? AND expiry_notified_at IS NULL",
                    (now.isoformat(), row["id"]))
                if cur.rowcount == 0:
                    continue
            _notify_contacts(
                self._db, int(row["account_id"]),
                subject_rule="pause_expiring",
                text=(
                    "Reminder: the requested pause for '{label}' takes effect at "
                    f"{row['execute_at'][:16]} UTC - less than "
                    f"{EXPIRY_NOTICE_HOURS} hours from now - unless cancelled. "
                    "Cancel from the member portal, or ask Minas. " + CONTACT_MINAS
                ),
            )
