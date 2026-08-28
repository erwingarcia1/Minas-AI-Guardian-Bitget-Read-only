"""#103 item 2: delivery is recorded PER RECIPIENT PER CHANNEL, for every leg.

"Was this customer actually told" was unanswerable for every alert in the
database: `alerts.delivered_to` counted the global channels only and the
per-member email/Telegram/SMS legs left nothing behind. The audit's verdict
on 124 unreachable alerts had to be "unverifiable". These tests execute each
leg through its real entry point and read the record back - and pin that
`delivered_to` still means what it meant, that the old tables still answer
as views, and that a database from before this shipped is migrated, not
truncated.
"""
from __future__ import annotations

import sqlite3

import pytest

from sentinel.alerts.delivery import deliveries_for_alert, last_accepted, reached_a_person, record_delivery
from sentinel.alerts.dispatch import dispatch
from sentinel.db import Database
from sentinel.detectors.base import Detection
from tests.conftest import make_account


def _detection(rule="withdrawal_initiated", severity="CRITICAL"):
    return Detection(rule=rule, severity=severity, confidence="high", message="m", evidence={"order_id": "wd-1"})


class _Channel:
    """A global channel as the routing builder hands it out: named, with
    recipients, and either working or broken."""

    def __init__(self, name, recipients, broken=False):
        self.route_name = name
        self.recipients = recipients
        self.broken = broken
        self.sent = 0

    def send(self, alert):
        if self.broken:
            raise RuntimeError(f"{self.route_name} is down")
        self.sent += 1


def _rows(db, alert_id=None, **where):
    with db.cursor() as cur:
        clauses = [] if alert_id is None else ["alert_id = ?"]
        params = [] if alert_id is None else [alert_id]
        for k, v in where.items():
            clauses.append(f"{k} = ?")
            params.append(v)
        cur.execute("SELECT * FROM deliveries" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY id", params)
        return [dict(r) for r in cur.fetchall()]


# --- global channels: one row per recipient, audience operator --------------------------

def test_dispatch_records_every_global_recipient_and_keeps_delivered_to(db):
    aid = make_account(db)
    email = _Channel("email", ["ops@minas.test", "erwin@minas.test"])
    tele = _Channel("telegram", ["-100123"])
    broken = _Channel("telegram_team", ["-100999"], broken=True)
    alert_id = dispatch(db, {"CRITICAL": [email, tele, broken]}, aid, "acct", _detection(), {"CRITICAL": 15})

    rows = _rows(db, alert_id)
    assert [(r["channel"], r["recipient"], r["status"], r["audience"]) for r in rows] == [
        ("email", "ops@minas.test", "accepted", "operator"),
        ("email", "erwin@minas.test", "accepted", "operator"),
        ("telegram", "-100123", "accepted", "operator"),
        ("telegram_team", "-100999", "failed", "operator"),
    ]
    assert "telegram_team is down" in rows[-1]["detail"]
    with db.cursor() as cur:
        cur.execute("SELECT delivered_to FROM alerts WHERE id = ?", (alert_id,))
        assert cur.fetchone()["delivered_to"] == 2, "delivered_to still counts global channels that took it"
    assert reached_a_person(db, alert_id) is True


def test_a_snoozed_alert_records_every_leg_as_skipped_not_silently(db):
    from sentinel.snooze import snooze_alert

    aid = make_account(db)
    first = dispatch(db, {"CRITICAL": [_Channel("email", ["ops@minas.test"])]}, aid, "acct", _detection(), {"CRITICAL": 0})
    with db.cursor() as cur:
        cur.execute("SELECT dedup_key FROM alerts WHERE id = ?", (first,))
        key = cur.fetchone()["dedup_key"]
    snooze_alert(db, aid, key, "withdrawal_initiated", 1, "customer@x.test")
    second = dispatch(db, {"CRITICAL": [_Channel("email", ["ops@minas.test"])]}, aid, "acct", _detection(), {"CRITICAL": 0})
    assert [r["status"] for r in _rows(db, second)] == ["skipped"]
    assert "snoozed" in _rows(db, second)[0]["detail"]
    assert reached_a_person(db, second) is False


# --- the customer's own legs, through the router --------------------------------------

class _RecEmail:
    broken = False

    def __init__(self, host, port, user, pw, sender, recipients, *, audience="operator"):
        self.recipients = recipients

    def send(self, alert):
        if _RecEmail.broken:
            raise RuntimeError("550 mailbox unavailable")


@pytest.fixture
def router(monkeypatch, db):
    _RecEmail.broken = False
    monkeypatch.setattr("sentinel.alerts.contact.EmailChannel", _RecEmail)
    monkeypatch.setattr("sentinel.alerts.contact.build_sms_channel", lambda recipients: None)  # no provider
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_SENDER", "alerts@test")
    from sentinel.alerts.contact import ContactRouter

    return ContactRouter(db=db)


def _alert_row(db, aid):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, confidence, "
            "sent_at, delivered_to) VALUES (?, 'withdrawal_initiated', 'CRITICAL', 'k', 'm', '{}', 'high', "
            "'2026-08-28T00:00:00+00:00', 0)", (aid,))
        return cur.lastrowid


