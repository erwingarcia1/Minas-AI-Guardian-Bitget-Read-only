"""#24 / decision 4: channels per severity, quiet hours, and the floor.

Quiet hours off by default, non-critical only, the member's own timezone,
22:00-07:00 when on; held alerts are QUEUED into the next digest, never
dropped; email is the un-disableable CRITICAL channel; removing the email
is an onboarding-blocking error; an unknown timezone means quiet hours
cannot be enabled at all - never UTC. A member who never opens the page
sees exactly what happened before.
"""
from __future__ import annotations

import argparse
import http.client
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

from sentinel.alerts.digest import build_member_digest
from sentinel.alerts.preferences import (
    channel_allowed,
    hold_alert,
    in_quiet_hours,
    member_prefs,
    release_held,
    set_member_prefs,
)
from sentinel.detectors.base import Detection
from sentinel.portal import set_member_password
from tests.conftest import make_account

EMAIL = "alice@example.com"


@pytest.fixture
def member(db):
    set_member_password(db, EMAIL, "a-long-enough-password-1", notify=None)
    return EMAIL


def _prefs(tz="America/Toronto", quiet=False, start="22:00", end="07:00", grid=None):
    from sentinel.alerts.preferences import default_grid

    g = default_grid()
    for sev, chans in (grid or {}).items():
        g[sev].update(chans)
    return {"timezone": tz, "quiet_hours_enabled": quiet, "quiet_start": start, "quiet_end": end, "grid": g}


# --- defaults: nothing changes for a member who never opens the page ---------------

def test_no_preference_means_every_channel_on_and_no_quiet_hours(db, member):
    prefs = member_prefs(db, member)
    assert prefs["quiet_hours_enabled"] is False and prefs["timezone"] is None
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        for ch in ("email", "telegram", "sms"):
            assert channel_allowed(prefs, sev, ch)


def test_a_member_with_no_login_row_reads_as_defaults(db):
    prefs = member_prefs(db, "nobody@example.com")
    assert prefs["quiet_hours_enabled"] is False and channel_allowed(prefs, "LOW", "sms")


# --- quiet hours ---------------------------------------------------------------------

def test_quiet_hours_are_judged_in_the_members_own_timezone():
    prefs = _prefs(quiet=True)
    # 03:00 Toronto (EDT, UTC-4) = 07:00 UTC: inside the window locally.
    assert in_quiet_hours(prefs, datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc))
    # 12:00 Toronto = 16:00 UTC: outside.
    assert not in_quiet_hours(prefs, datetime(2026, 7, 1, 16, 0, tzinfo=timezone.utc))


def test_the_default_window_is_overnight_and_both_edges_are_right():
    prefs = _prefs(tz="UTC", quiet=True)
    assert in_quiet_hours(prefs, datetime(2026, 7, 1, 22, 0, tzinfo=timezone.utc))
    assert in_quiet_hours(prefs, datetime(2026, 7, 1, 23, 59, tzinfo=timezone.utc))
    assert in_quiet_hours(prefs, datetime(2026, 7, 2, 6, 59, tzinfo=timezone.utc))
    assert not in_quiet_hours(prefs, datetime(2026, 7, 2, 7, 0, tzinfo=timezone.utc))
    assert not in_quiet_hours(prefs, datetime(2026, 7, 1, 21, 59, tzinfo=timezone.utc))


def test_a_daytime_window_works_too():
    prefs = _prefs(tz="UTC", quiet=True, start="09:00", end="17:00")
    assert in_quiet_hours(prefs, datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc))
    assert not in_quiet_hours(prefs, datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc))


def test_the_window_edges_are_exact_on_a_daytime_window():
    """Start is inside, end is outside - a minute either way is the
    difference between a held alert and a delivered one."""
    prefs = _prefs(tz="UTC", quiet=True, start="09:00", end="17:00")
    assert in_quiet_hours(prefs, datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc))
    assert not in_quiet_hours(prefs, datetime(2026, 7, 1, 8, 59, tzinfo=timezone.utc))
    assert in_quiet_hours(prefs, datetime(2026, 7, 1, 16, 59, tzinfo=timezone.utc))
    assert not in_quiet_hours(prefs, datetime(2026, 7, 1, 17, 0, tzinfo=timezone.utc))


