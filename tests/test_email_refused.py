"""The alarm must not silently consume the capacity it needs to alarm (item 4).

From 2026-09-21 23:41 the SMTP provider answered "550 You have reached your
daily email sending quota" - operator pages first, customer alert emails
from 10:48 the next day - and nothing said so. alert_delivered_to_nobody
did not fire because it reads the alert's GLOBAL legs (Minas's own
channels, where Telegram kept delivering); the customer's leg is recorded
as failed by the contact router and paged nobody.
"""
from __future__ import annotations

import smtplib

import pytest

from sentinel.detectors.base import Detection
from sentinel.liveness import DEAD, LIVE, SMTP_DEAD_AFTER_REFUSALS, evaluate
from sentinel.net import smtp_refusal_code
from tests.test_alert_preferences import EMAIL, _account_with_phone, _router
from tests.test_liveness_gate import _by, _facts

QUOTA = smtplib.SMTPDataError(550, b"You have reached your daily email sending quota.")


class _Pager:
    def __init__(self):
        self.pages = []

    def page(self, **kw):
        self.pages.append(kw)
        return len(self.pages)


class _RefusingEmail:
    def __init__(self, *a, **k):
        pass

    def send(self, alert):
        raise QUOTA


class _DeadWire:
    def __init__(self, *a, **k):
        pass

    def send(self, alert):
        raise OSError("connection refused")


def _detection(rule="withdrawal_initiated", severity="CRITICAL"):
    return Detection(rule=rule, severity=severity, confidence="high", message="m", evidence={"order_id": "o1"})


def _stored(db, account_id, det):
    from sentinel.alerts.dispatch import dispatch

    return dispatch(db, {}, account_id, "acct-1", det, {"CRITICAL": 15, "HIGH": 30, "MEDIUM": 720, "LOW": 1440})


def _send(db, monkeypatch, det, channel_cls):
    router = _router(db, monkeypatch)
    monkeypatch.setattr("sentinel.alerts.contact.EmailChannel", channel_cls)
    pager = _Pager()
    router._operator_alerter = pager
    account_id = _account_with_phone(db)
    alert_id = _stored(db, account_id, det)
    router.send(det, "acct-1", [EMAIL], [], account_id=account_id, alert_id=alert_id)
    return pager, alert_id


def test_refusal_codes():
    assert smtp_refusal_code(QUOTA) == 550
    assert smtp_refusal_code(smtplib.SMTPRecipientsRefused({"a@b": (550, b"no such user")})) == 550
    assert smtp_refusal_code(OSError("connection refused")) is None
    assert smtp_refusal_code(smtplib.SMTPResponseException(250, b"ok")) is None


def test_a_refused_customer_critical_pages_operators_with_the_providers_words(db, monkeypatch):
    pager, alert_id = _send(db, monkeypatch, _detection(), _RefusingEmail)
    assert [p["rule"] for p in pager.pages] == ["customer_email_refused"]
    page = pager.pages[0]
    assert page["severity"] == "CRITICAL" and page["evidence"]["smtp_code"] == 550
    assert "daily email sending quota" in page["evidence"]["detail"] and page["evidence"]["alert_id"] == alert_id
    assert page["dedup_scope"] == "smtp-refused:550"
    assert "Telegram" in page["message"], "the page says why it arrives on a channel that is not email"
    with db.cursor() as cur:
        cur.execute("SELECT status, detail FROM deliveries WHERE alert_id = ? AND channel = 'email'", (alert_id,))
        row = cur.fetchone()
        assert row["status"] == "failed" and "quota" in row["detail"]


def test_a_refused_coverage_alert_pages_too(db, monkeypatch):
    pager, _ = _send(db, monkeypatch, _detection("account_unreachable", "HIGH"), _RefusingEmail)
    assert [p["rule"] for p in pager.pages] == ["customer_email_refused"]


@pytest.mark.parametrize("det, channel", [
    (_detection(severity="MEDIUM"), _RefusingEmail),      # not an alert that must reach a human now
    (_detection(), _DeadWire),                             # a connection error is not a refusal (#58)
])
def test_nothing_pages_for_a_routine_severity_or_a_dead_wire(db, monkeypatch, det, channel):
    pager, _ = _send(db, monkeypatch, det, channel)
    assert pager.pages == []


