"""#31: someone else guessing my email can't lock me out, and sessions die
of idleness.

The lockout half: the throttle keyed failures on the BARE email, so any
stranger who knew a victim's address could post wrong passwords and lock
the victim out of the portal - the page they most need mid-incident, and a
denial-of-service an attacker would pair with the theft it hides. Identity
throttling is now keyed to (ip, identity); the per-IP key still caps any
single source. The deliberate residue, recorded in the code: a DISTRIBUTED
guesser is throttled per-IP only, because the alternative hands every
stranger a lockout button.

The session half: tokens now carry two deadlines - an idle window that
activity slides forward (the cookie is re-issued on authenticated
requests) and an absolute cap that every refresh copies and none extends.

The server-level tests all originate from 127.0.0.1, so cross-IP behaviour
is proven at the throttle-semantics level and the server's KEY COMPOSITION
is proven with a recording spy - together they pin the criterion without
pretending the test client can forge source addresses.
"""
from __future__ import annotations

import http.client
import time
import urllib.parse

import pytest

from sentinel.passwords import LoginThrottle
from sentinel.portal import (
    SESSION_IDLE_SECONDS,
    SESSION_TTL_SECONDS,
    make_session_token,
    serve_portal,
    session_hard_deadline,
    set_member_password,
    verify_session_token,
)

ALICE = "alice@example.com"
SECRET = b"s" * 32


def _account(db, email=ALICE):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, "
            "encrypted_secret, encrypted_passphrase, alert_email, status, onboarded_at, "
            "updated_at) VALUES ('acct-1', 'bitget', 'independent', 'x', 'y', 'z', ?, "
            "'active', '2026-01-01', '2026-01-01')",
            (email,),
        )


# --- throttle semantics: the pair key is the fix ------------------------------

def test_an_attackers_failures_do_not_block_the_victims_own_pair():
    """The criterion. Five failures from the attacker's address leave the
    victim's (ip, email) pair - and therefore the victim - untouched."""
    throttle = LoginThrottle()
    for _ in range(5):
        throttle.record_failure(f"6.6.6.6|{ALICE}")

    assert throttle.blocked(f"6.6.6.6|{ALICE}") is True     # the attacker
    assert throttle.blocked(f"203.0.113.7|{ALICE}") is False  # the victim


def test_one_source_is_still_capped_across_every_email():
    """The per-IP key survives: a single source rotating emails is blocked
    by its address alone."""
    throttle = LoginThrottle()
    for _ in range(5):
        throttle.record_failure("6.6.6.6")

    assert throttle.blocked("6.6.6.6") is True


# --- the server composes the keys correctly -----------------------------------

class _SpyThrottle:
    instances: list = []

    def __init__(self, *a, **k):
        self.blocked_keys = []
        self.failure_keys = []
        _SpyThrottle.instances.append(self)

    def blocked(self, key, now=None):
        self.blocked_keys.append(key)
        return False

    def record_failure(self, key, now=None):
        self.failure_keys.append(key)

    def reset(self, key):
        pass


