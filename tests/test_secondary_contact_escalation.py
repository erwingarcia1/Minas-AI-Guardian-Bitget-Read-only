"""Decision 1 (#27): a secondary contact is an EMERGENCY contact, not a
copy-to.

The complaint that decided it: a secondary contact asked why she was
receiving every alert for accounts she is an emergency contact on. The rule
is about the contact, not the channel. Two gates: severity (LOW and MEDIUM
never reach a secondary at all) and time-and-acknowledgement (HIGH after 60
unacknowledged minutes, CRITICAL after 15 — both from config). Plus the
closing note: every acknowledged CRITICAL tells the secondary it was closed,
because an attacker who can acknowledge from a compromised primary session
must not be able to silence the emergency contact. Cap: two messages per
incident. Usually one. Often none.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sentinel.alerts.escalation import Escalator, send_critical_ack_closing_note
from sentinel.operators import acknowledge_alert
from sentinel.portal import acknowledge_alert_as_member

ORIGINAL_TEXT = "0.5 BTC left the account to bc1qsomewhere"


class Recording:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


class WordSpyRouter:
    """Captures the full message so the wording rules are testable."""

    def __init__(self):
        self.calls = []

    def send(self, detection, account_label, emails, phones, *, account_id=None, alert_id=None):
        self.calls.append({
            "emails": list(emails or []), "phones": list(phones or []),
            "rule": detection.rule, "severity": detection.severity,
            "message": detection.message, "account_id": account_id,
        })


class SpyAlerter:
    def __init__(self):
        self.calls = []

    def page(self, **kwargs):
        self.calls.append(kwargs)


def _account(db, **fields):
    defaults = {"alert_email": "primary@example.com", "alert_phone": None,
                "alert_email_secondary": "second@example.com",
                "alert_phone_secondary": None}
    defaults.update(fields)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, "
            "encrypted_secret, encrypted_passphrase, alert_email, alert_phone, "
            "alert_email_secondary, alert_phone_secondary, status, onboarded_at, updated_at) "
            "VALUES ('acct-1', 'bitget', 'independent', 'x', 'y', 'z', ?, ?, ?, ?, "
            "'active', '2026-01-01', '2026-01-01')",
            (defaults["alert_email"], defaults["alert_phone"],
             defaults["alert_email_secondary"], defaults["alert_phone_secondary"]),
        )
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
        return account_id


def _alert(db, account_id, severity, minutes_ago):
    sent_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, "
            "evidence_json, confidence, sent_at) VALUES (?, 'withdrawal_initiated', ?, 'k', "
            "?, '{}', 'high', ?)",
            (account_id, severity, ORIGINAL_TEXT, sent_at),
        )
        return cur.lastrowid


def _secondary_calls(router):
    return [c for c in router.calls if "second@example.com" in c["emails"]]


def _escalator(router, alerter=None, critical_minutes=15, high_minutes=60, channels=None):
    return Escalator(channels if channels is not None else [Recording()],
                     unacknowledged_minutes=critical_minutes,
                     high_unacknowledged_minutes=high_minutes,
                     operator_alerter=alerter, contact_router=router)


# --- gate 1: severity ---------------------------------------------------------

def test_low_and_medium_never_reach_a_secondary_no_matter_how_stale(db):
    account_id = _account(db)
    _alert(db, account_id, "LOW", minutes_ago=10_000)
    _alert(db, account_id, "MEDIUM", minutes_ago=10_000)
    router = WordSpyRouter()

    escalated = _escalator(router).check(db)

    assert escalated == 0
    assert router.calls == []


# --- gate 2: time and acknowledgement -----------------------------------------

def test_a_high_alert_waits_the_full_hour_before_the_secondary_hears(db):
    account_id = _account(db)
    _alert(db, account_id, "HIGH", minutes_ago=30)
    router = WordSpyRouter()

    _escalator(router).check(db)

    assert _secondary_calls(router) == []


def test_an_unacknowledged_high_reaches_the_secondary_after_the_hour(db):
    account_id = _account(db)
    _alert(db, account_id, "HIGH", minutes_ago=70)
    router = WordSpyRouter()
    channel = Recording()
    alerter = SpyAlerter()

    escalated = _escalator(router, alerter, channels=[channel]).check(db)

    assert escalated == 1
    assert len(_secondary_calls(router)) == 1
    # The decision's literal scope for HIGH: the secondary and nobody else.
    assert channel.sent == []                       # no global re-fire
    assert alerter.calls == []                      # no operator page
    assert all("primary@example.com" not in c["emails"] for c in router.calls)


def test_an_acknowledged_high_never_escalates(db):
    account_id = _account(db)
    alert_id = _alert(db, account_id, "HIGH", minutes_ago=70)
    with db.cursor() as cur:
        cur.execute("UPDATE alerts SET acknowledged_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), alert_id))
    router = WordSpyRouter()

    assert _escalator(router).check(db) == 0
    assert router.calls == []


def test_the_windows_come_from_configuration_not_literals(db):
    """Shrink the HIGH window and the same 40-minute-old alert escalates."""
    account_id = _account(db)
    _alert(db, account_id, "HIGH", minutes_ago=40)
    router = WordSpyRouter()

    assert _escalator(router, high_minutes=60).check(db) == 0
    assert _escalator(router, high_minutes=30).check(db) == 1


def test_a_high_escalation_fires_once_not_hourly(db):
    account_id = _account(db)
    _alert(db, account_id, "HIGH", minutes_ago=70)
    router = WordSpyRouter()
    esc = _escalator(router)

    esc.check(db)
    esc.check(db)

    assert len(_secondary_calls(router)) == 1


# --- the wording ---------------------------------------------------------------

def test_the_secondary_is_told_why_they_are_hearing_at_all(db):
    """Replacing "why do I get all of these" with "why am I suddenly getting
    this one" is not an improvement."""
    account_id = _account(db)
    _alert(db, account_id, "CRITICAL", minutes_ago=30)
    router = WordSpyRouter()

    _escalator(router).check(db)

    msg = _secondary_calls(router)[0]["message"]
    assert "You are receiving this because" in msg
    assert "has not responded" in msg
    assert "acct-1" in msg
    assert "15 minutes" in msg


