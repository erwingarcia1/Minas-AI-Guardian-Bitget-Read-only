from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sentinel.alerts.base import AlertChannel, AlertMessage
from sentinel.alerts.meaning import COVERAGE_RULES, is_coverage_rule
from sentinel.db import Database
from sentinel.naming import display_label

logger = logging.getLogger("sentinel.escalation")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _EscalationDetection:
    """Adapts an escalation `AlertMessage` to the shape `ContactRouter.send`
    expects. The rule name keeps its ESCALATION prefix on purpose: someone
    reading this needs to know it is the second time, or it is
    indistinguishable from the message they already ignored."""

    # An escalation is "nobody acknowledged this": it must reach the person
    # whatever their #24 preferences say for ordinary detections.
    bypass_preferences = True
    # The customer's copy is marked (the body says it is the second sending)
    # and the rule is resolved by the customer template, so the subject and
    # headline are the original rule's.
    escalation = True

    def __init__(self, alert: AlertMessage, *, severity: str | None = None):
        self.rule = alert.rule
        self.display_label = alert.display_label
        # The operator re-send borrows CRITICAL so it takes the CRITICAL
        # channel set; the customer's copy keeps the alert's OWN severity,
        # so a HIGH coverage alert escalates as "Please check: Contact with
        # this account has been lost", not "Urgent: Unusual activity".
        self.severity = severity or alert.severity
        self.confidence = alert.confidence
        self.message = alert.message
        self.evidence = alert.evidence


_COVERAGE_MARKS = ", ".join("?" for _ in COVERAGE_RULES)


def _carried_cause(row) -> dict:
    """{CAUSE_KEY: cause} from the stored alert's evidence, or {}."""
    import json

    from sentinel.alerts.base import CAUSE_KEY, established_cause

    try:
        evidence = json.loads(row["evidence_json"] or "{}")
    except (KeyError, IndexError, TypeError, ValueError):
        return {}
    cause = established_cause(evidence)
    return {CAUSE_KEY: cause} if cause else {}


