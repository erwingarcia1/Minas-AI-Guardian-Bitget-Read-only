"""#104 batch B: the contact router's method decisions, pinned.

The mutation gate never measured a class method until 2026-09-22; these
are the eight ContactRouter decisions that survived once it did, each
retired from the `unpinned` ledger by a test here (one, `_phone_owners`'s
guard, is argued equivalent below and stays as such).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from sentinel.alerts.contact import ContactRouter
from sentinel.alerts.delivery import record_delivery
from sentinel.alerts.preferences import set_member_prefs
from sentinel.detectors.base import Detection
from sentinel.portal import set_member_password
from tests.test_alert_preferences import EMAIL, _account_with_phone, _RecEmail, _RecSms, _router, _stored

BOB = "bob@example.com"
FIXED_NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def member(db):
    set_member_password(db, EMAIL, "a-long-enough-password-1", notify=None)
    return EMAIL


class _FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW


def _deliveries(db, channel):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM deliveries WHERE channel = ? ORDER BY id", (channel,))
        return [dict(r) for r in cur.fetchall()]


def test_a_held_first_address_does_not_stop_the_second_from_being_delivered(db, member, monkeypatch):
    """`continue`, not `break`: alice is in quiet hours and held; bob, listed
    after her, is not and gets his email."""
    set_member_password(db, BOB, "a-long-enough-password-2", notify=None)
    set_member_prefs(db, member, timezone_name="UTC", quiet_hours_enabled=True,
                     quiet_start="00:00", quiet_end="23:59", grid={})
    account_id = _account_with_phone(db)
    router = _router(db, monkeypatch)
    det, alert_id = _stored(db, account_id, "HIGH")
    router.send(det, "acct-1", [EMAIL, BOB], [], account_id=account_id, alert_id=alert_id)
    assert [e.recipients for e in _RecEmail.instances] == [[BOB]]
    assert [d["status"] for d in _deliveries(db, "email") if d["recipient"] == EMAIL] == ["held"]


def test_an_unowned_first_phone_does_not_stop_the_owned_second_from_being_texted(db, member, monkeypatch):
    """`continue`, not `break`: a phone with no paired login keeps today's
    behaviour and the owned phone after it is still judged (and kept)."""
    account_id = _account_with_phone(db)  # +15550001111 is alice's
    router = _router(db, monkeypatch)
    det, alert_id = _stored(db, account_id, "HIGH")
    router.send(det, "acct-1", [EMAIL], ["+15559990000", "+15550001111"], account_id=account_id, alert_id=alert_id)
    assert [s.recipients for s in _RecSms.instances] == [["+15559990000", "+15550001111"]]


def test_an_sms_record_with_no_account_is_not_written(db, monkeypatch):
    """`db is None or account_id is None`: either absent means no row. (The
    same guard in _phone_owners is equivalent under `and`: a query for
    account None finds no row and returns {} by a longer route.)"""
    router = _router(db, monkeypatch)
    router._record_sms(None, None, "withdrawal_burst", "skipped", detail="x")
    assert _deliveries(db, "sms") == []


def test_the_sms_record_carries_the_legs_audience_and_kind(db, monkeypatch):
    """`leg = getattr(self, "_leg", None) or {}`: when a leg is set its
    audience and kind reach the row, not the defaults."""
    account_id = _account_with_phone(db)
    router = _router(db, monkeypatch)
    det, alert_id = _stored(db, account_id, "HIGH")
    router.send(det, "acct-1", [], ["+15550001111"], account_id=account_id, alert_id=alert_id, audience="secondary")
    rows = _deliveries(db, "sms")
    assert [(r["audience"], r["kind"], r["status"]) for r in rows] == [("secondary", "alert", "accepted")]


def test_a_router_with_no_db_sends_no_telegram_copy_and_logs_nothing(monkeypatch, caplog):
    """`db is None or not emails`: no db is silence, not an attempt that
    fails inside the guard and logs a warning per alert."""
    _RecEmail.instances = []
    monkeypatch.setattr("sentinel.alerts.contact.EmailChannel", _RecEmail)
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_SENDER", "alerts@test")
    det = Detection(rule="withdrawal_burst", severity="HIGH", confidence="high", message="m", evidence={"order_id": "o"})
    with caplog.at_level(logging.WARNING, logger="sentinel.alerts.contact"):
        ContactRouter(db=None).send(det, "acct-1", [EMAIL], [], account_id=None, alert_id=None)
    assert [e.recipients for e in _RecEmail.instances] == [[EMAIL]]
    assert not [r for r in caplog.records if "telegram" in r.getMessage()], caplog.text


def test_the_sms_cooldown_edge_is_open(db, monkeypatch):
    """`now - last < cooldown`: a text sent exactly thirty minutes ago no
    longer suppresses; one sent 29 minutes ago still does."""
    monkeypatch.setattr("sentinel.alerts.contact.datetime", _FixedClock)
    account_id = _account_with_phone(db)
    router = _router(db, monkeypatch)

    def sent_at(when):
        record_delivery(db, channel="sms", recipient="+15550001111", status="accepted", audience="customer",
                        kind="alert", account_id=account_id, rule="withdrawal_burst")
        with db.cursor() as cur:
            cur.execute("UPDATE deliveries SET created_at = ? WHERE id = (SELECT max(id) FROM deliveries)",
                        (when.isoformat(),))

    sent_at(FIXED_NOW - timedelta(minutes=30))
    assert router._sms_suppressed(account_id, "withdrawal_burst") is False
    sent_at(FIXED_NOW - timedelta(minutes=29))
    assert router._sms_suppressed(account_id, "withdrawal_burst") is True


def test_a_router_with_no_db_still_texts_and_never_reads_preferences(monkeypatch):
    """`auditable = db is not None and account_id is not None`: with no db
    there is nothing to audit against; `or` would read preferences from a
    db that is not there and raise before the text goes out."""
    _RecSms.instances = []
    monkeypatch.setattr("sentinel.alerts.contact.build_sms_channel",
                        lambda recipients, audience="customer": _RecSms(recipients) if recipients else None)
    det = Detection(rule="withdrawal_initiated", severity="CRITICAL", confidence="high", message="m",
                    evidence={"order_id": "o"})
    ContactRouter(db=None).send(det, "acct-1", [], ["+15550001111"], account_id=7, alert_id=None)
    assert [s.recipients for s in _RecSms.instances] == [["+15550001111"]]


def test_a_held_owners_phone_ahead_of_another_does_not_stop_the_second_text(db, member, monkeypatch):
    """The other `continue` in the phone loop: alice is held for quiet hours
    so her phone is skipped, and bob's phone, listed after it, is still texted."""
    set_member_password(db, BOB, "a-long-enough-password-2", notify=None)
    set_member_prefs(db, member, timezone_name="UTC", quiet_hours_enabled=True,
                     quiet_start="00:00", quiet_end="23:59", grid={})
    account_id = _account_with_phone(db)
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email_secondary = ?, alert_phone_secondary = ? WHERE id = ?",
                    (BOB, "+15550002222", account_id))
    router = _router(db, monkeypatch)
    det, alert_id = _stored(db, account_id, "HIGH")
    router.send(det, "acct-1", [EMAIL, BOB], ["+15550001111", "+15550002222"], account_id=account_id, alert_id=alert_id)
    assert [s.recipients for s in _RecSms.instances] == [["+15550002222"]]
