"""Signing in when a second factor is enabled (G-28, #32).

The flow, and every place it could hand out a session it should not:

1. `/login` with email + password. If 2FA is on, **no session cookie is set**.
   A short-lived signed *pending* token is issued instead, and the code form is
   rendered.
2. `/login-code` with that pending token and a code. Only then is a session
   issued.

The pending token is the interesting object. It proves "this browser presented
a correct password moments ago" and nothing else. Everything below is about
making sure it cannot be mistaken for a session, cannot be minted without a
password, and cannot be replayed later.
"""
from __future__ import annotations

import html
import http.client
import re
import time
import urllib.parse

import pytest

from sentinel import qr
from sentinel.portal import serve_portal, set_member_password
from sentinel.totp import totp_at
from sentinel.twofactor import MEMBER, begin_enrolment, confirm_enrolment
from tests.conftest import PINNED_TOTP_NOW
from tests.helpers.landing_redirect import assert_sent_to_the_landing_page

ALICE = "alice@example.com"
PASSWORD = "a-long-enough-password"


def _free_port():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def portal(db, cipher):
    port = _free_port()
    srv = serve_portal(db, port, cipher=cipher)
    yield "127.0.0.1", port
    srv.shutdown()


def _account(db, label="alice-1", email=ALICE):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', '2026-01-01', '2026-01-01')",
            (label, email),
        )
        aid = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (aid,))
        return aid