def test_the_login_route_never_throttles_on_the_bare_email(db, monkeypatch):
    monkeypatch.setattr("sentinel.portal.LoginThrottle", _SpyThrottle)
    _SpyThrottle.instances = []
    server = serve_portal(db, 0, host="127.0.0.1")
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        conn.request("POST", "/login",
                     body=urllib.parse.urlencode({"email": ALICE, "password": "wrong"}),
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        conn.getresponse().read()
        conn.close()
    finally:
        server.shutdown()
        server.server_close()

    spy = _SpyThrottle.instances[0]
    seen = set(spy.blocked_keys + spy.failure_keys)
    assert f"127.0.0.1|{ALICE}" in seen, "identity throttling lost its ip prefix"
    assert "127.0.0.1" in seen, "the per-IP cap is gone"
    assert ALICE not in seen, (
        "the bare email is back in the throttle - the lockout-by-guessing "
        "vector #31 closed has reopened")


def test_repeated_failures_from_one_source_still_get_429(db):
    """The refusal half at the server level: the per-IP key blocks the
    sixth attempt from the same source."""
    _account(db)
    set_member_password(db, ALICE, "correct horse", notify=None)
    server = serve_portal(db, 0, host="127.0.0.1")
    try:
        port = server.server_address[1]

        def attempt():
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/login",
                         body=urllib.parse.urlencode({"email": ALICE, "password": "wrong"}),
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            resp = conn.getresponse()
            resp.read()
            conn.close()
            return resp.status

        for _ in range(5):
            assert attempt() == 200  # rejected, but not yet throttled
        assert attempt() == 429
    finally:
        server.shutdown()
        server.server_close()


# --- sessions: idle window slides, the absolute cap never does -----------------

def test_a_session_left_alone_expires_after_the_idle_window():
    token = make_session_token(SECRET, ALICE, now=1000.0)
    assert verify_session_token(SECRET, token, now=1000.0 + SESSION_IDLE_SECONDS - 1) == ALICE
    assert verify_session_token(SECRET, token, now=1000.0 + SESSION_IDLE_SECONDS + 1) is None


def test_no_refresh_extends_past_the_absolute_deadline():
    """A refresh copies the hard deadline, never moves it: even a token
    re-issued moments ago dies at the original cap."""
    hard = 1000.0 + SESSION_TTL_SECONDS
    first = make_session_token(SECRET, ALICE, now=1000.0)
    assert session_hard_deadline(first) == pytest.approx(hard)

    # Simulate the server's refresh just before the cap.
    refreshed = make_session_token(SECRET, ALICE, now=hard - 10,
                                   absolute_deadline=session_hard_deadline(first))
    assert session_hard_deadline(refreshed) == pytest.approx(hard)
    assert verify_session_token(SECRET, refreshed, now=hard - 5) == ALICE
    assert verify_session_token(SECRET, refreshed, now=hard + 5) is None, (
        "activity extended a session past the absolute cap")


def test_pre_upgrade_four_field_tokens_are_refused():
    """The old format had one deadline. Refusing it cleanly means everyone
    signs in once after the upgrade - never a crash, never an accept."""
    import base64
    import hashlib
    import hmac as hmac_mod

    email_b64 = base64.urlsafe_b64encode(ALICE.encode()).decode().rstrip("=")
    payload = f"{email_b64}.{int(time.time()) + 9999}.1"
    sig = hmac_mod.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()
    assert verify_session_token(SECRET, f"{payload}.{sig}") is None


def test_activity_re_issues_the_cookie_with_the_same_hard_deadline(db):
    """End to end: sign in, make an authenticated request, and the response
    carries a refreshed cookie whose absolute deadline matches the
    original's - the sliding window is real and the cap is untouched."""
    _account(db)
    set_member_password(db, ALICE, "correct horse", notify=None)
    server = serve_portal(db, 0, host="127.0.0.1")
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/login",
                     body=urllib.parse.urlencode({"email": ALICE, "password": "correct horse"}),
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        resp.read()
        set_cookie = resp.getheader("Set-Cookie") or ""
        conn.close()
        token = set_cookie.split("minas_portal=", 1)[1].split(";", 1)[0]
        original_hard = session_hard_deadline(token)
        assert original_hard is not None

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/", headers={"Cookie": f"minas_portal={token}"})
        resp = conn.getresponse()
        resp.read()
        refresh = resp.getheader("Set-Cookie") or ""
        conn.close()
        assert "minas_portal=" in refresh, "no sliding refresh on activity"
        refreshed = refresh.split("minas_portal=", 1)[1].split(";", 1)[0]
        assert session_hard_deadline(refreshed) == pytest.approx(original_hard), (
            "the refresh moved the absolute deadline")
        # The idle deadline slides forward (equal is possible when both
        # requests land in the same integer second - it must never regress).
        assert int(refreshed.split(".")[1]) >= int(token.split(".")[1])
    finally:
        server.shutdown()
        server.server_close()