def test_equal_start_and_end_hold_nothing():
    prefs = _prefs(tz="UTC", quiet=True, start="09:00", end="09:00")
    for hour in (0, 9, 12, 23):
        assert not in_quiet_hours(prefs, datetime(2026, 7, 1, hour, 0, tzinfo=timezone.utc))


def test_an_impossible_time_is_refused_even_when_half_of_it_is_valid(db, member):
    for bad in ("25:00", "10:75", "24:00"):
        with pytest.raises(ValueError, match="times like"):
            set_member_prefs(db, member, timezone_name="UTC", quiet_hours_enabled=False,
                             quiet_start=bad, quiet_end="07:00", grid={})


def test_known_timezone_handles_nothing_and_blank():
    from sentinel.alerts.preferences import known_timezone

    assert known_timezone(None) is False
    assert known_timezone("") is False
    assert known_timezone("   ") is False
    assert known_timezone("UTC") is True


def test_off_is_off_whatever_the_clock_says():
    assert not in_quiet_hours(_prefs(quiet=False), datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc))


def test_an_unknown_timezone_cannot_enable_quiet_hours(db, member):
    """FAIL CLOSED: never defaulted to UTC."""
    with pytest.raises(ValueError, match="timezone"):
        set_member_prefs(db, member, timezone_name=None, quiet_hours_enabled=True,
                         quiet_start="22:00", quiet_end="07:00", grid={})
    with pytest.raises(ValueError, match="unknown timezone"):
        set_member_prefs(db, member, timezone_name="Mars/Olympus", quiet_hours_enabled=True,
                         quiet_start="22:00", quiet_end="07:00", grid={})
    assert member_prefs(db, member)["quiet_hours_enabled"] is False


def test_a_stored_timezone_the_runtime_no_longer_knows_delivers_rather_than_holding():
    prefs = _prefs(tz="Atlantis/Lost", quiet=True)
    assert not in_quiet_hours(prefs, datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc))


# --- the floor ------------------------------------------------------------------------

def test_critical_email_cannot_be_switched_off(db, member):
    with pytest.raises(ValueError, match="cannot be switched off"):
        set_member_prefs(db, member, timezone_name=None, quiet_hours_enabled=False,
                         quiet_start="22:00", quiet_end="07:00",
                         grid={"CRITICAL": {"email": False}})
    prefs = member_prefs(db, member)
    assert channel_allowed(prefs, "CRITICAL", "email")


def test_a_corrupt_stored_grid_reads_as_on_not_off(db, member):
    with db.cursor() as cur:
        cur.execute("UPDATE portal_members SET channel_prefs_json = ? WHERE email = ?", ("{not json", member))
    prefs = member_prefs(db, member)
    assert all(channel_allowed(prefs, s, c) for s in ("HIGH", "LOW") for c in ("email", "sms", "telegram"))


def test_every_other_cell_is_the_members_choice(db, member):
    assert set_member_prefs(db, member, timezone_name="Europe/London", quiet_hours_enabled=False,
                            quiet_start="22:00", quiet_end="07:00",
                            grid={"HIGH": {"sms": False}, "LOW": {"email": False, "telegram": False}})
    prefs = member_prefs(db, member)
    assert not channel_allowed(prefs, "HIGH", "sms") and channel_allowed(prefs, "HIGH", "email")
    assert not channel_allowed(prefs, "LOW", "email") and channel_allowed(prefs, "LOW", "sms")
    # Saving the same thing again is not a change.
    assert not set_member_prefs(db, member, timezone_name="Europe/London", quiet_hours_enabled=False,
                                quiet_start="22:00", quiet_end="07:00",
                                grid={"HIGH": {"sms": False}, "LOW": {"email": False, "telegram": False}})


# --- the router honours it; held alerts reach the digest -------------------------------

class _RecEmail:
    instances: list = []

    def __init__(self, host, port, user, pw, sender, recipients, *, audience="operator"):
        self.recipients = recipients
        _RecEmail.instances.append(self)

    def send(self, alert):
        self.sent = alert


class _RecSms:
    instances: list = []

    def __init__(self, recipients, audience="customer"):
        self.recipients = recipients
        _RecSms.instances.append(self)

    def send(self, alert):
        return [{"to": r, "provider": "test", "provider_message_id": "1", "status": "sent"} for r in self.recipients]