def _post(host_port, path, body, cookie=None):
    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if cookie:
        headers["Cookie"] = f"minas_portal={cookie}"
    conn.request("POST", path, body=urllib.parse.urlencode(body), headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode()
    set_cookie = resp.getheader("Set-Cookie") or ""
    conn.close()
    token = ""
    if "minas_portal=" in set_cookie:
        token = set_cookie.split("minas_portal=", 1)[1].split(";", 1)[0]
    return resp.status, data, token


def _get(host_port, path, cookie=None):
    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {"Cookie": f"minas_portal={cookie}"} if cookie else {}
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode()
    conn.close()
    return resp.status, data


def _pending_from(body):
    marker = 'name="pending" value="'
    return body.split(marker, 1)[1].split('"', 1)[0] if marker in body else ""


@pytest.fixture
def alice_with_2fa(db, cipher):
    """Enrols using the *previous* step's code, deliberately.

    Confirming spends a step, and every step at or below the last used one is
    refused afterwards. Enrolling on the current step would leave a fixture
    whose account cannot sign in until the clock moves — which looks like a
    broken test and is actually replay protection working."""
    from sentinel.totp import STEP_SECONDS

    _account(db)
    set_member_password(db, ALICE, PASSWORD)
    secret, _ = begin_enrolment(db, cipher, MEMBER, ALICE)
    codes = confirm_enrolment(db, cipher, MEMBER, ALICE,
                              totp_at(secret, PINNED_TOTP_NOW - STEP_SECONDS))
    assert codes
    return secret, codes


# --- the password alone is not enough -----------------------------------------


def test_a_correct_password_alone_issues_no_session(portal, db, alice_with_2fa):
    """The whole point. If this sets a cookie, 2FA is decoration."""
    status, body, cookie = _post(portal, "/login", {"email": ALICE, "password": PASSWORD})

    assert cookie == "", "a session was issued before the second factor"
    assert status == 200
    assert _pending_from(body), "no code form was offered"


def test_the_pending_token_is_not_a_session(portal, db, alice_with_2fa):
    """Presented as a session cookie it must open nothing. Otherwise the
    two-step flow just renames the session and hands it over a step early."""
    _, body, _ = _post(portal, "/login", {"email": ALICE, "password": PASSWORD})
    pending = _pending_from(body)

    status, page = _get(portal, "/", cookie=pending)

    assert status == 200 and 'id="landing-title"' in page, "the landing page: a pending token is no session"
    assert "alice-1" not in page and "Signed in as" not in page


def test_a_correct_code_completes_the_sign_in(portal, db, alice_with_2fa):
    secret, _ = alice_with_2fa
    _, body, _ = _post(portal, "/login", {"email": ALICE, "password": PASSWORD})
    pending = _pending_from(body)

    status, _, cookie = _post(portal, "/login-code",
                              {"pending": pending, "code": totp_at(secret, PINNED_TOTP_NOW + 30)})

    assert status == 303 and cookie
    _, page = _get(portal, "/", cookie=cookie)
    assert "alice-1" in page


def test_a_wrong_code_issues_no_session(portal, db, alice_with_2fa):
    _, body, _ = _post(portal, "/login", {"email": ALICE, "password": PASSWORD})
    pending = _pending_from(body)

    _, _, cookie = _post(portal, "/login-code", {"pending": pending, "code": "000000"})

    assert cookie == ""


def test_a_recovery_code_completes_the_sign_in(portal, db, alice_with_2fa):
    _, codes = alice_with_2fa
    _, body, _ = _post(portal, "/login", {"email": ALICE, "password": PASSWORD})
    pending = _pending_from(body)

    _, _, cookie = _post(portal, "/login-code", {"pending": pending, "code": codes[0]})

    assert cookie


# --- the pending token cannot be minted or reused -----------------------------


def test_a_pending_token_cannot_be_forged(portal, db, alice_with_2fa):
    """Without a signature check, anyone who knows an email address skips
    straight to the code step — turning 2FA from "password AND code" into
    "code".

    The forgery is **structurally valid**: four dot-separated parts, the right
    context string, a live expiry, a plausible signature. An earlier version of
    this test used a three-part string, which was rejected by the `split`
    before it ever reached the signature check — so removing the signature
    check entirely left the test green."""
    import base64

    secret, _ = alice_with_2fa
    email_b64 = base64.urlsafe_b64encode(ALICE.encode()).decode().rstrip("=")
    forged = f"pending.{email_b64}.{int(time.time()) + 300}.{'0' * 64}"

    _, _, cookie = _post(portal, "/login-code",
                         {"pending": forged, "code": totp_at(secret, PINNED_TOTP_NOW)})

    assert cookie == ""


def test_the_security_routes_ignore_a_form_supplied_identity(portal, db, cipher):
    """Every /security route acts on the signed-in member.

    The earlier version of this test posted with no session *and no email*, so
    a handler that fell back to a form field still saw nothing and still
    refused — passing for the wrong reason. This one names the victim in the
    form, which is what an attacker would do."""
    _account(db)
    set_member_password(db, ALICE, PASSWORD)

    assert_sent_to_the_landing_page(portal, "POST", "/security/2fa/start", body={"email": ALICE})

    from sentinel.twofactor import is_enabled
    assert is_enabled(db, MEMBER, ALICE) is False


def test_repeated_wrong_codes_are_throttled(portal, db, alice_with_2fa):
    """Six digits is a million guesses, and an endpoint that accepts them as
    fast as they arrive is not a second factor. Failed codes count on the same
    limiter as failed passwords."""
    _, body, _ = _post(portal, "/login", {"email": ALICE, "password": PASSWORD})
    pending = _pending_from(body)

    statuses = [_post(portal, "/login-code", {"pending": pending, "code": "000000"})[0]
                for _ in range(12)]

    assert 429 in statuses, "wrong codes were never throttled"


def test_a_pending_token_from_a_wrong_password_is_never_issued(portal, db, alice_with_2fa):
    _, body, cookie = _post(portal, "/login", {"email": ALICE, "password": "wrong"})

    assert cookie == ""
    assert _pending_from(body) == ""
    assert "Invalid email or password" in body


def test_an_expired_pending_token_is_refused(portal, db, alice_with_2fa, monkeypatch):
    """It carries a password proof, so it must not sit in a browser for hours.
    Minutes, not the session lifetime."""
    from sentinel import portal as portal_module

    secret, _ = alice_with_2fa
    _, body, _ = _post(portal, "/login", {"email": ALICE, "password": PASSWORD})
    pending = _pending_from(body)

    monkeypatch.setattr(portal_module, "_pending_now",
                        lambda: time.time() + portal_module.PENDING_TTL_SECONDS + 10)
    _, _, cookie = _post(portal, "/login-code",
                         {"pending": pending, "code": totp_at(secret, PINNED_TOTP_NOW)})

    assert cookie == ""


# --- members without 2FA are unaffected ---------------------------------------


def test_a_member_without_two_factor_still_signs_in_in_one_step(portal, db):
    _account(db, label="bob-1", email="bob@example.com")
    set_member_password(db, "bob@example.com", PASSWORD)

    status, _, cookie = _post(portal, "/login",
                              {"email": "bob@example.com", "password": PASSWORD})

    assert status == 303 and cookie


# --- the customer can actually turn it on and off -----------------------------


def _signed_in(portal, db, email=ALICE):
    set_member_password(db, email, PASSWORD)
    _, _, cookie = _post(portal, "/login", {"email": email, "password": PASSWORD})
    return cookie


def test_a_member_can_enrol_and_is_shown_recovery_codes_once(portal, db, cipher):
    """"I can enable app-based 2FA" and "I'm given recovery codes and told to
    store them safely" — the two criteria that need a page rather than a
    function."""
    _account(db)
    cookie = _signed_in(portal, db)

    _, setup, _ = _post(portal, "/security/2fa/start", {}, cookie=cookie)
    secret = setup.split('id="totp-secret"', 1)[1].split(">", 1)[1].split("<", 1)[0]

    _, done, _ = _post(portal, "/security/2fa/confirm",
                       {"code": totp_at(secret, PINNED_TOTP_NOW)}, cookie=cookie)

    assert "recovery codes" in done.lower()
    assert "only time they can be shown" in done.lower()
    from sentinel.twofactor import is_enabled
    assert is_enabled(db, MEMBER, ALICE) is True


def test_the_enrolment_page_draws_the_uri_as_a_qr_code_above_the_secret(portal, db, cipher):
    """The page carries the otpauth URI it already shows as a QR code too -
    exactly segno's for that URI - above the secret. The secret and the URI
    stay as text: that is the route when this page is open on the phone
    that has the app, and the page says so."""
    _account(db)
    cookie = _signed_in(portal, db)

    # The connection is opened here rather than through `_post` for the
    # response headers: the page carries the secret twice over, so what
    # `_send` already does for every response is pinned on this one.
    host, port = portal
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("POST", "/security/2fa/start", body="",
                 headers={"Content-Type": "application/x-www-form-urlencoded",
                          "Cookie": f"minas_portal={cookie}"})
    resp = conn.getresponse()
    setup = resp.read().decode()
    conn.close()
    assert resp.getheader("Cache-Control") == "no-store", "a page with the secret on it is never cached"
    assert resp.getheader("X-Content-Type-Options") == "nosniff"

    secret = setup.split('id="totp-secret"', 1)[1].split(">", 1)[1].split("<", 1)[0]
    uri = html.unescape(re.search(r'<p class="mono">(otpauth://[^<]+)</p>', setup).group(1))
    assert f"secret={secret}" in uri, "the URI shown is the secret shown"
    label = html.unescape(re.search(r'<svg role="img" aria-label="([^"]*)"', setup).group(1))
    assert secret not in label and "otpauth" not in label
    assert qr.svg(uri, label=label) in setup
    assert setup.index(qr.svg(uri, label=label)) < setup.index('id="totp-secret"'), "the QR code sits above the secret"
    assert "Scan this with your authenticator app, then enter a code to switch it on." in setup
    assert "Reading this on the phone that has the app? Copy the secret below into the app instead of scanning." in setup


def test_a_wrong_enrolment_code_leaves_two_factor_off(portal, db, cipher):
    _account(db)
    cookie = _signed_in(portal, db)
    _post(portal, "/security/2fa/start", {}, cookie=cookie)

    _, body, _ = _post(portal, "/security/2fa/confirm", {"code": "000000"}, cookie=cookie)

    from sentinel.twofactor import is_enabled
    assert is_enabled(db, MEMBER, ALICE) is False
    assert "not accepted" in body.lower()


def test_the_security_page_needs_a_session(portal, db, cipher):
    """Every route under /security acts on the *signed-in* member. Without the
    check, an unauthenticated POST would enrol or disable two-factor on
    whichever account the request named."""
    _account(db)
    set_member_password(db, ALICE, PASSWORD)

    assert_sent_to_the_landing_page(portal, "GET", "/security")
    assert_sent_to_the_landing_page(portal, "POST", "/security/2fa/start", body={})

    from sentinel.twofactor import is_enabled
    assert is_enabled(db, MEMBER, ALICE) is False


def test_disabling_from_the_page_requires_a_working_code(portal, db, cipher, alice_with_2fa):
    """Note the step arithmetic: signing in spends a step, so disabling needs
    the *next* one. That is replay protection doing its job across two
    different actions, not a quirk of the test — a code good enough to sign in
    with must not still be good enough to switch the protection off a second
    later."""
    from sentinel.totp import STEP_SECONDS
    from sentinel.twofactor import is_enabled

    secret, _ = alice_with_2fa
    now = PINNED_TOTP_NOW
    _, body, _ = _post(portal, "/login", {"email": ALICE, "password": PASSWORD})
    _, _, cookie = _post(portal, "/login-code",
                         {"pending": _pending_from(body), "code": totp_at(secret, now)})
    assert cookie

    _, refused, _ = _post(portal, "/security/2fa/disable", {"code": "000000"}, cookie=cookie)
    assert is_enabled(db, MEMBER, ALICE) is True
    assert "still on" in refused.lower()

    # The code that just signed us in is spent; the next step still works.
    _post(portal, "/security/2fa/disable",
          {"code": totp_at(secret, now + STEP_SECONDS)}, cookie=cookie)
    assert is_enabled(db, MEMBER, ALICE) is False


def test_the_security_page_is_linked_from_the_home_page(portal, db, cipher):
    """A page nobody can find is a feature nobody has."""
    _account(db)
    cookie = _signed_in(portal, db)

    _, home = _get(portal, "/", cookie=cookie)

    assert "/security" in home