def test_the_secondary_message_carries_no_amounts_addresses_or_links(db):
    account_id = _account(db)
    _alert(db, account_id, "CRITICAL", minutes_ago=30)
    router = WordSpyRouter()

    _escalator(router).check(db)

    msg = _secondary_calls(router)[0]["message"]
    assert "0.5" not in msg and "BTC" not in msg and "bc1q" not in msg
    assert "http" not in msg  # no one-tap anything; sign in and look


# --- the closing note -----------------------------------------------------------

class _RouterFactorySpy:
    """Stands in for ContactRouter inside the closing-note path."""

    instances: list = []

    def __init__(self, db=None, rules=None, operator_alerter=None):
        self.calls = []
        _RouterFactorySpy.instances.append(self)

    def send(self, detection, account_label, emails, phones, *, account_id=None, alert_id=None):
        self.calls.append({
            "emails": list(emails or []), "phones": list(phones or []),
            "message": detection.message, "severity": detection.severity,
            "account_id": account_id, "alert_id": alert_id,
        })


def _note_calls():
    return [c for inst in _RouterFactorySpy.instances for c in inst.calls]


def _patch_router(monkeypatch):
    _RouterFactorySpy.instances = []
    monkeypatch.setattr("sentinel.alerts.contact.ContactRouter", _RouterFactorySpy)


def test_acknowledging_a_critical_sends_the_secondary_one_closing_note(db, monkeypatch):
    """The compromise scenario: acknowledged BEFORE the window, so the
    secondary never heard the alert - the closing note is the only thing
    standing between a compromised primary session and total silence."""
    _patch_router(monkeypatch)
    account_id = _account(db)
    alert_id = _alert(db, account_id, "CRITICAL", minutes_ago=2)

    assert acknowledge_alert(db, alert_id, "operator-1") is True

    calls = _note_calls()
    assert len(calls) == 1
    assert calls[0]["emails"] == ["second@example.com"]
    assert "primary@example.com" not in calls[0]["emails"]
    assert "was acknowledged at" in calls[0]["message"]
    assert "0.5" not in calls[0]["message"] and "bc1q" not in calls[0]["message"]


def test_the_member_acknowledgement_path_sends_it_too(db, monkeypatch):
    """The pair-of-gates rule: two acknowledgement writers exist, and hooking
    only one would leave the other silently mute."""
    _patch_router(monkeypatch)
    account_id = _account(db)
    alert_id = _alert(db, account_id, "CRITICAL", minutes_ago=2)

    assert acknowledge_alert_as_member(
        db, alert_id, "primary@example.com", [account_id]) is True

    assert len(_note_calls()) == 1