def _router(db, monkeypatch):
    _RecEmail.instances = []
    _RecSms.instances = []
    monkeypatch.setattr("sentinel.alerts.contact.EmailChannel", _RecEmail)
    monkeypatch.setattr("sentinel.alerts.contact.build_sms_channel",
                        lambda recipients, audience="customer": _RecSms(recipients) if recipients else None)
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_SENDER", "alerts@test")
    from sentinel.alerts.contact import ContactRouter

    return ContactRouter(db=db)


def _account_with_phone(db):
    account_id = make_account(db)
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = ?, alert_phone = ? WHERE id = ?",
                    (EMAIL, "+15550001111", account_id))
    return account_id


def _stored(db, account_id, severity="HIGH"):
    from sentinel.alerts.dispatch import dispatch

    det = Detection(rule="withdrawal_burst", severity=severity, confidence="high", message="m",
                    evidence={"order_id": f"o-{severity}"})
    return det, dispatch(db, {}, account_id, "acct-1", det, {"CRITICAL": 15, "HIGH": 30, "MEDIUM": 720, "LOW": 1440})


def test_a_non_critical_in_quiet_hours_is_held_not_sent_and_lands_in_the_digest(db, member, monkeypatch):
    account_id = _account_with_phone(db)
    # A window around the clock, not 00:00-23:59: with the end exclusive,
    # that window left the minute 23:59:00-23:59:59 UTC outside quiet hours,
    # and the suite's first minute landed there on 2026-10-23 (CI started at
    # 23:59). Four hours around now wraps midnight when it must; the wrap is
    # the production case and is tested above.
    now = datetime.now(timezone.utc)
    set_member_prefs(db, member, timezone_name="UTC", quiet_hours_enabled=True,
                     quiet_start=(now - timedelta(hours=2)).strftime("%H:%M"),
                     quiet_end=(now + timedelta(hours=2)).strftime("%H:%M"), grid={})
    router = _router(db, monkeypatch)
    det, alert_id = _stored(db, account_id, "HIGH")
    router.send(det, "acct-1", [EMAIL], ["+15550001111"], account_id=account_id, alert_id=alert_id)
    assert _RecEmail.instances == [] and _RecSms.instances == [], "held, not delivered"
    body, _ = build_member_digest(db, member)
    assert "Held during your quiet hours: 1 alert(s)" in body
    assert "high alert at" in body
    assert "withdrawal_burst" not in body, "customer copy: no rule names"
    # Released once: the next digest does not repeat it.
    body2, _ = build_member_digest(db, member)
    assert "Held during your quiet hours" not in body2


def test_a_critical_is_never_held(db, member, monkeypatch):
    account_id = _account_with_phone(db)
    set_member_prefs(db, member, timezone_name="UTC", quiet_hours_enabled=True,
                     quiet_start="00:00", quiet_end="23:59", grid={})
    router = _router(db, monkeypatch)
    det, alert_id = _stored(db, account_id, "CRITICAL")
    router.send(det, "acct-1", [EMAIL], ["+15550001111"], account_id=account_id, alert_id=alert_id)
    assert len(_RecEmail.instances) == 1 and _RecEmail.instances[0].recipients == [EMAIL]
    assert release_held(db, member) == []


def test_the_grid_turns_a_channel_off_for_a_severity_and_leaves_the_others(db, member, monkeypatch):
    account_id = _account_with_phone(db)
    set_member_prefs(db, member, timezone_name=None, quiet_hours_enabled=False,
                     quiet_start="22:00", quiet_end="07:00", grid={"HIGH": {"sms": False, "email": True}})
    router = _router(db, monkeypatch)
    det, alert_id = _stored(db, account_id, "HIGH")
    router.send(det, "acct-1", [EMAIL], ["+15550001111"], account_id=account_id, alert_id=alert_id)
    assert len(_RecEmail.instances) == 1 and _RecSms.instances == []


def test_email_off_for_a_severity_still_lets_sms_through(db, member, monkeypatch):
    account_id = _account_with_phone(db)
    set_member_prefs(db, member, timezone_name=None, quiet_hours_enabled=False,
                     quiet_start="22:00", quiet_end="07:00", grid={"HIGH": {"email": False}})
    router = _router(db, monkeypatch)
    det, alert_id = _stored(db, account_id, "HIGH")
    router.send(det, "acct-1", [EMAIL], ["+15550001111"], account_id=account_id, alert_id=alert_id)
    assert _RecEmail.instances == [] and len(_RecSms.instances) == 1