def test_the_member_email_leg_is_recorded_accepted_per_address(db, router):
    aid = make_account(db)
    alert_id = _alert_row(db, aid)
    router.send(_detection(), "acct", ["Owner@X.test", "kin@x.test"], [], account_id=aid, alert_id=alert_id)
    rows = _rows(db, alert_id, channel="email")
    assert [(r["recipient"], r["status"], r["audience"], r["kind"]) for r in rows] == [
        ("owner@x.test", "accepted", "customer", "alert"), ("kin@x.test", "accepted", "customer", "alert")]
    assert rows[0]["provider"] == "smtp"
    assert reached_a_person(db, alert_id) is True


def test_a_refused_email_is_recorded_failed_with_the_transports_words(db, router):
    aid = make_account(db)
    alert_id = _alert_row(db, aid)
    _RecEmail.broken = True
    router.send(_detection(), "acct", ["owner@x.test"], [], account_id=aid, alert_id=alert_id)
    rows = _rows(db, alert_id, channel="email")
    assert [(r["status"], r["detail"]) for r in rows] == [("failed", "550 mailbox unavailable")]
    assert reached_a_person(db, alert_id) is False


def test_no_smtp_records_the_leg_as_skipped_not_absent(db, router, monkeypatch):
    monkeypatch.delenv("SMTP_HOST")
    from sentinel.alerts.contact import ContactRouter

    aid = make_account(db)
    alert_id = _alert_row(db, aid)
    ContactRouter(db=db).send(_detection(), "acct", ["owner@x.test"], [], account_id=aid, alert_id=alert_id)
    rows = _rows(db, alert_id, channel="email")
    assert [(r["status"], r["detail"]) for r in rows] == [("skipped", "SMTP is not configured")]


def test_sms_with_no_provider_records_every_phone_as_skipped(db, router):
    aid = make_account(db)
    alert_id = _alert_row(db, aid)
    router.send(_detection(), "acct", [], ["+15550001", "+15550002"], account_id=aid, alert_id=alert_id)
    rows = _rows(db, alert_id, channel="sms")
    assert [(r["recipient"], r["status"], r["detail"]) for r in rows] == [
        ("+15550001", "skipped", "no SMS provider configured"), ("+15550002", "skipped", "no SMS provider configured")]
    # The old SMS-only record still answers, through the view, in its old words.
    with db.cursor() as cur:
        cur.execute("SELECT phone, status FROM sms_deliveries WHERE alert_id = ? ORDER BY id", (alert_id,))
        assert [tuple(r) for r in cur.fetchall()] == [("+15550001", "skipped"), ("+15550002", "skipped")]


def test_nothing_recorded_is_unknowable_not_false(db):
    aid = make_account(db)
    alert_id = _alert_row(db, aid)
    assert deliveries_for_alert(db, alert_id) == []
    assert reached_a_person(db, alert_id) is None


def test_the_secondary_note_is_recorded_as_the_secondary_audience(db, router):
    from sentinel.alerts.base import AlertMessage
    from sentinel.alerts.escalation import Escalator

    aid = make_account(db)
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = 'owner@x.test', alert_email_secondary = 'kin@x.test', "
                    "alert_phone = NULL, alert_phone_secondary = NULL WHERE id = ?", (aid,))
    alert_id = _alert_row(db, aid)
    with db.cursor() as cur:
        cur.execute("UPDATE alerts SET sent_at = '2026-01-01T00:00:00+00:00' WHERE id = ?", (alert_id,))
    esc = Escalator([_Channel("telegram", ["-1"])], unacknowledged_minutes=15, contact_router=router)
    esc.check(db)
    rows = _rows(db, alert_id)
    assert ("telegram", "-1", "accepted", "operator", "escalation") in [
        (r["channel"], r["recipient"], r["status"], r["audience"], r["kind"]) for r in rows]
    assert ("email", "owner@x.test", "accepted", "customer", "escalation") in [
        (r["channel"], r["recipient"], r["status"], r["audience"], r["kind"]) for r in rows]
    assert ("email", "kin@x.test", "accepted", "secondary", "escalation") in [
        (r["channel"], r["recipient"], r["status"], r["audience"], r["kind"]) for r in rows]
    assert isinstance(AlertMessage, type)


# --- operator pages and the digest ------------------------------------------------------

def test_an_operator_page_records_each_leg_at_minas(db):
    from sentinel.alerts.operator import OperatorAlerter
    from sentinel.config import RulesConfig

    alerter = OperatorAlerter(db, {"HIGH": [_Channel("telegram_team", ["-100"]), _Channel("email", ["ops@minas.test"], broken=True)]},
                              RulesConfig("config/rules.yaml"))
    alerter.page(rule="something_broke", severity="HIGH", message="m", evidence={})
    rows = _rows(db, kind="operator_page")
    assert [(r["channel"], r["recipient"], r["status"], r["audience"]) for r in rows] == [
        ("telegram_team", "-100", "accepted", "operator"), ("email", "ops@minas.test", "failed", "operator")]