class Escalator:
    """Escalates alerts nobody acknowledged within the configured windows.

    CRITICAL (window: escalation_unacknowledged_minutes, default 15): re-fires
    the global channels, reaches the customer's own contacts, and pages an
    operator - as before - and this is now also the FIRST moment a secondary
    contact hears anything (decision 1, #27: a secondary is an EMERGENCY
    contact, not a copy-to).

    HIGH (window: escalation_unacknowledged_minutes_high, default 60): reaches
    the secondary contact ONLY - no global re-fire, no primary re-send, no
    operator page. That is the decision's literal scope: the only thing a HIGH
    escalation exists to do is bring in the second pair of eyes.

    LOW and MEDIUM never escalate and never reach a secondary at all.

    Acknowledge with: sentinel ack <alert_id>."""

    def __init__(self, channels: list[AlertChannel], unacknowledged_minutes: int,
                 operator_alerter=None, contact_router=None,
                 high_unacknowledged_minutes: int = 60,
                 stale_after_hours: float = 4,
                 customer_burst_max: int = 3,
                 customer_burst_window_minutes: int = 60):
        self._channels = channels
        self._window = timedelta(minutes=unacknowledged_minutes)
        self._high_window = timedelta(minutes=high_unacknowledged_minutes)
        # The stale bound (2026-09-08, after 2026-08-30): an alert older than
        # this that has NEVER escalated is a backlog, not a live incident.
        # On 2026-08-30 v1.11.1 made coverage alerts escalate; seven seconds
        # after the switchover the escalator found 41 five-week-old
        # unacknowledged alerts on one account and emailed the customer 42
        # times in eight minutes. The longest live window is 60 minutes;
        # four hours is four missed opportunities, which only a stopped
        # scheduler or a routing change produces - so the default is
        # conservative for the customer's inbox, not for the operator's:
        # the backlog is reported to Minas once, with the count, and never
        # emailed. (A snooze that expires on an alert older than this lands
        # in the same report; the customer chose that snooze.)
        self._stale_after = timedelta(hours=stale_after_hours)
        # The burst bound: whatever the cause, one customer receives at most
        # this many escalation emails per window, plus ONE collapsed message
        # naming how many alerts remain unacknowledged; the rest are
        # recorded as suppressed. Counted from alerts.escalated_at, so it
        # holds across restarts and does not depend on the delivery record.
        self._burst_max = max(1, int(customer_burst_max))
        self._burst_window = timedelta(minutes=customer_burst_window_minutes)
        # Optional: a deployment with no operator paging configured is worse
        # off, not broken. See `_page_if_nobody_heard`.
        self._operator_alerter = operator_alerter
        # The customer's OWN contacts, primary and secondary (G-21, #27).
        # Without this, escalation re-fired down the same global channels the
        # original alert used - the path that has already failed to get a
        # response - and the second pair of eyes never saw it. Escalation that
        # repeats the unanswered channel is not escalation.
        self._contact_router = contact_router
        self._db_for_record: Database | None = None

    def check(self, db: Database) -> int:
        self._db_for_record = db
        now = datetime.now(timezone.utc)
        cutoff_critical = (now - self._window).isoformat()
        cutoff_high = (now - self._high_window).isoformat()
        with db.cursor() as cur:
            cur.execute(
                "SELECT a.id, a.rule, a.severity, a.message, a.confidence, a.account_id, a.sent_at, a.evidence_json, "
                "ac.label, ac.member_name, ac.alert_email, ac.alert_phone, ac.alert_email_secondary, "
                "ac.alert_phone_secondary "
                "FROM alerts a JOIN accounts ac ON ac.id = a.account_id "
                "WHERE a.acknowledged_at IS NULL AND a.escalated_at IS NULL "
                # A coverage repeat (repeat_of set) already escalated as the
                # first alert of its incident; the emergency contact and Minas
                # are not told the same thing every day.
                "AND a.repeat_of IS NULL "
                # #37: a drill is acknowledged by the drill itself; if that
                # ever failed, escalating a simulated theft to an emergency
                # contact would be the drill causing the harm it tests for.
                "AND a.rule != 'drill' "
                # #103 item 3: a coverage alert (we are blind on this
                # account) escalates at the CRITICAL window whatever its
                # severity - the meaning decides, not the level.
                "AND ((a.severity = 'CRITICAL' AND a.sent_at < ?) "
                f"  OR (a.rule IN ({_COVERAGE_MARKS}) AND a.sent_at < ?) "
                "  OR (a.severity = 'HIGH' AND a.sent_at < ?)) "
                # A live snooze holds escalation too (G-26, #30). Escalating
                # past a snooze would page the customer anyway and make the
                # button a lie; suppressing escalation *permanently* would be
                # the permanent mute this product does not have. It holds, and
                # then it lets go - which is what snoozes expiring is for.
                "AND NOT EXISTS (SELECT 1 FROM snoozes s WHERE s.account_id = a.account_id "
                "AND s.dedup_key = a.dedup_key AND s.expires_at > ?)",
                (cutoff_critical, *sorted(COVERAGE_RULES), cutoff_critical, cutoff_high, now.isoformat()),
            )
            stale = cur.fetchall()

        # The stale bound, before anything reaches a customer: rows older
        # than the window are a backlog (see __init__), reported once.
        stale_cutoff = (now - self._stale_after).isoformat()
        backlog = [r for r in stale if (r["sent_at"] or "") < stale_cutoff]
        if backlog:
            self._report_backlog(db, backlog, now)
        live = [r for r in stale if (r["sent_at"] or "") >= stale_cutoff]

        escalated = 0
        for row in live:
            if row["severity"] == "CRITICAL" or is_coverage_rule(row["rule"]):
                self._escalate_critical(row)
            else:
                self._reach_the_secondary_contact(row, window=self._high_window)
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE alerts SET escalated_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), row["id"]),
                )
            escalated += 1
        return escalated

    def _report_backlog(self, db: Database, rows, now: datetime) -> None:
        """A backlog of never-escalated alerts older than the stale window:
        one operator page with the count, one suppressed delivery row per
        alert (so "was the customer told" stays answerable), escalated_at
        set so it is reported once and never again."""
        from sentinel.alerts.delivery import record_delivery

        ids = [int(r["id"]) for r in rows]
        by_account: dict[str, int] = {}
        for r in rows:
            by_account[r["label"]] = by_account.get(r["label"], 0) + 1
        hours = self._stale_after.total_seconds() / 3600
        accounts = ", ".join(f"{label} ({n})" for label, n in sorted(by_account.items()))
        page_id = None
        if self._operator_alerter is not None:
            page_id = self._operator_alerter.page(
                rule="escalation_backlog", severity="HIGH",
                message=(f"{len(rows)} unacknowledged alert(s) older than {hours:g} h were never escalated "
                         f"and will NOT be escalated to customers - a backlog, not a live incident "
                         f"(accounts: {accounts}). Review or acknowledge them in the dashboard."),
                evidence={"count": len(rows), "older_than_hours": hours, "accounts": accounts,
                          "oldest": min(r["sent_at"] for r in rows), "newest": max(r["sent_at"] for r in rows),
                          "alert_ids": ",".join(str(i) for i in ids)},
                dedup_scope=f"backlog:{min(ids)}:{max(ids)}",
            )
        for r in rows:
            for recipient in (r["alert_email"], r["alert_email_secondary"]):
                if recipient:
                    record_delivery(db, channel="email", recipient=recipient, status="suppressed",
                                    audience="customer" if recipient == r["alert_email"] else "secondary",
                                    kind="escalation", alert_id=r["id"], account_id=r["account_id"], rule=r["rule"],
                                    detail=f"stale backlog: never escalated, older than {hours:g} h; "
                                           f"reported to operators as page #{page_id}")
            with db.cursor() as cur:
                cur.execute("UPDATE alerts SET escalated_at = ? WHERE id = ?", (now.isoformat(), r["id"]))
        logger.warning("escalation backlog: %d alert(s) older than %gh reported to operators, not escalated",
                       len(rows), hours)

    def _live_escalations_in_window(self, db: Database, account_id: int, now: datetime,
                                    *, customer_path_only: bool) -> int:
        """How many alerts on this account escalated LIVE inside the burst
        window. A backlog row is marked escalated too (so it is reported
        once), but nobody was written to for it, so it must not count
        against anyone's inbox: a live escalation happens within the stale
        window of its alert, a backlog one by definition later.
        `customer_path_only` counts the rows that reached the primary
        contact (CRITICAL or coverage); the emergency contact hears HIGH
        escalations as well, so their bound counts every row."""
        since = (now - self._burst_window).isoformat()
        with db.cursor() as cur:
            cur.execute(
                "SELECT sent_at, escalated_at, severity, rule FROM alerts "
                "WHERE account_id = ? AND escalated_at >= ?",
                (account_id, since),
            )
            rows = cur.fetchall()
        n = 0
        for r in rows:
            if customer_path_only and not (r["severity"] == "CRITICAL" or is_coverage_rule(r["rule"])):
                continue
            try:
                age_at_escalation = datetime.fromisoformat(r["escalated_at"]) - datetime.fromisoformat(r["sent_at"])
            except (TypeError, ValueError):
                continue
            if age_at_escalation < self._stale_after:
                n += 1
        return n

    def _unacknowledged_on(self, db: Database, account_id: int) -> int:
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM alerts WHERE account_id = ? AND acknowledged_at IS NULL", (account_id,))
            return int(cur.fetchone()["n"])

    def _record_global_leg(self, channel, status, row, *, detail=None) -> None:
        """A delivery row per recipient of a global escalation leg (#103
        item 2), audience 'operator': the re-send reaches Minas' own
        channels; the customer's legs are recorded by the router."""
        from sentinel.alerts.delivery import channel_name_of, record_delivery

        name = channel_name_of(channel)
        for recipient in (list(getattr(channel, "recipients", None) or []) or [None]):
            record_delivery(self._db_for_record, channel=name, recipient=recipient, status=status,
                            audience="operator", kind="escalation", alert_id=row["id"],
                            account_id=row["account_id"], rule=row["rule"], detail=detail)

    def _escalate_critical(self, row) -> None:
        # A coverage alert escalates on this path whatever its level (#103
        # item 3); the words say what it is rather than borrowing "CRITICAL".
        what = ("CRITICAL alert" if row["severity"] == "CRITICAL"
                else f"{row['severity']} coverage alert (monitoring is blind on {row['label']})")
        alert = AlertMessage(
            account_label=row["label"],
            display_label=display_label(row),
            rule=f"ESCALATION-unacknowledged: {row['rule']}",
            severity="CRITICAL",
            confidence=row["confidence"],
            message=(
                f"{what} #{row['id']} has not been acknowledged. "
                f"Original: {row['message']} -- acknowledge with: sentinel ack {row['id']}"
            ),
            # The original's established cause (alerts/base.py CAUSE_KEY)
            # rides along, so the customer's escalation copy carries the
            # diagnosis's advice, not the rule's (2026-09-23).
            evidence={"original_alert_id": row["id"], **_carried_cause(row)},
        )
        delivered = 0
        for channel in self._channels:
            try:
                channel.send(alert)
                delivered += 1
                self._record_global_leg(channel, "accepted", row)
            except Exception as exc:  # noqa: BLE001 - escalation must try every channel
                logger.error("escalation channel failed: %s", exc)
                self._record_global_leg(channel, "failed", row, detail=str(exc))
        self._reach_the_customers_own_contacts(row, alert)
        self._reach_the_secondary_contact(row, window=self._window)
        self._page_escalated(row)
        self._page_if_nobody_heard(delivered, row)

    def _reach_the_secondary_contact(self, row, *, window: timedelta) -> None:
        """The emergency contact's ONLY entry point for a live alert
        (decision 1, #27), so its message must say why they are hearing at
        all - "why am I suddenly getting this one" is not an improvement on
        "why do I get all of these".

        Deliberately carries NONE of the original alert text: detection
        messages embed amounts and destination addresses, and the emergency
        contact's message must not (the same rule as every customer-facing
        surface: no balances, no amounts, no addresses, no one-tap link).

        No secondary contact on file is a deliberate silence, not a delivery
        failure - it returns without recording or paging anything, which is
        the #57 distinction applied in reverse."""
        if self._contact_router is None:
            return
        emails = [row["alert_email_secondary"]]
        phones = [row["alert_phone_secondary"]]
        if not any(emails) and not any(phones):
            return
        # The same burst bound as the primary's leg: an emergency contact is
        # a person too, and 41 notes in eight minutes is the 2026-08-30 shape.
        db = self._db_for_record
        if db is not None and self._live_escalations_in_window(
                db, row["account_id"], _now(), customer_path_only=False) >= self._burst_max:
            from sentinel.alerts.delivery import record_delivery

            for email in emails:
                if email:
                    record_delivery(db, channel="email", recipient=email, status="suppressed", audience="secondary",
                                    kind="escalation", alert_id=row["id"], account_id=row["account_id"], rule=row["rule"],
                                    detail=f"burst bound: {self._burst_max} escalations to this account's contacts in the window")
            return
        minutes = int(window.total_seconds() // 60)
        severity_word = "critical" if row["severity"] == "CRITICAL" else "high-severity"
        note = AlertMessage(
            account_label=row["label"],
            rule=f"ESCALATION-unacknowledged: {row['rule']}",
            severity=row["severity"],
            confidence=row["confidence"],
            message=(
                f"You are receiving this because the primary contact for "
                f"{display_label(row)} has not responded to a {severity_word} alert in "
                f"{minutes} minutes. You are listed as the emergency contact for "
                f"this account. Sign in to the Minas AI Guardian member portal to "
                f"see the incident and what to do next."
            ),
            evidence={"original_alert_id": row["id"]},
        )
        note.is_notice = True  # the message IS the content; render it verbatim
        note.audience = "secondary"  # the delivery record names who this reached (#103 item 2)
        try:
            self._contact_router.send(
                note, row["label"], emails, phones,
                account_id=row["account_id"], alert_id=row["id"],
            )
        except Exception as exc:  # noqa: BLE001 - one path must not cost the other
            logger.error("escalation to the secondary contact failed: %s", exc)

    def _reach_the_customers_own_contacts(self, row, alert) -> None:
        """Deliver the escalation to the account's own PRIMARY email and
        phone (#27; the secondary goes through _reach_the_secondary_contact).

        Guarded on its own, on the same rule as everything else on a delivery
        path (#57): the router reaching the customer's address and the global
        channels reaching whatever else is configured are two independent
        deliveries, and losing both because one raised is the failure this
        codebase keeps having."""
        if self._contact_router is None:
            return
        # Primary only: the secondary contact has its own path with its own
        # wording (_reach_the_secondary_contact) - and its own rules about
        # what the message may contain.
        emails = [row["alert_email"]]
        phones = [row["alert_phone"]]
        if not any(emails) and not any(phones):
            return
        # The burst bound (see __init__): prior escalations to this customer
        # inside the window decide whether this one goes out as itself, as
        # the ONE collapsed message, or not at all.
        db = self._db_for_record
        prior = (self._live_escalations_in_window(db, row["account_id"], _now(), customer_path_only=True)
                 if db is not None else 0)
        if prior > self._burst_max:
            self._record_suppressed(row, emails, prior)
            return
        detection = _EscalationDetection(alert, severity=row["severity"])
        if prior == self._burst_max:
            open_count = self._unacknowledged_on(db, row["account_id"])
            minutes = int(self._burst_window.total_seconds() // 60)
            collapsed = AlertMessage(
                account_label=row["label"], display_label=display_label(row),
                rule="escalation_burst_collapsed", severity=row["severity"], confidence=row["confidence"],
                message=(f"{open_count} alerts on {display_label(row)} remain unacknowledged, including this one. "
                         f"You have received {self._burst_max} escalation emails about this account in the "
                         f"last {minutes} minutes, so we are sending this one summary instead of more. "
                         "Sign in to Minas AI Guardian to see every open alert and acknowledge them."),
                evidence={"unacknowledged": open_count}, notice=True,
            )
            detection = _EscalationDetection(collapsed, severity=row["severity"])
            detection.is_notice = True
        try:
            self._contact_router.send(
                detection, row["label"], emails, phones,
                account_id=row["account_id"], alert_id=row["id"],
            )
        except Exception as exc:  # noqa: BLE001 - one path must not cost the other
            logger.error("escalation to the account's own contacts failed: %s", exc)

    def _record_suppressed(self, row, emails, prior: int) -> None:
        from sentinel.alerts.delivery import record_delivery

        minutes = int(self._burst_window.total_seconds() // 60)
        for email in emails:
            if email:
                record_delivery(self._db_for_record, channel="email", recipient=email, status="suppressed",
                                audience="customer", kind="escalation", alert_id=row["id"],
                                account_id=row["account_id"], rule=row["rule"],
                                detail=f"burst bound: {prior} escalations to this customer in the last "
                                       f"{minutes} minutes (max {self._burst_max} + one summary)")

    def _page_escalated(self, row) -> None:
        """Minas is told that an escalation happened at all (#27).

        Distinct from `escalation_delivered_to_nobody`, which is about our
        machinery failing. This one is about the *customer*: a CRITICAL has
        gone unanswered for the whole window, which is worth an operator's
        attention whether or not the escalation message left the building.
        Both fire when both are true."""
        if self._operator_alerter is None:
            return
        self._operator_alerter.page(
            rule="critical_alert_escalated",
            severity="HIGH",
            message=(
                f"CRITICAL alert #{row['id']} ({row['rule']}) on {row['label']} was not "
                f"acknowledged and has been escalated. The customer has not responded to a "
                f"CRITICAL for the full escalation window."
            ),
            evidence={"original_alert_id": row["id"], "rule": row["rule"],
                      "account_label": row["label"]},
            account_id=row["account_id"],
            dedup_scope=f"escalated:{row['id']}",
        )

    def _page_if_nobody_heard(self, delivered: int, row) -> None:
        """The strongest entry in the #58 sweep, and the only one with a real
        claim on CRITICAL.

        Escalation is what happens when a CRITICAL goes unacknowledged. There
        is nothing after it. If the escalation itself reaches nobody, the
        customer is not answering *and* nobody knows they are not answering -
        and the log line this used to be went into a file that, by definition,
        nobody was reading at the time.

        Only on zero. One channel failing while another delivers is degraded,
        not uninformed, and paging on that would train an operator to ignore
        the channel - which is the same outcome as not paging at all. An empty
        channel list counts as zero on purpose: every escalation going nowhere
        is worse than one that fails loudly."""
        if delivered > 0 or self._operator_alerter is None:
            return
        self._operator_alerter.page(
            rule="escalation_delivered_to_nobody",
            severity="CRITICAL",
            message=(
                f"Alert #{row['id']} ({row['rule']}) on {row['label']} went unacknowledged, "
                f"escalated, and the escalation reached no channel at all. Escalation is the "
                f"last resort - there is nothing after it. Check the channels configured for "
                f"escalation."
            ),
            evidence={"original_alert_id": row["id"], "rule": row["rule"],
                      "account_label": row["label"]},
            dedup_scope="escalation",
        )


def send_critical_ack_closing_note(db: Database, alert_id: int, *, rules=None) -> None:
    """The closing note (decision 1, #27): when a CRITICAL alert is
    acknowledged, the SECONDARY contact hears exactly one line - "a critical
    alert on <account> was acknowledged at <time>".

    Why acknowledgement must not simply silence the emergency contact:
    whoever compromises a primary's portal access could acknowledge, and the
    one person listed for emergencies would never hear anything happened.
    CRITICAL only; the primary does not receive it - they are the one who
    acknowledged. Together with the escalation this caps the secondary at two
    messages per incident. Usually one. Often none.

    Called by BOTH acknowledgement writers (operators.acknowledge_alert and
    portal.acknowledge_alert_as_member) - the pair-of-gates rule: hooking one
    path leaves the other silently mute. Exactly-once comes from the callers
    firing only on a successful FIRST acknowledgement (their UPDATEs are
    guarded by acknowledged_at IS NULL). Guarded top to bottom: a note that
    cannot send must never fail the acknowledgement that triggered it.

    An account with no secondary contact returns without recording anything:
    a deliberate silence is not a delivery failure (#57's distinction). The
    router is called with account_id=None so no sms_deliveries bookkeeping
    rows are written against the original alert for this side-channel note.

    Sent after the answer when a request is being answered
    (sentinel/outbox.py): a member acknowledging in the portal or the app
    gets their answer first - a slow mail or SMS provider must not hold an
    acknowledgement that has already stopped the escalation - and the notes
    for the alert and its siblings follow in the order they were owed.
    Anywhere else (`sentinel ack`, the dashboard) it is sent at once."""
    from sentinel.outbox import later

    later(_closing_note, db, alert_id, rules)


def _closing_note(db: Database, alert_id: int, rules) -> None:
    from sentinel.alerts.contact import ContactRouter

    try:
        with db.cursor() as cur:
            cur.execute(
                "SELECT a.severity, a.acknowledged_at, ac.label, "
                "ac.alert_email_secondary, ac.alert_phone_secondary "
                "FROM alerts a JOIN accounts ac ON ac.id = a.account_id "
                "WHERE a.id = ?",
                (alert_id,),
            )
            row = cur.fetchone()
        if row is None or row["severity"] != "CRITICAL":
            return
        emails = [row["alert_email_secondary"]]
        phones = [row["alert_phone_secondary"]]
        if not any(emails) and not any(phones):
            return
        when = (row["acknowledged_at"] or "")[:16].replace("T", " ")
        note = AlertMessage(
            account_label=row["label"],
            rule="critical_alert_acknowledged",
            severity="CRITICAL",
            confidence="high",
            message=(
                f"A critical alert on {row['label']} was acknowledged at {when} UTC. "
                f"You are receiving this because you are listed as the emergency "
                f"contact for this account. If this acknowledgement is a surprise, "
                f"sign in to the Minas AI Guardian member portal and review the "
                f"incident."
            ),
            evidence={"alert_id": alert_id},
        )
        note.is_notice = True
        ContactRouter(db=db, rules=rules).send(
            note, row["label"], emails, phones, account_id=None, alert_id=None,
        )
    except Exception as exc:  # noqa: BLE001 - the ack must never fail because the note could not send
        logger.error("closing note for acknowledged CRITICAL #%s failed: %s", alert_id, exc)
