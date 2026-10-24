"""Snoozing from the portal (G-26, #30).

`tests/test_snooze.py` covers what a snooze *does*. This covers who is allowed
to ask for one, which is the same boundary as acknowledgement and for a sharper
reason: acknowledging stops escalation, snoozing stops the alert itself. A
snooze reachable across the isolation boundary is a way to mute a stranger's
account.

The duration is the other half. It arrives from a form, so it arrives as text a
customer can retype — the tests below point it at values no dropdown offers.
"""
from __future__ import annotations

import http.client
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

from sentinel.portal import serve_portal, set_member_password
from sentinel.snooze import MAX_SNOOZE_HOURS, active_snooze
from tests.helpers.landing_redirect import assert_sent_to_the_landing_page


def _free_port():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def portal(db):
    port = _free_port()
    srv = serve_portal(db, port)
    yield "127.0.0.1", port
    srv.shutdown()


def _member_account(db, label, email):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', '2026-01-01', '2026-01-01')",
            (label, email),
        )
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
        return account_id


def _alert(db, account_id, dedup_key="key-1"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at) VALUES (?, 'withdrawal_initiated', 'CRITICAL', ?, 'msg', '{}', "
            "'high', ?)",
            (account_id, dedup_key, datetime.now(timezone.utc).isoformat()),
        )
        return cur.lastrowid


def _request(host_port, method, path, body=None, cookie=None, origin=None):
    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {}
    payload = None
    if body is not None:
        payload = urllib.parse.urlencode(body)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if cookie:
        headers["Cookie"] = f"minas_portal={cookie}"
    if origin:
        headers["Origin"] = origin
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode()
    conn.close()
    return resp.status, data


def _login(host_port, email, password):
    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("POST", "/login",
                 body=urllib.parse.urlencode({"email": email, "password": password}),
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    resp.read()
    cookie = resp.getheader("Set-Cookie") or ""
    conn.close()
    return cookie.split("minas_portal=", 1)[1].split(";", 1)[0] if "minas_portal=" in cookie else ""


@pytest.fixture
def alice(db, portal):
    account_id = _member_account(db, "alice-1", "alice@example.com")
    set_member_password(db, "alice@example.com", "pw-alice")
    return account_id, _login(portal, "alice@example.com", "pw-alice")


# --- who may silence what -----------------------------------------------------


def test_a_member_cannot_snooze_another_members_alert(portal, db, alice):
    """Worse than the acknowledgement equivalent: acknowledging suppresses the
    escalation of an alert already delivered, snoozing suppresses the delivery.
    Across the boundary it is a mute button on a stranger's account."""
    _, token = alice
    bob_id = _member_account(db, "bob-1", "bob@example.com")
    bobs_alert = _alert(db, bob_id, dedup_key="bob-key")

    status, _ = _request(portal, "POST", "/snooze",
                         body={"alert_id": str(bobs_alert), "hours": "4"}, cookie=token)

    assert status == 404
    assert active_snooze(db, bob_id, "bob-key") is None


def test_an_unauthenticated_post_snoozes_nothing(portal, db, alice):
    account_id, _ = alice
    alert_id = _alert(db, account_id)

    assert_sent_to_the_landing_page(portal, "POST", "/snooze", body={"alert_id": str(alert_id), "hours": "4"})

    assert active_snooze(db, account_id, "key-1") is None


def test_a_cross_origin_post_snoozes_nothing(portal, db, alice):
    account_id, token = alice
    alert_id = _alert(db, account_id)

    status, _ = _request(portal, "POST", "/snooze", body={"alert_id": str(alert_id), "hours": "4"},
                         cookie=token, origin="https://evil.example")

    assert status == 403
    assert active_snooze(db, account_id, "key-1") is None


# --- the duration arrives from a form, so it arrives as text ------------------


@pytest.mark.parametrize("hours", ["99999", "0", "-4", "abc", ""])
def test_a_duration_no_dropdown_offers_is_clamped_or_refused(portal, db, alice, hours):
    """The cap and the floor are enforced where the row is written, not in the
    markup. A form field is a POST body, and a POST body is whatever the
    sender types."""
    account_id, token = alice
    alert_id = _alert(db, account_id)
    before = datetime.now(timezone.utc)

    _request(portal, "POST", "/snooze",
             body={"alert_id": str(alert_id), "hours": hours}, cookie=token)

    expires = active_snooze(db, account_id, "key-1")
    if expires is not None:  # clamped, never unbounded
        assert datetime.fromisoformat(expires) <= (
            before + timedelta(hours=MAX_SNOOZE_HOURS) + timedelta(seconds=5))


def test_snoozing_records_the_member_who_asked(portal, db, alice):
    account_id, token = alice
    alert_id = _alert(db, account_id)

    status, _ = _request(portal, "POST", "/snooze",
                         body={"alert_id": str(alert_id), "hours": "4"}, cookie=token)

    assert status == 303
    with db.cursor() as cur:
        cur.execute("SELECT requested_by, rule FROM snoozes WHERE account_id = ?", (account_id,))
        row = cur.fetchone()
    assert row["requested_by"] == "alice@example.com"
    assert row["rule"] == "withdrawal_initiated"


# --- snoozed alerts stay visible ----------------------------------------------


def test_a_snoozed_alert_is_still_listed_and_says_so(portal, db, alice):
    """The criterion, and the reason a snooze is not a delete: the customer
    silenced their phone, and the account's history still has to show what
    happened while it was quiet."""
    account_id, token = alice
    alert_id = _alert(db, account_id)

    _, before = _request(portal, "GET", f"/account?id={account_id}", cookie=token)
    assert "Snooze" in before

    _request(portal, "POST", "/snooze",
             body={"alert_id": str(alert_id), "hours": "4"}, cookie=token)

    _, after = _request(portal, "GET", f"/account?id={account_id}", cookie=token)
    assert "withdrawal_initiated" in after      # still listed
    assert "Snoozed" in after                   # and labelled as silenced
