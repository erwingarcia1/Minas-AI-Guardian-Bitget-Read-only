"""Acknowledging from the app, and signing a phone out from the browser
(decisions 21c, 21d).

Acknowledging is the app's one account-facing write and it stops a
CRITICAL's escalation, so it runs through the portal's own scoped writer:
any contact on the account may do it (#25), nobody else can, and the first
acknowledgement is the one that stands.
"""
from __future__ import annotations

import http.client
import threading
import time
import urllib.parse

import pytest

from sentinel import api as api_module
from sentinel import outbox
from sentinel.portal import _session_secret, make_session_token, serve_portal
from tests.conftest import PINNED_TOTP_NOW
from tests.helpers.api_client import ApiClient, add_account, add_alert, enrolled_member

ALICE = "alice@example.com"
BOB = "bob@example.com"
KIN = "kin@example.com"


@pytest.fixture
def server(db, cipher, monkeypatch):
    monkeypatch.setattr(api_module, "send_new_device_email", lambda db, address, name: None)
    srv = serve_portal(db, 0, host="127.0.0.1", cipher=cipher)
    yield ApiClient(srv.server_address[1])
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def fleet(db):
    a1 = add_account(db, ALICE, "alice-1", secondary=KIN)
    b1 = add_account(db, BOB, "bob-1")
    return {"a1": a1, "b1": b1, "alice_alert": add_alert(db, a1), "bob_alert": add_alert(db, b1)}


def _token(server, db, cipher, email, device_name="phone"):
    secret = enrolled_member(db, cipher, email)
    status, tokens, _ = server.sign_in(email, secret, at=PINNED_TOTP_NOW, device_name=device_name)
    assert status == 200, tokens
    return tokens


def _ack_state(db, alert_id):
    with db.cursor() as cur:
        cur.execute("SELECT acknowledged_at, acknowledged_by FROM alerts WHERE id = ?", (alert_id,))
        return tuple(cur.fetchone())


# --- acknowledging -------------------------------------------------------------------

def test_the_primary_acknowledges_and_the_first_acknowledgement_stands(server, db, cipher, fleet):
    alice = _token(server, db, cipher, ALICE)["access_token"]
    path = f"/api/v1/alerts/{fleet['alice_alert']}/acknowledge"
    status, body, _ = server.request("POST", path, token=alice)
    assert status == 200 and body["newly_acknowledged"] is True
    first = _ack_state(db, fleet["alice_alert"])
    assert first[1] == ALICE and body["acknowledged_at"] == first[0]

    status, again, _ = server.request("POST", path, token=alice)
    assert status == 200 and again["newly_acknowledged"] is False
    assert _ack_state(db, fleet["alice_alert"]) == first


def test_the_emergency_contact_may_acknowledge(server, db, cipher, fleet):
    """#25: a non-primary may read and may acknowledge. Nothing else."""
    kin = _token(server, db, cipher, KIN)["access_token"]
    status, body, _ = server.request("POST", f"/api/v1/alerts/{fleet['alice_alert']}/acknowledge", token=kin)
    assert status == 200 and body["acknowledged_by"] == KIN


def test_someone_elses_alert_cannot_be_acknowledged_or_detected(server, db, cipher, fleet):
    alice = _token(server, db, cipher, ALICE)["access_token"]
    theirs = server.request("POST", f"/api/v1/alerts/{fleet['bob_alert']}/acknowledge", token=alice)
    nothing = server.request("POST", "/api/v1/alerts/999999/acknowledge", token=alice)
    assert theirs[0] == nothing[0] == 404 and theirs[1] == nothing[1]
    assert _ack_state(db, fleet["bob_alert"]) == (None, None)


def test_acknowledging_reads_like_the_web_on_the_incident(server, db, cipher, fleet):
    alice = _token(server, db, cipher, ALICE)["access_token"]
    server.request("POST", f"/api/v1/alerts/{fleet['alice_alert']}/acknowledge", token=alice)
    _, incident, _ = server.request("GET", f"/api/v1/alerts/{fleet['alice_alert']}", token=alice)
    assert incident["acknowledged_by"] == ALICE and incident["acknowledged_at"]


