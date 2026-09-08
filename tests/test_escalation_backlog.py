"""Item 7 of the 2026-09-08 brief: a stale alert must not escalate to a
customer, and one customer cannot receive more than a bounded number of
escalation emails in a window.

The 2026-08-30 shape: v1.11.1 made coverage alerts escalate; seven seconds
after the switchover the escalator found 41 five-week-old unacknowledged
alerts on one account and emailed the customer 42 times in eight minutes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sentinel.alerts.escalation import Escalator
from tests.test_escalation_reach import Recording, SpyAlerter, SpyRouter, _account


def _alert(db, account_id, *, rule="account_unreachable", severity="MEDIUM", age=timedelta(minutes=30), key=None):
    sent_at = (datetime.now(timezone.utc) - age).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at) VALUES (?, ?, ?, ?, 'Your account is NOT being watched.', '{}', 'high', ?)",
            (account_id, rule, severity, key or f"k{sent_at}", sent_at),
        )
        return cur.lastrowid


def _deliveries(db, **where):
    sql = "SELECT * FROM deliveries"
    if where:
        sql += " WHERE " + " AND ".join(f"{k} = ?" for k in where)
    with db.cursor() as cur:
        cur.execute(sql, tuple(where.values()))
        return [dict(r) for r in cur.fetchall()]


def test_the_2026_08_30_shape_emails_nobody_and_reports_once(db):
    """41 stale, never-escalated MEDIUM coverage alerts on one account;
    routing now escalates coverage; the engine (re)starts. The customer
    receives NO email; operators receive ONE report with the count; a
    second pass, and a fresh Escalator (the restart), report nothing."""
    account_id = _account(db, alert_email="paula@example.com", alert_email_secondary="sister@example.com")
    ids = [_alert(db, account_id, age=timedelta(weeks=5) + timedelta(minutes=i)) for i in range(41)]
    router, ops, pager = SpyRouter(), Recording(), SpyAlerter()

    Escalator([ops], unacknowledged_minutes=15, operator_alerter=pager, contact_router=router).check(db)

    assert router.calls == [], "a stale backlog reached a customer"
    assert ops.sent == [], "a stale backlog re-fired the global channels"
    reports = [c for c in pager.calls if c["rule"] == "escalation_backlog"]
    assert len(reports) == 1 and len(pager.calls) == 1
    assert reports[0]["evidence"]["count"] == 41 and "41" in reports[0]["message"]
    assert "acct-1 (41)" in reports[0]["message"]
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM alerts WHERE escalated_at IS NOT NULL")
        assert cur.fetchone()["n"] == 41
    suppressed = _deliveries(db, status="suppressed", kind="escalation")
    assert len(suppressed) == 82 and {d["audience"] for d in suppressed} == {"customer", "secondary"}
    assert all(d["alert_id"] in ids for d in suppressed)

    # Reported once: the same pass again, and a fresh instance after a restart.
    Escalator([ops], unacknowledged_minutes=15, operator_alerter=pager, contact_router=router).check(db)
    assert router.calls == [] and len(pager.calls) == 1


def test_a_fresh_alert_still_escalates_and_is_not_a_backlog(db):
    account_id = _account(db, alert_email="primary@example.com")
    _alert(db, account_id, rule="withdrawal_initiated", severity="CRITICAL", age=timedelta(minutes=30))
    router, pager = SpyRouter(), SpyAlerter()
    Escalator([Recording()], unacknowledged_minutes=15, operator_alerter=pager, contact_router=router).check(db)
    assert len(router.calls) == 1 and router.calls[0]["severity"] == "CRITICAL"
    assert not [c for c in pager.calls if c["rule"] == "escalation_backlog"]


def test_the_stale_window_is_configurable_and_bounds_at_its_edge(db):
    account_id = _account(db, alert_email="primary@example.com")
    _alert(db, account_id, rule="withdrawal_initiated", severity="CRITICAL", age=timedelta(hours=3), key="young")
    _alert(db, account_id, rule="withdrawal_initiated", severity="CRITICAL", age=timedelta(hours=5), key="old")
    router, pager = SpyRouter(), SpyAlerter()
    Escalator([Recording()], unacknowledged_minutes=15, operator_alerter=pager, contact_router=router,
              stale_after_hours=4).check(db)
    assert len(router.calls) == 1, "the 3-hour alert escalates; the 5-hour one is backlog"
    assert [c for c in pager.calls if c["rule"] == "escalation_backlog"][0]["evidence"]["count"] == 1


def test_one_customer_receives_at_most_the_bound_plus_one_summary_per_window(db):
    """Six fresh CRITICAL alerts on one account, bound 3: the customer gets
    three escalations and ONE summary naming how many alerts are open;
    the remaining two are recorded as suppressed. Minas still hears all six."""
    account_id = _account(db, alert_email="primary@example.com", alert_email_secondary="sister@example.com")
    for i in range(6):
        _alert(db, account_id, rule="withdrawal_initiated", severity="CRITICAL",
               age=timedelta(minutes=30 + i), key=f"w{i}")
    router, ops, pager = SpyRouter(), Recording(), SpyAlerter()
    esc = Escalator([ops], unacknowledged_minutes=15, operator_alerter=pager, contact_router=router,
                    customer_burst_max=3, customer_burst_window_minutes=60)
    # One alert per pass, as the scheduler would see them arrive.
    for _ in range(6):
        with db.cursor() as cur:
            cur.execute("SELECT id FROM alerts WHERE escalated_at IS NULL ORDER BY id LIMIT 1")
            next_id = cur.fetchone()["id"]
            cur.execute("UPDATE alerts SET acknowledged_at = 'hold' WHERE escalated_at IS NULL AND id != ?", (next_id,))
        esc.check(db)
        with db.cursor() as cur:
            cur.execute("UPDATE alerts SET acknowledged_at = NULL WHERE acknowledged_at = 'hold'")
    primary = [c for c in router.calls if "primary@example.com" in c["emails"]]
    assert len(primary) == 4
    assert [c["rule"] for c in primary[:3]] == ["ESCALATION-unacknowledged: withdrawal_initiated"] * 3
    assert primary[3]["rule"] == "escalation_burst_collapsed"
    suppressed = _deliveries(db, status="suppressed", audience="customer")
    assert len(suppressed) == 2 and all("burst bound" in d["detail"] for d in suppressed)
    assert len(ops.sent) == 6, "the bound is on the customer's inbox, not on Minas"
    # The emergency contact is bounded by the same count.
    secondary = [c for c in router.calls if "sister@example.com" in c["emails"]]
    assert len(secondary) == 3


def test_the_summary_names_the_open_count_and_is_a_notice(db):
    account_id = _account(db, alert_email="primary@example.com")
    for i in range(4):
        _alert(db, account_id, rule="withdrawal_initiated", severity="CRITICAL", age=timedelta(minutes=30 + i), key=f"w{i}")
    captured = []

    class _Router:
        def send(self, detection, account_label, emails, phones, *, account_id=None, alert_id=None):
            captured.append(detection)

    esc = Escalator([Recording()], unacknowledged_minutes=15, contact_router=_Router(), customer_burst_max=3)
    for _ in range(4):
        with db.cursor() as cur:
            cur.execute("SELECT id FROM alerts WHERE escalated_at IS NULL ORDER BY id LIMIT 1")
            next_id = cur.fetchone()["id"]
            cur.execute("UPDATE alerts SET acknowledged_at = 'hold' WHERE escalated_at IS NULL AND id != ?", (next_id,))
        esc.check(db)
        with db.cursor() as cur:
            cur.execute("UPDATE alerts SET acknowledged_at = NULL WHERE acknowledged_at = 'hold'")
    summary = captured[-1]
    assert summary.rule == "escalation_burst_collapsed" and getattr(summary, "is_notice", False)
    assert "4 alerts on acct-1 remain unacknowledged" in summary.message
    assert "3 escalation emails" in summary.message


def test_a_reported_backlog_does_not_count_against_the_customers_burst_bound(db):
    """The backlog rows are marked escalated so they are reported once,
    but nobody was written to for them: a live CRITICAL that escalates
    right after the report still reaches the customer as itself."""
    account_id = _account(db, alert_email="primary@example.com", alert_email_secondary="sister@example.com")
    for i in range(10):
        _alert(db, account_id, age=timedelta(weeks=5) + timedelta(minutes=i), key=f"old{i}")
    router, pager = SpyRouter(), SpyAlerter()
    esc = Escalator([Recording()], unacknowledged_minutes=15, operator_alerter=pager, contact_router=router,
                    customer_burst_max=3)
    esc.check(db)
    assert router.calls == [] and [c["rule"] for c in pager.calls] == ["escalation_backlog"]

    _alert(db, account_id, rule="withdrawal_initiated", severity="CRITICAL", age=timedelta(minutes=30), key="live")
    esc.check(db)
    primary = [c for c in router.calls if "primary@example.com" in c["emails"]]
    secondary = [c for c in router.calls if "sister@example.com" in c["emails"]]
    assert [c["rule"] for c in primary] == ["ESCALATION-unacknowledged: withdrawal_initiated"]
    assert len(secondary) == 1
    assert _deliveries(db, status="suppressed", rule="withdrawal_initiated") == []