def test_acknowledging_a_high_sends_no_closing_note(db, monkeypatch):
    _patch_router(monkeypatch)
    account_id = _account(db)
    alert_id = _alert(db, account_id, "HIGH", minutes_ago=2)

    assert acknowledge_alert(db, alert_id, "operator-1") is True

    assert _note_calls() == []


def test_a_second_acknowledgement_sends_no_second_note(db, monkeypatch):
    _patch_router(monkeypatch)
    account_id = _account(db)
    alert_id = _alert(db, account_id, "CRITICAL", minutes_ago=2)

    assert acknowledge_alert(db, alert_id, "operator-1") is True
    assert acknowledge_alert(db, alert_id, "operator-2") is False

    assert len(_note_calls()) == 1


def test_no_secondary_contact_means_deliberate_silence_not_a_failure(db, monkeypatch):
    """#57's distinction, applied in reverse: a contact who is never
    contacted BY DESIGN must not read as a delivery failure - no router
    call, no sms_deliveries bookkeeping, nothing."""
    _patch_router(monkeypatch)
    account_id = _account(db, alert_email_secondary=None, alert_phone_secondary=None)
    alert_id = _alert(db, account_id, "CRITICAL", minutes_ago=2)

    assert acknowledge_alert(db, alert_id, "operator-1") is True

    assert _note_calls() == []
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sms_deliveries")
        assert cur.fetchone()[0] == 0


def test_a_broken_note_never_fails_the_acknowledgement(db, monkeypatch):
    class Exploding:
        def __init__(self, **kwargs):
            raise RuntimeError("smtp fell over")

    monkeypatch.setattr("sentinel.alerts.contact.ContactRouter", Exploding)
    account_id = _account(db)
    alert_id = _alert(db, account_id, "CRITICAL", minutes_ago=2)

    assert acknowledge_alert(db, alert_id, "operator-1") is True
    with db.cursor() as cur:
        cur.execute("SELECT acknowledged_at FROM alerts WHERE id = ?", (alert_id,))
        assert cur.fetchone()["acknowledged_at"] is not None


# --- the cap: two messages per incident, usually one, often none ---------------

def test_an_escalated_then_acknowledged_critical_costs_the_secondary_two_messages(db, monkeypatch):
    _patch_router(monkeypatch)
    account_id = _account(db)
    alert_id = _alert(db, account_id, "CRITICAL", minutes_ago=30)
    router = WordSpyRouter()

    _escalator(router).check(db)                     # message 1: the escalation
    assert acknowledge_alert(db, alert_id, "op") is True   # message 2: the note

    total = len(_secondary_calls(router)) + len(_note_calls())
    assert total == 2


def test_the_closing_note_helper_refuses_a_missing_alert(db, monkeypatch):
    _patch_router(monkeypatch)
    send_critical_ack_closing_note(db, 424242)
    assert _note_calls() == []


# --- the immediate path: primary only ------------------------------------------

def test_the_immediate_alert_reaches_the_primary_only(db, cipher):
    """Gate 1 at the source: the per-account routing the poller hands the
    router no longer includes the secondary at all - LOW and MEDIUM can
    never reach them because nothing ever offers them the address."""
    from sentinel.config import RulesConfig
    from sentinel.poller import AccountPoller

    account_id = _account(db, alert_phone="+15550001", alert_phone_secondary="+15550002")
    poller = AccountPoller(db, cipher, RulesConfig("config/rules.yaml"), {})

    emails, phones = poller._account_contact(account_id)

    assert emails == ["primary@example.com"]
    assert phones == ["+15550001"]


def test_the_closing_note_carries_the_real_acknowledgement_time(db, monkeypatch):
    """The time IS the content: "was acknowledged at <time>" is what lets the
    emergency contact judge recency. A note rendering None or blank where the
    time belongs still says "acknowledged" and still soothes - which is
    exactly how two mutants of the timestamp line survived the first scoring
    pass. The stored acknowledged_at must appear, minute-precise."""
    _patch_router(monkeypatch)
    account_id = _account(db)
    alert_id = _alert(db, account_id, "CRITICAL", minutes_ago=2)

    assert acknowledge_alert(db, alert_id, "operator-1") is True

    with db.cursor() as cur:
        cur.execute("SELECT acknowledged_at FROM alerts WHERE id = ?", (alert_id,))
        stored = cur.fetchone()["acknowledged_at"]
    msg = _note_calls()[0]["message"]
    assert stored[:16].replace("T", " ") in msg
    assert "None" not in msg