def test_a_refused_secondary_contact_email_is_not_the_customers_leg(db, monkeypatch):
    """Audience is part of the decision: the escalator's copy to the
    secondary contact refusing is recorded, not paged (found by the
    method-aware mutation measurement of 2026-09-22: `or` -> `and` in the
    guard survived because every test sent as the customer)."""
    router = _router(db, monkeypatch)
    monkeypatch.setattr("sentinel.alerts.contact.EmailChannel", _RefusingEmail)
    pager = _Pager()
    router._operator_alerter = pager
    account_id = _account_with_phone(db)
    det = _detection()
    alert_id = _stored(db, account_id, det)
    router.send(det, "acct-1", [EMAIL], [], account_id=account_id, alert_id=alert_id, audience="secondary")
    assert pager.pages == []


def test_the_page_reaches_a_channel_that_is_not_email(db, monkeypatch):
    """Through the real OperatorAlerter: the email leg refuses, Telegram accepts."""
    from sentinel.alerts.operator import OperatorAlerter
    from sentinel.config import RulesConfig

    class _Tg:
        route_name = "telegram"
        recipients = ["-100"]
        sent = []

        def send(self, alert):
            _Tg.sent.append(alert)

    class _Mail:
        route_name = "email"
        recipients = ["ops@minas.test"]

        def send(self, alert):
            raise QUOTA

    alerter = OperatorAlerter(db, {"CRITICAL": [_Tg(), _Mail()]}, RulesConfig("config/rules.yaml"))
    router = _router(db, monkeypatch)
    monkeypatch.setattr("sentinel.alerts.contact.EmailChannel", _RefusingEmail)
    router._operator_alerter = alerter
    account_id = _account_with_phone(db)
    det = _detection()
    alert_id = _stored(db, account_id, det)
    router.send(det, "acct-1", [EMAIL], [], account_id=account_id, alert_id=alert_id)
    assert len(_Tg.sent) == 1 and _Tg.sent[0].rule == "customer_email_refused"
    with db.cursor() as cur:
        cur.execute("SELECT delivered_to FROM operator_pages WHERE rule = 'customer_email_refused'")
        assert cur.fetchone()["delivered_to"] == 1
    # A second refusal inside the cooldown is one page, not two.
    router.send(det, "acct-1", [EMAIL], [], account_id=account_id, alert_id=alert_id)
    assert len(_Tg.sent) == 1


def _delivery(db, status, detail=None, when="2026-09-22T10:48:00+00:00"):
    from sentinel.alerts.delivery import record_delivery

    record_delivery(db, channel="email", recipient="x@example.com", status=status, audience="customer",
                    kind="alert", detail=detail)
    with db.cursor() as cur:
        cur.execute("UPDATE deliveries SET created_at = ? WHERE id = (SELECT max(id) FROM deliveries)", (when,))


def test_email_is_dead_when_its_last_sends_were_all_refused(db, monkeypatch):
    env = {"SMTP_HOST": "smtp.test", "SMTP_SENDER": "alerts@test"}
    _delivery(db, "accepted", when="2026-09-21T20:00:00+00:00")
    for i in range(SMTP_DEAD_AFTER_REFUSALS):
        _delivery(db, "failed", "(550, b'You have reached your daily email sending quota.')", when=f"2026-09-22T10:{48 + i}:00+00:00")
    v = _by(evaluate(_facts(db, env=env)))["smtp"]
    assert v.state == DEAD and "refused" in v.evidence and "quota" in v.evidence
    assert "capacity" in v.evidence and "SMTP does not expose it" in v.evidence, "no invented estimate"


def test_email_is_live_while_any_recent_send_was_accepted(db):
    env = {"SMTP_HOST": "smtp.test", "SMTP_SENDER": "alerts@test"}
    for i in range(SMTP_DEAD_AFTER_REFUSALS - 1):
        _delivery(db, "failed", "(550, b'quota')", when=f"2026-09-22T10:{48 + i}:00+00:00")
    _delivery(db, "accepted", when="2026-09-22T10:59:00+00:00")
    assert _by(evaluate(_facts(db, env=env)))["smtp"].state == LIVE
