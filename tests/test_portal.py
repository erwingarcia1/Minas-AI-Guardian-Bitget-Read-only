from __future__ import annotations

import http.client
import time
import urllib.parse

import pytest

from sentinel.portal import (
    LoginThrottle,
    check_member_login,
    hash_password,
    make_session_token,
    member_accounts,
    serve_portal,
    set_member_password,
    verify_password,
    verify_session_token,
)


def _free_port():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _member_account(db, label, email, **extra):
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


@pytest.fixture
def portal(db):
    port = _free_port()
    srv = serve_portal(db, port)
    yield "127.0.0.1", port
    srv.shutdown()


def _request(host_port, method, path, body=None, cookie=None):
    """Raw request without redirect-following so Set-Cookie is observable."""
    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {}
    payload = None
    if body is not None:
        payload = urllib.parse.urlencode(body)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if cookie:
        headers["Cookie"] = f"minas_portal={cookie}"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode()
    set_cookie = resp.getheader("Set-Cookie") or ""
    conn.close()
    return resp.status, data, set_cookie


def _login(host_port, email, password):
    status, body, set_cookie = _request(host_port, "POST", "/login", {"email": email, "password": password})
    token = ""
    if "minas_portal=" in set_cookie:
        token = set_cookie.split("minas_portal=", 1)[1].split(";", 1)[0]
    return status, body, token


# --- password hashing ---------------------------------------------------------

def test_password_hash_roundtrip():
    encoded = hash_password("hunter2-but-long")
    assert verify_password("hunter2-but-long", encoded)
    assert not verify_password("wrong", encoded)


def test_password_hashes_are_salted():
    assert hash_password("same") != hash_password("same")


def test_malformed_hash_fails_closed():
    assert not verify_password("anything", "not-a-real-hash")
    assert not verify_password("anything", "")


# --- session tokens -----------------------------------------------------------

def test_session_token_roundtrip():
    secret = b"s" * 32
    token = make_session_token(secret, "Member@Example.com")
    assert verify_session_token(secret, token) == "member@example.com"


def test_tampered_token_rejected():
    secret = b"s" * 32
    token = make_session_token(secret, "a@b.com")
    head, _, sig = token.rpartition(".")
    assert verify_session_token(secret, head + "." + ("0" * len(sig))) is None
    assert verify_session_token(b"x" * 32, token) is None  # different secret


def test_expired_token_rejected():
    secret = b"s" * 32
    token = make_session_token(secret, "a@b.com", now=time.time() - 999999)
    assert verify_session_token(secret, token) is None


# --- member records -----------------------------------------------------------

def test_member_login_check(db):
    set_member_password(db, "Alice@Example.com", "pw-alice")
    assert check_member_login(db, "alice@example.com", "pw-alice")
    assert not check_member_login(db, "alice@example.com", "wrong")
    assert not check_member_login(db, "nobody@example.com", "pw-alice")


def test_login_timing_balanced_missing_vs_wrong_password(db, monkeypatch):
    # A missing email and a wrong password must both cost exactly ONE scrypt
    # evaluation, else response time leaks which emails are registered.
    import sentinel.portal as portal

    set_member_password(db, "alice@example.com", "pw-alice")
    calls = {"n": 0}
    real_scrypt = portal.hashlib.scrypt

    def counting_scrypt(*a, **k):
        calls["n"] += 1
        return real_scrypt(*a, **k)

    monkeypatch.setattr(portal.hashlib, "scrypt", counting_scrypt)

    calls["n"] = 0
    check_member_login(db, "alice@example.com", "wrong-password")
    wrong_pw_scrypts = calls["n"]

    calls["n"] = 0
    check_member_login(db, "ghost@example.com", "wrong-password")
    missing_email_scrypts = calls["n"]

    assert wrong_pw_scrypts == missing_email_scrypts == 1


def test_member_accounts_are_isolated_and_case_insensitive(db):
    a1 = _member_account(db, "alice-1", "Alice@Example.com")
    a2 = _member_account(db, "alice-2", "alice@example.com")
    b1 = _member_account(db, "bob-1", "bob@example.com")
    alice_ids = {r["id"] for r in member_accounts(db, "ALICE@example.com")}
    assert alice_ids == {a1, a2}
    assert b1 not in alice_ids


def test_password_reset_replaces_old(db):
    set_member_password(db, "c@d.com", "first")
    set_member_password(db, "c@d.com", "second")
    assert not check_member_login(db, "c@d.com", "first")
    assert check_member_login(db, "c@d.com", "second")


