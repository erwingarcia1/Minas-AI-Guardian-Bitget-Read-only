"""The alarm must not silently consume the capacity it needs to alarm (item 4).

From 2026-09-21 23:41 the SMTP provider answered "550 You have reached your
daily email sending quota" - operator pages first, customer alert emails
from 10:48 the next day - and nothing said so. alert_delivered_to_nobody
did not fire because it reads the alert's GLOBAL legs (Minas's own
channels, where Telegram kept delivering); the customer's leg is recorded
as failed by the contact router and paged nobody.

2026-10-25, the same blindness from the other side: a member's password
reset "never arrived", and nothing Minas reads would have shown a refused
email. The liveness Email check let a row that was never sent keep it
LIVE through refusals.
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


def _delivery(db, status, detail=None, when="2026-09-22T10:48:00+00:00", kind="alert"):
    from sentinel.alerts.delivery import record_delivery

    record_delivery(db, channel="email", recipient="x@example.com", status=status, audience="customer",
                    kind=kind, detail=detail)
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


# --- 2026-10-25: only a send counts towards the refusal window -------------------------------
#
# The window was the newest five email ROWS. Three statuses are not sends -
# each below with the detail its writer gives it - and one of them among
# the refusals kept Email LIVE through a total refusal outage.

SMTP_ENV = {"SMTP_HOST": "smtp.test", "SMTP_SENDER": "alerts@test"}
NEVER_SENT = {
    "skipped": ("alert", "member preference: channel off for this severity"),
    "held": ("alert", "quiet hours - released in the next digest"),
    "suppressed": ("escalation", "burst bound: 3 escalations to this customer in the last 60 minutes "
                                 "(max 3 + one summary)"),
}


@pytest.mark.parametrize("never_sent", [("skipped",), ("held",), ("suppressed",), ("skipped", "held", "suppressed")],
                         ids=["skipped", "held", "suppressed", "all-three"])
def test_a_row_that_was_never_sent_cannot_keep_email_live_through_refusals(db, never_sent):
    for i in range(SMTP_DEAD_AFTER_REFUSALS):
        _delivery(db, "failed", "(550, b'You have reached your daily email sending quota.')",
                  when=f"2026-09-22T10:{48 + i}:00+00:00")
    # Between the refusals rather than above them all: a window that only
    # began at the newest send would pass a row stacked on top, not this.
    for j, status in enumerate(never_sent):
        kind, detail = NEVER_SENT[status]
        _delivery(db, status, detail, when=f"2026-09-22T10:{50 - j}:30+00:00", kind=kind)
    v = _by(evaluate(_facts(db, env=SMTP_ENV)))["smtp"]
    assert v.state == DEAD, v.evidence
    assert "newest 2026-09-22T10:52:00+00:00" in v.evidence and "quota" in v.evidence


def test_an_accepted_send_among_the_newest_five_sends_keeps_email_live(db):
    """What must not change: five refusals in all, rows that were never sent
    between them, and one acceptance among the newest five SENDS is a
    provider still taking our mail. A window of failures alone would read
    the five refusals and call it dead."""
    refusal = "(550, b'5.1.1 User unknown')"
    for minute in (47, 48, 49):
        _delivery(db, "failed", refusal, when=f"2026-09-22T10:{minute}:00+00:00")
    _delivery(db, "accepted", when="2026-09-22T10:50:00+00:00")
    _delivery(db, "skipped", NEVER_SENT["skipped"][1], when="2026-09-22T10:50:30+00:00")
    _delivery(db, "failed", refusal, when="2026-09-22T10:51:00+00:00")
    _delivery(db, "held", NEVER_SENT["held"][1], when="2026-09-22T10:51:30+00:00")
    _delivery(db, "failed", refusal, when="2026-09-22T10:52:00+00:00")
    v = _by(evaluate(_facts(db, env=SMTP_ENV)))["smtp"]
    assert v.state == LIVE and "last accepted email 2026-09-22T10:50:00+00:00" in v.evidence, v.evidence


def test_four_refusals_are_not_five_however_many_rows_were_never_sent(db):
    """Nor does a row that was never sent count AS a refusal: four refusals
    followed by a skipped, a held and a suppressed row are four refusals."""
    for i in range(SMTP_DEAD_AFTER_REFUSALS - 1):
        _delivery(db, "failed", "(550, b'quota')", when=f"2026-09-22T10:{48 + i}:00+00:00")
    for j, status in enumerate(sorted(NEVER_SENT)):
        kind, detail = NEVER_SENT[status]
        _delivery(db, status, detail, when=f"2026-09-22T10:{55 + j}:00+00:00", kind=kind)
    assert _by(evaluate(_facts(db, env=SMTP_ENV)))["smtp"].state == LIVE


@pytest.mark.parametrize("env", [{}, {"SMTP_HOST": "smtp.test"}, {"SMTP_HOST": "  ", "SMTP_SENDER": "alerts@test"}],
                         ids=["unset", "no-sender", "blank-host"])
def test_email_with_smtp_unset_is_dead_for_that_reason_whatever_was_sent_before(db, env):
    _delivery(db, "accepted", when="2026-09-22T10:59:00+00:00")
    v = _by(evaluate(_facts(db, env=env)))["smtp"]
    assert (v.state, v.evidence) == (DEAD, "SMTP_HOST/SMTP_SENDER unset: no email leaves this host")
