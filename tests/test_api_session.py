"""Signing in from the app, and every way that sign-in ends (decision 21c).

The app signs in with the web's credentials - email, password and the
member's TOTP - and must have two-factor on. It holds a 15-minute access token
and a refresh token that is replaced on every use. A password change, a
revocation or a replayed refresh token ends the sign-in on the next request.
"""
from __future__ import annotations

import logging
import threading
import time

import pytest

from sentinel import api as api_module
from sentinel import outbox
from sentinel.api import (
    ACCESS_TTL_SECONDS,
    REFRESH_TTL_SECONDS,
    access_key,
    device_name,
    make_access_token,
)
from sentinel.portal import (
    _session_secret,
    make_session_token,
    serve_portal,
    set_member_password,
)
from sentinel.totp import STEP_SECONDS
from tests.conftest import PINNED_TOTP_NOW
from tests.helpers.api_client import PASSWORD, ApiClient, add_account, enrolled_member

ALICE = "alice@example.com"
BOB = "bob@example.com"
# The real notice, before any fixture stands a recorder in for it.
REAL_NOTICE = api_module.send_new_device_email


def _errors(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.fixture
def notices(monkeypatch):
    sent = []
    monkeypatch.setattr(api_module, "send_new_device_email",
                        lambda db, address, name: sent.append((address, name)))
    return sent


@pytest.fixture
def server(db, cipher, notices):
    srv = serve_portal(db, 0, host="127.0.0.1", cipher=cipher)
    yield ApiClient(srv.server_address[1])
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def alice(db, cipher):
    add_account(db, ALICE, "alice-1")
    return enrolled_member(db, cipher, ALICE)


def _devices(db, email=ALICE):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM api_devices WHERE email = ? ORDER BY id", (email,))
        return cur.fetchall()


def _move_api_clock(monkeypatch, seconds: float):
    base = time.time()
    monkeypatch.setattr(api_module, "_clock", lambda: base + seconds)


# --- signing in ------------------------------------------------------------------

def test_password_and_code_issue_both_tokens_and_record_the_device(server, db, alice, notices):
    status, body, _ = server.sign_in(ALICE, alice, device_name="Alice's iPhone")
    assert status == 200, body
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == ACCESS_TTL_SECONDS
    assert body["refresh_expires_in"] == REFRESH_TTL_SECONDS
    [device] = _devices(db)
    assert body["device_id"] == device["id"]
    assert device["name"] == "Alice's iPhone"
    assert device["revoked_at"] is None
    # The refresh token is held only as its hash.
    assert body["refresh_token"] not in (device["refresh_hash"], device["previous_refresh_hash"])
    status, security, _ = server.request("GET", "/api/v1/security", token=body["access_token"])
    assert status == 200 and security["two_factor_enabled"] is True


def test_every_sign_in_tells_the_member_and_lands_on_their_activity_page(server, db, alice, notices):
    status, _, _ = server.sign_in(ALICE, alice, device_name="Alice's iPhone")
    assert status == 200
    assert outbox.wait_until_sent(5), "the notice is sent after the answer, and within seconds of it"
    assert notices == [(ALICE, "Alice's iPhone")]
    with db.cursor() as cur:
        cur.execute("SELECT payload_json, actor FROM events WHERE event_type = 'member_app_signed_in'")
        rows = cur.fetchall()
    assert len(rows) == 1 and "Alice's iPhone" in rows[0]["payload_json"]
    assert rows[0]["actor"] == ALICE


def test_a_failed_notice_does_not_undo_the_sign_in(server, alice, monkeypatch, caplog):
    def boom(db, address, name):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(api_module, "send_new_device_email", boom)
    status, body, _ = server.sign_in(ALICE, alice)
    assert status == 200 and body["access_token"]
    assert outbox.wait_until_sent(5)
    assert _errors(caplog) == [f"new-device notice to {ALICE} failed: smtp down"]


def test_the_sign_in_answers_before_its_notice_is_sent(server, db, alice, monkeypatch):
    """Answer first, deliver after (2026-10-23). smtplib's 15 seconds apply to
    each socket operation, so a slow mail provider held this answer past
    the app's 15-second deadline - after the device was created and the
    member's code spent - and the app said it could not reach Minas. The
    answer now goes first, and the notice still follows it."""
    entered, release, sent = threading.Event(), threading.Event(), []

    def stalled(router, detection, account_label, emails, phones, **kwargs):
        sent.append((list(emails), detection.message))
        entered.set()
        release.wait(10)  # a provider that never answers; bounded, so the test always ends

    monkeypatch.setattr(api_module, "send_new_device_email", REAL_NOTICE)
    monkeypatch.setattr("sentinel.alerts.contact.ContactRouter.send", stalled)
    try:
        started = time.monotonic()
        status, body, _ = server.sign_in(ALICE, alice, device_name="Alice's iPhone")
        elapsed = time.monotonic() - started
        assert entered.wait(5), "the notice was never sent"
    finally:
        release.set()
    assert status == 200 and body["access_token"]
    assert elapsed < 2, f"the sign-in answered after {elapsed:.1f} s: it waited for its notice"
    assert outbox.wait_until_sent(5)
    [(emails, message)] = sent
    assert emails == [ALICE] and "Alice's iPhone" in message


def test_a_sign_in_whose_answer_cannot_be_written_still_sends_its_notice(server, alice, notices, monkeypatch):
    """The app gave up, or its network dropped, while the server was still
    answering, so writing the answer raises. The device exists all the same,
    so its notice - decision 21c's tripwire - must still go out: that is
    outbox.answering's `finally`."""
    import http.server

    real_end_headers = http.server.BaseHTTPRequestHandler.end_headers

    def broken_pipe(self):
        if self.command == "POST" and self.path.startswith("/api/v1/session"):
            raise BrokenPipeError(32, "Broken pipe")
        return real_end_headers(self)

    monkeypatch.setattr(http.server.BaseHTTPRequestHandler, "end_headers", broken_pipe)
    # The client sees the connection drop (an OSError: reset, or closed
    # without an answer).
    with pytest.raises(OSError):
        server.sign_in(ALICE, alice, device_name="Alice's iPhone")
    assert outbox.wait_until_sent(5), "what the sign-in queued never finished"
    assert notices == [(ALICE, "Alice's iPhone")]


def test_with_no_thread_to_be_had_the_notice_is_still_sent(server, alice, notices, monkeypatch):
    """docker-compose.yml's pids_limit means a thread can be refused. The
    answer has gone by then, so the notice is sent on the request's own
    thread rather than lost."""
    import types

    class NoThread(threading.Thread):
        def start(self):
            raise RuntimeError("can't start new thread")

    # Only the outbox's own view of `threading`: the server keeps its threads.
    monkeypatch.setattr(outbox, "threading", types.SimpleNamespace(Thread=NoThread))
    status, _body, _ = server.sign_in(ALICE, alice, device_name="Alice's iPhone")
    assert status == 200
    assert outbox.wait_until_sent(5), "what the sign-in queued never finished"
    assert notices == [(ALICE, "Alice's iPhone")]


def test_one_delivery_that_raises_does_not_cost_the_next(db):
    """Each delivery guards itself today; the queue guards each one again, so
    the next that forgets cannot cost the sibling closing notes behind it."""
    ran = []

    def raises():
        raise RuntimeError("unguarded")

    with outbox.answering(db):
        outbox.later(raises)
        outbox.later(lambda: ran.append("second"))
    assert outbox.wait_until_sent(5)
    assert ran == ["second"]


def test_a_wrong_password_and_a_wrong_code_get_the_same_answer(server, db, alice):
    bad_password = server.sign_in(ALICE, alice, password="not-the-password")
    bad_code = server.request("POST", "/api/v1/session",
                              {"email": ALICE, "password": PASSWORD, "code": "000000"})
    assert bad_password[0] == bad_code[0] == 401
    assert bad_password[1] == bad_code[1]
    assert bad_password[1]["error"] == "invalid_credentials"
    assert _devices(db) == []


def test_two_factor_off_is_refused_and_only_after_the_right_password(server, db):
    add_account(db, BOB, "bob-1")
    set_member_password(db, BOB, PASSWORD, notify=None)
    wrong = server.request("POST", "/api/v1/session", {"email": BOB, "password": "nope-nope-nope"})
    right = server.request("POST", "/api/v1/session", {"email": BOB, "password": PASSWORD})
    assert wrong[0] == 401 and wrong[1]["error"] == "invalid_credentials"
    assert right[0] == 403 and right[1]["error"] == "two_factor_required"
    assert _devices(db, BOB) == []


def test_an_unknown_address_reads_exactly_like_a_wrong_password(server, alice):
    unknown = server.request("POST", "/api/v1/session",
                             {"email": "nobody@example.com", "password": PASSWORD, "code": "123456"})
    wrong = server.sign_in(ALICE, alice, password="not-the-password")
    assert unknown[:2] == wrong[:2]


def test_failures_count_on_the_portals_own_throttle(server, db, alice):
    """One limiter for the web and the app: five wrong passwords through the
    API and the web sign-in is throttled too, for the same address and
    source."""
    import http.client
    import urllib.parse

    for _ in range(5):
        assert server.sign_in(ALICE, alice, password="not-the-password")[0] == 401
    status, body, _ = server.sign_in(ALICE, alice)
    assert status == 429 and body["error"] == "too_many_attempts"
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request("POST", "/login", body=urllib.parse.urlencode({"email": ALICE, "password": PASSWORD}),
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    assert resp.status == 429
    conn.close()


def test_a_body_that_is_not_a_json_object_is_refused(server, alice):
    for raw in (b"not json", b"[1, 2]", b"\xff\xfe"):
        status, body, _ = server.request("POST", "/api/v1/session", raw)
        assert status == 400 and body["error"] == "invalid_json"


@pytest.mark.parametrize("path, body", [
    ("/api/v1/session/refresh", {"refresh_token": "\ud800"}),
    ("/api/v1/session", {"email": "\ud800@example.com", "password": PASSWORD, "code": "123456"}),
    ("/api/v1/session", {"email": ALICE, "password": PASSWORD, "code": "\ud800"}),
], ids=["refresh token", "sign-in email", "sign-in code"])
def test_text_that_unicode_cannot_hold_is_a_malformed_body_not_a_failure_of_ours(server, alice, caplog, path, body):
    """JSON allows a lone surrogate; Unicode does not. Parsed and passed on,
    it failed deep in a handler - the token's hash, the address's database
    bind, the code check behind the right password - and was answered as
    Minas's own 500, with a traceback, to anyone, with no account."""
    status, answer, _ = server.request("POST", path, body)
    assert status == 400 and answer["error"] == "invalid_json"
    assert _errors(caplog) == []


def test_a_code_in_another_scripts_digits_is_a_wrong_code_and_is_counted(server, alice, caplog):
    """'١٢٣٤٥٦' is six digits to `str.isdigit` and not to compare_digest,
    which raised: behind the right password that was Minas's own 500, and
    the failed sign-in was never counted on the throttle."""
    for _ in range(5):
        status, body, _ = server.request("POST", "/api/v1/session",
                                         {"email": ALICE, "password": PASSWORD, "code": "١٢٣٤٥٦"})
        assert status == 401 and body["error"] == "invalid_credentials"
    status, body, _ = server.sign_in(ALICE, alice)
    assert status == 429 and body["error"] == "too_many_attempts", "each one counted on the login throttle"
    assert _errors(caplog) == []


# --- the access token -------------------------------------------------------------

def test_the_access_token_dies_after_fifteen_minutes(server, alice, monkeypatch):
    _, tokens, _ = server.sign_in(ALICE, alice)
    _move_api_clock(monkeypatch, ACCESS_TTL_SECONDS - 5)
    assert server.request("GET", "/api/v1/security", token=tokens["access_token"])[0] == 200
    _move_api_clock(monkeypatch, ACCESS_TTL_SECONDS + 5)
    assert server.request("GET", "/api/v1/security", token=tokens["access_token"])[0] == 401


def test_a_portal_cookie_is_not_an_access_token_and_the_reverse(server, db, alice):
    secret = _session_secret(db)
    cookie = make_session_token(secret, ALICE, epoch=1)
    assert server.request("GET", "/api/v1/security", token=cookie)[0] == 401
    _, tokens, _ = server.sign_in(ALICE, alice)
    status, body, _ = server.request("GET", "/", headers={"Cookie": f"minas_portal={tokens['access_token']}"})
    assert status == 200 and "Sign in" in body  # the login page, not the member's home


def test_a_token_signed_with_another_key_is_refused(server, db, alice):
    _, tokens, _ = server.sign_in(ALICE, alice)
    forged = make_access_token(access_key(b"not-the-portal-secret"), tokens["device_id"])
    assert server.request("GET", "/api/v1/security", token=forged)[0] == 401


# --- the refresh token ------------------------------------------------------------

def test_a_refresh_replaces_both_tokens(server, db, alice):
    _, first, _ = server.sign_in(ALICE, alice)
    status, second, _ = server.request("POST", "/api/v1/session/refresh",
                                       {"refresh_token": first["refresh_token"]})
    assert status == 200
    assert second["device_id"] == first["device_id"]
    assert second["refresh_token"] != first["refresh_token"]
    assert server.request("GET", "/api/v1/security", token=second["access_token"])[0] == 200
    [device] = _devices(db)
    assert device["last_refreshed_at"] is not None


def test_a_replaced_refresh_token_presented_again_ends_the_sign_in(server, db, alice):
    """Two parties held the device's credential. Whichever is the thief,
    neither keeps the sign-in."""
    _, first, _ = server.sign_in(ALICE, alice)
    _, second, _ = server.request("POST", "/api/v1/session/refresh",
                                  {"refresh_token": first["refresh_token"]})
    status, body, _ = server.request("POST", "/api/v1/session/refresh",
                                     {"refresh_token": first["refresh_token"]})
    assert status == 401 and body["error"] == "invalid_refresh_token"
    [device] = _devices(db)
    assert device["revoked_at"] is not None and device["revoked_reason"] == "refresh_reused"
    assert server.request("POST", "/api/v1/session/refresh",
                          {"refresh_token": second["refresh_token"]})[0] == 401
    assert server.request("GET", "/api/v1/security", token=second["access_token"])[0] == 401


def test_an_unknown_refresh_token_is_refused_and_revokes_nothing(server, db, alice):
    _, tokens, _ = server.sign_in(ALICE, alice)
    for presented in ("made-up", "", None, 12345):
        assert server.request("POST", "/api/v1/session/refresh", {"refresh_token": presented})[0] == 401
    [device] = _devices(db)
    assert device["revoked_at"] is None
    assert server.request("GET", "/api/v1/security", token=tokens["access_token"])[0] == 200


def test_a_refresh_token_unused_for_thirty_days_is_dead(server, alice, monkeypatch):
    _, tokens, _ = server.sign_in(ALICE, alice)
    _move_api_clock(monkeypatch, REFRESH_TTL_SECONDS + 60)
    assert server.request("POST", "/api/v1/session/refresh",
                          {"refresh_token": tokens["refresh_token"]})[0] == 401


def test_using_the_app_keeps_it_signed_in(server, alice, monkeypatch):
    """The window slides: a refresh at day 29 buys another 30 days."""
    _, tokens, _ = server.sign_in(ALICE, alice)
    _move_api_clock(monkeypatch, REFRESH_TTL_SECONDS - 86400)
    _, again, _ = server.request("POST", "/api/v1/session/refresh",
                                 {"refresh_token": tokens["refresh_token"]})
    _move_api_clock(monkeypatch, REFRESH_TTL_SECONDS + 86400)
    assert server.request("POST", "/api/v1/session/refresh",
                          {"refresh_token": again["refresh_token"]})[0] == 200


# --- ending a sign-in ------------------------------------------------------------

def test_a_password_change_signs_every_app_out(server, db, alice):
    """The password-change email says every other signed-in device has been
    signed out. A phone is a signed-in device."""
    _, tokens, _ = server.sign_in(ALICE, alice)
    set_member_password(db, ALICE, "a-brand-new-password", notify=None)
    assert server.request("GET", "/api/v1/security", token=tokens["access_token"])[0] == 401
    assert server.request("POST", "/api/v1/session/refresh",
                          {"refresh_token": tokens["refresh_token"]})[0] == 401


def test_signing_out_ends_this_device_and_no_other(server, db, alice):
    _, phone, _ = server.sign_in(ALICE, alice, device_name="phone")
    _, tablet, _ = server.sign_in(ALICE, alice, at=PINNED_TOTP_NOW + STEP_SECONDS, device_name="tablet")
    assert server.request("DELETE", "/api/v1/session", token=phone["access_token"])[0] == 204
    assert server.request("GET", "/api/v1/security", token=phone["access_token"])[0] == 401
    assert server.request("POST", "/api/v1/session/refresh",
                          {"refresh_token": phone["refresh_token"]})[0] == 401
    status, security, _ = server.request("GET", "/api/v1/security", token=tablet["access_token"])
    assert status == 200 and [d["name"] for d in security["devices"]] == ["tablet"]
    assert security["devices"][0]["current"] is True
    signed_out = [d for d in _devices(db) if d["name"] == "phone"][0]
    assert signed_out["revoked_reason"] == "signed_out"


def test_a_member_revokes_their_own_device_and_never_anyone_elses(server, db, cipher, alice):
    add_account(db, BOB, "bob-1")
    bob = enrolled_member(db, cipher, BOB)
    _, alice_phone, _ = server.sign_in(ALICE, alice, device_name="phone")
    _, alice_tablet, _ = server.sign_in(ALICE, alice, at=PINNED_TOTP_NOW + STEP_SECONDS, device_name="tablet")
    _, bob_phone, _ = server.sign_in(BOB, bob)

    status, body, _ = server.request("DELETE", f"/api/v1/devices/{bob_phone['device_id']}",
                                     token=alice_phone["access_token"])
    assert status == 404 and body["error"] == "not_found"
    assert server.request("GET", "/api/v1/security", token=bob_phone["access_token"])[0] == 200

    assert server.request("DELETE", f"/api/v1/devices/{alice_tablet['device_id']}",
                          token=alice_phone["access_token"])[0] == 204
    assert server.request("GET", "/api/v1/security", token=alice_tablet["access_token"])[0] == 401
    # Revoking twice is not-found the second time: nothing left to end.
    assert server.request("DELETE", f"/api/v1/devices/{alice_tablet['device_id']}",
                          token=alice_phone["access_token"])[0] == 404


def test_the_activity_page_names_the_device_and_why_it_ended(server, db, alice):
    from sentinel.portal import _activity_detail

    _, tokens, _ = server.sign_in(ALICE, alice, device_name="Alice's <iPhone>")
    server.request("DELETE", "/api/v1/session", token=tokens["access_token"])
    with db.cursor() as cur:
        cur.execute("SELECT event_type, payload_json FROM events WHERE event_type LIKE 'member_app_%' ORDER BY id")
        rows = cur.fetchall()
    assert [r["event_type"] for r in rows] == ["member_app_signed_in", "member_app_device_revoked"]
    import json

    detail = _activity_detail(rows[1]["event_type"], json.loads(rows[1]["payload_json"]))
    assert "Alice&#x27;s &lt;iPhone&gt;" in detail  # escaped, never raw markup
    assert "signed out in the app" in detail


# --- erasure -----------------------------------------------------------------------

def test_erasure_deletes_the_devices_and_their_tokens_stop_working(server, db, alice):
    from sentinel.erasure import erase_member

    _, tokens, _ = server.sign_in(ALICE, alice)
    erase_member(db, ALICE, actor=ALICE)
    assert _devices(db) == []
    assert server.request("GET", "/api/v1/security", token=tokens["access_token"])[0] == 401
    assert server.request("POST", "/api/v1/session/refresh",
                          {"refresh_token": tokens["refresh_token"]})[0] == 401


def test_the_data_export_lists_devices_and_never_their_tokens(server, db, alice):
    from sentinel.erasure import export_member_data

    server.sign_in(ALICE, alice, device_name="phone")
    exported = export_member_data(db, ALICE)
    assert [d["name"] for d in exported["app_devices"]] == ["phone"]
    assert "refresh_hash" not in exported["app_devices"][0]


# --- device names ------------------------------------------------------------------

@pytest.mark.parametrize("raw, shown", [
    ("Alice's iPhone", "Alice's iPhone"),
    ("  spaced \t out\n name ", "spaced out name"),
    ("bell\x07 and\x00 nul", "bell and nul"),
    ("x" * 200, "x" * 64),
    ("", "Phone app"),
    ("\n\t", "Phone app"),
    (None, "Phone app"),
    (42, "Phone app"),
])
def test_a_device_name_is_made_safe_to_show(raw, shown):
    assert device_name(raw) == shown