# --- throttle -------------------------------------------------------------------

def test_throttle_blocks_after_max_failures():
    t = LoginThrottle(max_failures=3, window=60)
    for _ in range(3):
        assert not t.blocked("k")
        t.record_failure("k")
    assert t.blocked("k")
    t.reset("k")
    assert not t.blocked("k")


def test_throttle_window_expires():
    t = LoginThrottle(max_failures=1, window=10)
    t.record_failure("k", now=100.0)
    assert t.blocked("k", now=105.0)
    assert not t.blocked("k", now=111.0)


# --- HTTP: auth flow ------------------------------------------------------------

def test_unauthenticated_pages_show_login(portal, db):
    for path in ("/", "/account?id=1"):
        status, body, _ = _request(portal, "GET", path)
        assert status == 200 and 'id="signin-title"' in body


def test_login_success_sets_cookie_and_shows_own_accounts(portal, db):
    _member_account(db, "alice-1", "alice@example.com")
    set_member_password(db, "alice@example.com", "pw-alice")
    status, _, token = _login(portal, "alice@example.com", "pw-alice")
    assert status == 303 and token
    status, body, _ = _request(portal, "GET", "/", cookie=token)
    assert status == 200 and "alice-1" in body and "My accounts" in body


def test_login_failure_is_generic(portal, db):
    set_member_password(db, "alice@example.com", "pw-alice")
    s1, b1, t1 = _login(portal, "alice@example.com", "wrong")
    s2, b2, t2 = _login(portal, "ghost@example.com", "whatever")
    assert (s1, s2) == (200, 200) and not t1 and not t2
    assert "Invalid email or password" in b1
    assert b1 == b2  # unknown email indistinguishable from wrong password


def test_member_cannot_see_other_members_account(portal, db):
    _member_account(db, "alice-1", "alice@example.com")
    bob_id = _member_account(db, "bob-secret", "bob@example.com")
    set_member_password(db, "alice@example.com", "pw-alice")
    _, _, token = _login(portal, "alice@example.com", "pw-alice")

    status, body, _ = _request(portal, "GET", f"/account?id={bob_id}", cookie=token)
    assert status == 404 and "bob-secret" not in body

    status, body, _ = _request(portal, "GET", "/", cookie=token)
    assert "bob-secret" not in body


def test_member_sees_own_account_detail(portal, db):
    aid = _member_account(db, "alice-1", "alice@example.com")
    set_member_password(db, "alice@example.com", "pw-alice")
    _, _, token = _login(portal, "alice@example.com", "pw-alice")
    status, body, _ = _request(portal, "GET", f"/account?id={aid}", cookie=token)
    assert status == 200 and "alice-1" in body and "Security posture" in body


def test_tampered_cookie_lands_on_login(portal, db):
    _member_account(db, "alice-1", "alice@example.com")
    set_member_password(db, "alice@example.com", "pw-alice")
    _, _, token = _login(portal, "alice@example.com", "pw-alice")
    bad = token[:-4] + ("aaaa" if token[-4:] != "aaaa" else "bbbb")
    status, body, _ = _request(portal, "GET", "/", cookie=bad)
    assert 'id="signin-title"' in body and "alice-1" not in body


def test_logout_clears_cookie(portal, db):
    set_member_password(db, "alice@example.com", "pw")
    _, _, token = _login(portal, "alice@example.com", "pw")
    status, _, set_cookie = _request(portal, "POST", "/logout", body={}, cookie=token)
    assert status == 303 and "Max-Age=0" in set_cookie


def test_login_throttled_after_repeated_failures(portal, db):
    set_member_password(db, "alice@example.com", "pw-alice")
    for _ in range(5):
        _login(portal, "alice@example.com", "wrong")
    status, body, _ = _login(portal, "alice@example.com", "pw-alice")
    assert status == 429 and "Too many attempts" in body


def test_session_cookie_attributes(portal, db):
    set_member_password(db, "alice@example.com", "pw")
    _, _, _ = _login(portal, "alice@example.com", "pw")
    status, body, set_cookie = _request(
        portal, "POST", "/login", {"email": "alice@example.com", "password": "pw"}
    )
    assert "HttpOnly" in set_cookie and "SameSite=Lax" in set_cookie and "Secure" in set_cookie


# --- ops-side password generation (dashboard endpoint) ---------------------------