def test_a_digest_send_is_recorded_as_a_digest(db, router):
    from sentinel.alerts.digest import send_member_digests

    aid = make_account(db)
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = 'owner@x.test' WHERE id = ?", (aid,))
    assert send_member_digests(db) == 1
    assert last_accepted(db, kind="digest") is not None
    assert last_accepted(db, kind="digest", recipient="OWNER@x.test") is not None
    assert last_accepted(db, kind="alert") is None


# --- the migration: a database from before this shipped ----------------------------------

def test_a_legacy_database_is_migrated_row_for_row_and_the_views_replace_the_tables(tmp_path):
    path = tmp_path / "legacy.db"
    Database(str(path)).close()  # the real schema...
    conn = sqlite3.connect(path)
    conn.executescript("""
        DROP VIEW sms_deliveries; DROP VIEW contact_deliveries; DROP TABLE deliveries;
        CREATE TABLE sms_deliveries (id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL,
            alert_id INTEGER, rule TEXT NOT NULL, phone TEXT, provider TEXT, provider_message_id TEXT,
            status TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL);
        CREATE TABLE contact_deliveries (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL,
            channel TEXT NOT NULL, alert_id INTEGER, delivered_at TEXT NOT NULL);
        INSERT INTO accounts (label, topology, encrypted_api_key, encrypted_secret, encrypted_passphrase,
            onboarded_at, updated_at) VALUES ('legacy', 'independent', 'x', 'y', 'z', '2026-01-01', '2026-01-01');
        INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, confidence, sent_at)
            VALUES (1, 'withdrawal_initiated', 'CRITICAL', 'k', 'm', '{}', 'high', '2026-07-13T12:00:00+00:00');
        INSERT INTO sms_deliveries (account_id, alert_id, rule, phone, provider, provider_message_id, status, detail, created_at)
            VALUES (1, 1, 'withdrawal_initiated', '+15550001', 'twilio', 'SM1', 'sent', NULL, '2026-07-13T12:48:53+00:00'),
                   (1, 1, 'ESCALATION-unacknowledged: withdrawal_initiated', NULL, NULL, NULL, 'skipped', 'no phone number on file', '2026-08-27T12:35:56+00:00');
        INSERT INTO contact_deliveries (email, channel, alert_id, delivered_at) VALUES ('Owner@X.test', 'email', 1, '2026-08-01T00:00:00+00:00');
    """)
    conn.commit()
    conn.close()

    Database(str(path)).close()  # ...reopened: the migration runs before schema.sql

    conn = sqlite3.connect(path)
    kinds = dict(conn.execute("SELECT name, type FROM sqlite_master WHERE name IN ('sms_deliveries', 'contact_deliveries', 'deliveries')").fetchall())
    assert kinds == {"deliveries": "table", "sms_deliveries": "view", "contact_deliveries": "view"}
    rows = conn.execute("SELECT channel, recipient, status, kind, provider, provider_ref, detail FROM deliveries ORDER BY id").fetchall()
    assert rows == [
        ("sms", "+15550001", "accepted", "alert", "twilio", "SM1", None),
        ("sms", None, "skipped", "escalation", None, None, "no phone number on file"),
        ("email", "Owner@X.test", "accepted", "alert", None, None, None),
    ]
    # and the old readers see the old shape
    assert conn.execute("SELECT phone, status, provider_message_id FROM sms_deliveries ORDER BY id").fetchall() == [
        ("+15550001", "sent", "SM1"), (None, "skipped", None)]
    assert conn.execute("SELECT email, channel FROM contact_deliveries").fetchall() == [("Owner@X.test", "email")]
    # a second open is a no-op, not a second copy
    conn.close()
    Database(str(path)).close()
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 3
    conn.close()


def test_record_delivery_refuses_an_unknown_channel_and_never_raises_on_a_dead_db(db):
    with pytest.raises(ValueError):
        record_delivery(db, channel="carrier-pigeon", recipient="x", status="accepted")
    assert record_delivery(None, channel="email", recipient="x", status="accepted") is None


def test_channel_names_come_from_the_route_then_the_class_then_a_guess():
    from sentinel.alerts.delivery import channel_name_of
    from sentinel.alerts.email import EmailChannel

    routed = _Channel("telegram_team", ["-1"])
    assert channel_name_of(routed) == "telegram_team"
    assert channel_name_of(EmailChannel("h", 25, "u", "p", "s@x", ["a@x"])) == "email"

    class MailStub:
        pass

    class SmsStub:
        pass

    class Other:
        pass

    assert channel_name_of(MailStub()) == "email"
    assert channel_name_of(SmsStub()) == "sms"
    assert channel_name_of(Other()) == "telegram"