def test_a_notice_and_an_escalation_bypass_preferences(db, member, monkeypatch):
    from sentinel.alerts.escalation import _EscalationDetection

    account_id = _account_with_phone(db)
    set_member_prefs(db, member, timezone_name="UTC", quiet_hours_enabled=True,
                     quiet_start="00:00", quiet_end="23:59", grid={"HIGH": {"email": False}})
    router = _router(db, monkeypatch)
    from sentinel.alerts.base import AlertMessage

    esc = _EscalationDetection(AlertMessage(account_label="acct-1", rule="ESCALATION withdrawal_burst",
                                            severity="HIGH", confidence="high", message="m", evidence={}))
    router.send(esc, "acct-1", [EMAIL], [], account_id=account_id, alert_id=None)
    assert len(_RecEmail.instances) == 1, "an escalation must arrive whatever the grid says"


def test_a_member_who_set_nothing_gets_exactly_todays_delivery(db, member, monkeypatch):
    account_id = _account_with_phone(db)
    router = _router(db, monkeypatch)
    det, alert_id = _stored(db, account_id, "LOW")
    router.send(det, "acct-1", [EMAIL], ["+15550001111"], account_id=account_id, alert_id=alert_id)
    assert len(_RecEmail.instances) == 1 and len(_RecSms.instances) == 1


def test_holding_is_idempotent_per_member_and_alert(db, member):
    account_id = make_account(db)
    _, alert_id = _stored(db, account_id)
    assert hold_alert(db, member, alert_id) is True
    assert hold_alert(db, member, alert_id) is False


# --- the email floor at both entry points -------------------------------------------

def test_set_contact_refuses_to_clear_the_primary_email(db, capsys):
    from sentinel.cli import cmd_set_contact

    account_id = make_account(db)
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = ?, label = 'floor' WHERE id = ?", (EMAIL, account_id))
    db.close()
    os.environ["SENTINEL_DB_PATH"] = str(db._path)
    ns = argparse.Namespace(label="floor", alert_email=None, alert_phone=None, clear=True, as_operator=None)
    assert cmd_set_contact(ns) == 2
    assert "cannot be removed" in capsys.readouterr().err


def test_onboarding_from_the_cli_requires_an_email(db, capsys):
    from sentinel.cli import cmd_onboard

    ns = argparse.Namespace(exchange="bitget", label="x", topology="independent", api_key="k", secret="s",
                            passphrase=None, alert_email=None, alert_phone=None, as_operator=None)
    assert cmd_onboard(ns) == 2
    assert "--alert-email is required" in capsys.readouterr().err


def test_the_dashboard_add_form_requires_an_email(db, cipher):
    from sentinel.dashboard import serve_dashboard
    from tests.test_member_account_grants import _sign_in_ops

    srv = serve_dashboard(db, cipher, 0)
    port = srv.server_address[1]
    try:
        cookie = _sign_in_ops(db, cipher, port)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/add", urllib.parse.urlencode({"name": "n", "api_key": "k", "secret": "s"}),
                     {"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie})
        resp = conn.getresponse()
        body = resp.read().decode()
    finally:
        srv.shutdown()
        srv.server_close()
    assert resp.status == 400 and "contact email is required" in body


# --- the portal page -------------------------------------------------------------------

def test_the_security_page_offers_preferences_with_the_floor_locked(db, member):
    from sentinel.portal import _render_security

    page = _render_security(db, member)
    assert "Alert preferences" in page and 'action="/preferences"' in page
    assert 'name="grid_CRITICAL_email" value="on"' in page and "disabled" in page
    assert "America/Toronto" in page


def test_a_blank_stored_quiet_window_reads_as_the_default(db, member):
    """A row that somehow holds '' for a window edge must read as the
    default, not as an empty time that later fails to parse."""
    with db.cursor() as cur:
        cur.execute("UPDATE portal_members SET quiet_start = '', quiet_end = '' WHERE email = ?", (member,))
    prefs = member_prefs(db, member)
    assert prefs["quiet_start"] == "22:00" and prefs["quiet_end"] == "07:00"