def test_ops_portal_access_generates_working_login(db, cipher):
    from sentinel.dashboard import serve_dashboard

    aid = _member_account(db, "alice-1", "alice@example.com")
    ops_port = _free_port()
    ops = serve_dashboard(db, cipher, ops_port)
    try:
        status, body, _ = _request(("127.0.0.1", ops_port), "POST", "/portal-access", {"account_id": aid})
        assert status == 200 and "alice@example.com" in body
        # extract the one-time password from the banner
        import re

        m = re.search(r"<code>([^<]+)</code>", body)
        assert m, "one-time password not shown"
        password = m.group(1)
    finally:
        ops.shutdown()

    assert check_member_login(db, "alice@example.com", password)


def test_ops_portal_access_missing_email_is_404(db, cipher):
    from sentinel.dashboard import serve_dashboard
    from tests.conftest import make_account

    aid = make_account(db, "no-email")  # no alert_email
    ops_port = _free_port()
    ops = serve_dashboard(db, cipher, ops_port)
    try:
        status, _, _ = _request(("127.0.0.1", ops_port), "POST", "/portal-access", {"account_id": aid})
        assert status == 404
    finally:
        ops.shutdown()


# --- what the credential opened, said at issue time and recorded (#61) -------


def test_the_confirmation_names_every_account_the_password_opens(db, cipher):
    """The operator is about to send this password to somebody. If the blast
    radius is going to surprise them, the confirmation is the last moment it
    can — after this the credential is out of the building."""
    from sentinel.dashboard import serve_dashboard

    here = _member_account(db, "bob-trading", "bob@example.com")
    _member_account(db, "bob-cold", "bob@example.com")

    ops_port = _free_port()
    ops = serve_dashboard(db, cipher, ops_port)
    try:
        status, body, _ = _request(("127.0.0.1", ops_port), "POST", "/portal-access",
                                   {"account_id": here})
    finally:
        ops.shutdown()

    assert status == 200
    assert "bob-trading" in body and "bob-cold" in body
    assert "This password opens" in body


def test_a_single_account_confirmation_does_not_manufacture_a_warning(db, cipher):
    from sentinel.dashboard import serve_dashboard

    here = _member_account(db, "carol-only", "carol@example.com")

    ops_port = _free_port()
    ops = serve_dashboard(db, cipher, ops_port)
    try:
        _, body, _ = _request(("127.0.0.1", ops_port), "POST", "/portal-access",
                              {"account_id": here})
    finally:
        ops.shutdown()

    assert "This password opens" not in body


def test_the_event_records_what_the_credential_opened(db, cipher):
    """The forensic question asked later is "what did this password reach",
    and the email alone cannot answer it. Contacts get added and removed, so
    replaying `member_accounts` next month gives a different set than the one
    actually handed over. The grant is recorded as it was at the time."""
    import json

    from sentinel.dashboard import serve_dashboard

    here = _member_account(db, "dave-trading", "dave@example.com")
    other = _member_account(db, "dave-cold", "dave@example.com")

    ops_port = _free_port()
    ops = serve_dashboard(db, cipher, ops_port)
    try:
        _request(("127.0.0.1", ops_port), "POST", "/portal-access", {"account_id": here})
    finally:
        ops.shutdown()

    with db.cursor() as cur:
        cur.execute(
            "SELECT payload_json FROM events WHERE event_type = 'member_portal_password_generated'"
        )
        row = cur.fetchone()

    assert row is not None, "handing out a credential left no record"
    payload = json.loads(row["payload_json"])
    assert sorted(payload["granted_account_ids"]) == sorted([here, other])
    assert sorted(payload["granted_account_labels"]) == ["dave-cold", "dave-trading"]


def test_the_recorded_grant_does_not_drift_when_contacts_change(db, cipher):
    """The point of recording it. Removing the second account from the contact
    afterwards must not rewrite history — the credential did open it."""
    import json

    from sentinel.dashboard import serve_dashboard

    here = _member_account(db, "erin-trading", "erin@example.com")
    other = _member_account(db, "erin-cold", "erin@example.com")

    ops_port = _free_port()
    ops = serve_dashboard(db, cipher, ops_port)
    try:
        _request(("127.0.0.1", ops_port), "POST", "/portal-access", {"account_id": here})
    finally:
        ops.shutdown()

    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = 'someone-else@example.com' WHERE id = ?",
                    (other,))
        cur.execute(
            "SELECT payload_json FROM events WHERE event_type = 'member_portal_password_generated'"
        )
        payload = json.loads(cur.fetchone()["payload_json"])

    assert other in payload["granted_account_ids"], "the record was rewritten by a later change"