def test_the_acknowledgement_answers_before_its_closing_notes_are_sent(server, db, cipher, fleet, monkeypatch):
    """Answer first, deliver after (2026-10-23). The emergency contact's
    closing note (decision 1) went out by email and SMS before the answer,
    one per acknowledged CRITICAL, so a slow provider held an
    acknowledgement that had already stopped the escalation past the app's
    deadline, and the app said "Not acknowledged". The answer now goes
    first; the notes follow it - this alert's, then its sibling's (#99),
    in the order they were owed."""
    withdrawal = '{"order_id": "424427676"}'
    first = add_alert(db, fleet["a1"], evidence=withdrawal, sent_at="2026-08-28T01:00:00+00:00")
    sibling = add_alert(db, fleet["a1"], rule="withdrawal_to_unseen_address", evidence=withdrawal,
                        sent_at="2026-08-28T01:00:07+00:00")
    alice = _token(server, db, cipher, ALICE)["access_token"]
    entered, release, notes = threading.Event(), threading.Event(), []

    def stalled(router, detection, account_label, emails, phones, **kwargs):
        notes.append((detection.evidence["alert_id"], list(emails)))
        entered.set()
        release.wait(10)  # a provider that never answers; bounded, so the test always ends

    monkeypatch.setattr("sentinel.alerts.contact.ContactRouter.send", stalled)
    try:
        started = time.monotonic()
        status, body, _ = server.request("POST", f"/api/v1/alerts/{first}/acknowledge", token=alice)
        elapsed = time.monotonic() - started
        assert entered.wait(5), "the closing note was never sent"
        # The first note is stuck in its provider: only the deliveries wait.
        assert _ack_state(db, sibling)[1] == ALICE, "the sibling is acknowledged with the alert, not after its note"
    finally:
        release.set()
    assert status == 200 and body["newly_acknowledged"] is True
    assert elapsed < 2, f"the acknowledgement answered after {elapsed:.1f} s: it waited for its notes"
    assert outbox.wait_until_sent(5)
    assert notes == [(first, [KIN]), (sibling, [KIN])]


# --- signing a phone out from the browser --------------------------------------------

def _cookie(db, email):
    return "minas_portal=" + make_session_token(_session_secret(db), email, epoch=1)


def _web(server, method, path, cookie=None, form=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if cookie:
        headers["Cookie"] = cookie
    conn.request(method, path, body=urllib.parse.urlencode(form or {}) if method == "POST" else None,
                 headers=headers)
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, body


def test_the_security_page_lists_every_signed_in_phone(server, db, cipher, fleet):
    _token(server, db, cipher, ALICE, device_name="Alice's <iPhone>")
    status, page = _web(server, "GET", "/security", _cookie(db, ALICE))
    assert status == 200
    assert "Phones signed in to the app" in page
    assert "Alice&#x27;s &lt;iPhone&gt;" in page  # escaped, never raw markup
    assert 'action="/security/devices/revoke"' in page


def test_a_login_with_no_phone_has_no_phone_section(server, db, cipher, fleet):
    enrolled_member(db, cipher, ALICE)
    _, page = _web(server, "GET", "/security", _cookie(db, ALICE))
    assert "Phones signed in to the app" not in page


def test_signing_a_phone_out_from_the_browser_ends_it(server, db, cipher, fleet):
    tokens = _token(server, db, cipher, ALICE)
    status, page = _web(server, "POST", "/security/devices/revoke", _cookie(db, ALICE),
                        {"device_id": tokens["device_id"]})
    assert status == 200 and "That phone is signed out of the app." in page
    assert server.request("GET", "/api/v1/security", token=tokens["access_token"])[0] == 401
    with db.cursor() as cur:
        cur.execute("SELECT revoked_reason FROM api_devices WHERE id = ?", (tokens["device_id"],))
        assert cur.fetchone()["revoked_reason"] == "revoked"


def test_another_logins_phone_is_not_found_and_stays_signed_in(server, db, cipher, fleet):
    enrolled_member(db, cipher, ALICE)
    bob = _token(server, db, cipher, BOB)
    for device_id in (bob["device_id"], 999999, "abc", ""):
        status, _ = _web(server, "POST", "/security/devices/revoke", _cookie(db, ALICE), {"device_id": device_id})
        assert status == 404, device_id
    assert server.request("GET", "/api/v1/security", token=bob["access_token"])[0] == 200


def test_signed_out_of_the_browser_the_form_signs_nothing_out(server, db, cipher, fleet):
    tokens = _token(server, db, cipher, ALICE)
    status, page = _web(server, "POST", "/security/devices/revoke", None, {"device_id": tokens["device_id"]})
    assert status == 200 and "Sign in" in page
    assert server.request("GET", "/api/v1/security", token=tokens["access_token"])[0] == 200
