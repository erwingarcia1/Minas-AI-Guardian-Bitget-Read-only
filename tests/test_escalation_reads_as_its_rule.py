"""Item 6 of the 2026-09-08 brief: an escalation resolves its original rule.

The customer-side escalation kept the "ESCALATION-unacknowledged: " prefix
and the forced CRITICAL, so a lost API key escalated as "Urgent: Unusual
activity was detected on this account" - 42 times on 2026-08-30. The
advice lookup already stripped the prefix; the headline, the subject and
the severity the customer sees must too. A coverage escalation reads as
coverage.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sentinel.alerts.base import AlertMessage, advice_steps, original_rule
from sentinel.alerts.escalation import Escalator
from tests.test_escalation_reach import Recording, SpyRouter, _account


def _stale(db, account_id, rule, severity, minutes_ago=90):
    sent_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at) VALUES (?, ?, ?, 'k', 'Your account is NOT being watched.', '{}', 'high', ?)",
            (account_id, rule, severity, sent_at),
        )
        return cur.lastrowid


def test_original_rule_strips_only_the_escalation_prefix():
    assert original_rule("ESCALATION-unacknowledged: account_unreachable") == "account_unreachable"
    assert original_rule("withdrawal_initiated") == "withdrawal_initiated"
    assert original_rule(None) is None


def test_a_coverage_escalation_reads_as_coverage_to_the_customer():
    alert = AlertMessage(account_label="acct-1", rule="ESCALATION-unacknowledged: account_unreachable",
                         severity="HIGH", confidence="high", message="x", evidence={}, incident_id=9,
                         escalation=True)
    assert alert.customer_subject() == "Please check: Contact with this account has been lost - acct-1"
    body = alert.render_customer()
    assert body.startswith("acct-1 - contact with this account has been lost.")
    assert "This alert has not been acknowledged and is being sent again." in body
    assert "Unusual activity" not in body and "Urgent" not in alert.customer_subject()
    for step in advice_steps(None, rule="account_unreachable"):
        assert step in body, "the coverage advice, not the withdrawal steps"
    assert "Revoke" not in body


def test_a_critical_escalation_keeps_its_own_words():
    alert = AlertMessage(account_label="acct-1", rule="ESCALATION-unacknowledged: withdrawal_initiated",
                         severity="CRITICAL", confidence="high", message="x", evidence={}, escalation=True)
    assert alert.customer_subject() == "Urgent: A withdrawal was started on this account - acct-1"
    assert "is being sent again" in alert.render_customer()
    # The first sending is not marked.
    first = AlertMessage(account_label="acct-1", rule="withdrawal_initiated", severity="CRITICAL",
                         confidence="high", message="x", evidence={})
    assert "sent again" not in first.render_customer()


def test_the_escalator_hands_the_customer_the_alerts_own_severity_and_the_operator_critical(db):
    """The operator re-send borrows CRITICAL so it takes the CRITICAL channel
    set; the customer's copy keeps the alert's own severity. The
    rule keeps its prefix on both - the template resolves it."""
    account_id = _account(db, alert_email="primary@example.com")
    _stale(db, account_id, "account_unreachable", "HIGH")
    router, ops = SpyRouter(), Recording()
    Escalator([ops], unacknowledged_minutes=15, contact_router=router).check(db)
    assert router.calls[0]["severity"] == "HIGH"
    assert router.calls[0]["rule"] == "ESCALATION-unacknowledged: account_unreachable"
    assert ops.sent[0].severity == "CRITICAL" and ops.sent[0].rule.startswith("ESCALATION")


def test_through_the_real_router_the_customer_email_subject_is_the_coverage_headline(db, monkeypatch):
    from sentinel.alerts.contact import ContactRouter

    account_id = _account(db, alert_email="primary@example.com")
    _stale(db, account_id, "account_unreachable", "HIGH")
    sent = {}

    class _Smtp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            sent["subject"] = msg["Subject"]
            sent["body"] = msg.get_content()

    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    monkeypatch.setenv("SMTP_SENDER", "guardian@example")
    monkeypatch.setattr("sentinel.alerts.email.net.smtp", lambda *a, **k: _Smtp())
    Escalator([Recording()], unacknowledged_minutes=15, contact_router=ContactRouter(db=db)).check(db)
    assert sent["subject"].startswith("Please check: Contact with this account has been lost")
    assert "is being sent again" in sent["body"] and "Unusual activity" not in sent["body"]
