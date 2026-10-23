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

from sentinel import api as api_module
from sentinel.dashboard import serve_dashboard
from sentinel.edge import TRUSTED_PROXIES_SETTING, client_address, parse_trusted_proxies
from sentinel.operators import create_operator, operator_events
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


# --- the real client behind the proxy (decision 22c item 6) ---------------------
#
# Behind Caddy every connection comes from Caddy's address. Read off the
# socket, that makes each login throttle one shared bucket: five wrong
# passwords from anyone block every member's sign-in, the app's included,
# for fifteen minutes. The address now comes from X-Forwarded-For, believed
# only from a peer named in SENTINEL_TRUSTED_PROXIES, right-most entry that
# is not itself a trusted proxy. The test client always connects from
# 127.0.0.1, so below "the trusted proxy" is 127.0.0.1 named in the setting,
# and X-Forwarded-For plays the visitor.

CADDY = parse_trusted_proxies("10.231.0.2")
VISITOR_A = "203.0.113.7"
VISITOR_B = "198.51.100.9"


def test_an_untrusted_peer_cannot_choose_its_own_address():
    """The header is a claim, and from anyone but our proxy it is ignored
    entirely - with no proxy configured, and with one configured that is
    not this peer."""
    assert client_address("6.6.6.6", [VISITOR_A], frozenset()) == "6.6.6.6"
    assert client_address("6.6.6.6", [VISITOR_A], CADDY) == "6.6.6.6"


def test_from_the_trusted_proxy_the_right_most_address_is_the_client():
    """Caddy writes the address it saw last; anything to its left came
    from the client and is whatever the client typed."""
    assert client_address("10.231.0.2", [VISITOR_A], CADDY) == VISITOR_A
    assert client_address("10.231.0.2", [f"6.6.6.6, {VISITOR_A}"], CADDY) == VISITOR_A
    # Two field lines are one list: the right-most entry is in the LAST line.
    assert client_address("10.231.0.2", ["6.6.6.6", VISITOR_A], CADDY) == VISITOR_A


def test_a_chain_of_trusted_proxies_is_walked_past():
    both = parse_trusted_proxies("10.231.0.2, 10.231.0.3")
    assert client_address("10.231.0.2", [f"6.6.6.6, {VISITOR_A}, 10.231.0.3"], both) == VISITOR_A
    # Nothing but proxies in the header names no client: the peer stands.
    assert client_address("10.231.0.2", ["10.231.0.3"], both) == "10.231.0.2"


@pytest.mark.parametrize("header", [
    ["not-an-address"],
    [f"{VISITOR_A}:4431"],          # a port is not an address
    [f"{VISITOR_A}, garbage"],      # garbage where the walk starts
    ["10.231.0.0/24"],
    ["unknown"],
])
def test_a_malformed_forwarded_for_falls_back_to_the_proxy_never_further_left(header):
    """Never anything to the left of a bad entry - that part the client
    wrote. The cost is the proxy's own bucket, which only a malformed
    request shares."""
    assert client_address("10.231.0.2", header, CADDY) == "10.231.0.2"


def test_no_header_or_an_empty_one_from_the_proxy_is_the_proxy():
    assert client_address("10.231.0.2", None, CADDY) == "10.231.0.2"
    assert client_address("10.231.0.2", [], CADDY) == "10.231.0.2"
    assert client_address("10.231.0.2", [""], CADDY) == "10.231.0.2"
    # Empty list elements are no hops (RFC 9110 5.6.1), not malformed ones.
    assert client_address("10.231.0.2", [f"{VISITOR_A}, , "], CADDY) == VISITOR_A


def test_addresses_are_compared_as_addresses_not_strings():
    assert client_address("10.231.0.2", ["2001:db8::1"], CADDY) == "2001:db8::1"
    # IPv4-mapped IPv6 is the IPv4 it carries: one client cannot be two.
    assert client_address("10.231.0.2", [f"::ffff:{VISITOR_A}"], CADDY) == VISITOR_A
    assert client_address("::ffff:10.231.0.2", [VISITOR_A], CADDY) == VISITOR_A
    assert client_address(None, [VISITOR_A], CADDY) == "?"


def test_the_setting_is_exact_addresses_and_absent_trusts_nobody(caplog):
    """None, blank and separators-only all mean no trusted proxy: the
    behaviour from before the proxy existed. A name or a range is refused
    by name in the log, and the valid entries stand - an entry that cannot
    be read only ever makes the server trust less."""
    assert parse_trusted_proxies(None) == frozenset()
    assert parse_trusted_proxies(" , ,") == frozenset()
    assert parse_trusted_proxies("10.231.0.2,10.231.0.3") == parse_trusted_proxies(" 10.231.0.3 , 10.231.0.2 ")
    with caplog.at_level("ERROR", logger="sentinel.edge"):
        parsed = parse_trusted_proxies("caddy, 10.231.0.0/24, 10.231.0.2")
    assert parsed == CADDY
    assert "'caddy'" in caplog.text and "'10.231.0.0/24'" in caplog.text


@pytest.fixture
def behind_the_proxy(monkeypatch):
    """The servers read the setting once, when they start."""
    monkeypatch.setenv(TRUSTED_PROXIES_SETTING, "127.0.0.1")


def _portal_login(port, password, forwarded_for):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/login",
                 body=urllib.parse.urlencode({"email": ALICE, "password": password}),
                 headers={"Content-Type": "application/x-www-form-urlencoded",
                          "X-Forwarded-For": forwarded_for})
    resp = conn.getresponse()
    resp.read()
    conn.close()
    return resp.status


def test_behind_the_proxy_one_visitors_failures_do_not_block_another(db, behind_the_proxy, caplog):
    """The launch blocker itself. Five wrong passwords from one visitor
    throttle that visitor, by their own address - and a member arriving
    through the same proxy from somewhere else still signs in."""
    _account(db)
    set_member_password(db, ALICE, "correct horse", notify=None)
    server = serve_portal(db, 0, host="127.0.0.1")
    try:
        port = server.server_address[1]
        with caplog.at_level("WARNING", logger="sentinel.portal"):
            for _ in range(5):
                assert _portal_login(port, "wrong", VISITOR_A) == 200
        assert _portal_login(port, "wrong", VISITOR_A) == 429
        assert _portal_login(port, "correct horse", VISITOR_B) == 303, (
            "a visitor through the same proxy was throttled by someone else's failures")
    finally:
        server.shutdown()
        server.server_close()
    assert f"from {VISITOR_A}" in caplog.text, "the failed sign-in is logged against the proxy, not the visitor"


def test_two_forwarded_for_lines_are_one_list_read_from_the_right(db, behind_the_proxy, caplog):
    """A proxy may add its own field line instead of folding into the one
    the client sent. Reading only the first line would take the client's
    word for who it is."""
    _account(db)
    set_member_password(db, ALICE, "correct horse", notify=None)
    server = serve_portal(db, 0, host="127.0.0.1")
    body = urllib.parse.urlencode({"email": ALICE, "password": "wrong"}).encode()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        conn.putrequest("POST", "/login")
        conn.putheader("Content-Type", "application/x-www-form-urlencoded")
        conn.putheader("Content-Length", str(len(body)))
        conn.putheader("X-Forwarded-For", "6.6.6.6")
        conn.putheader("X-Forwarded-For", VISITOR_A)
        with caplog.at_level("WARNING", logger="sentinel.portal"):
            conn.endheaders(body)
            resp = conn.getresponse()
            resp.read()
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
    assert resp.status == 200
    assert f"from {VISITOR_A}" in caplog.text and "6.6.6.6" not in caplog.text


def test_a_spoofed_forwarded_for_from_an_untrusted_peer_changes_nothing(db):
    """No trusted proxy (the setting unset): a fresh X-Forwarded-For on
    every attempt is not a fresh bucket. The source is still capped at five
    whatever it claims to be."""
    _account(db)
    set_member_password(db, ALICE, "correct horse", notify=None)
    server = serve_portal(db, 0, host="127.0.0.1")
    try:
        port = server.server_address[1]
        for n in range(5):
            assert _portal_login(port, "wrong", f"203.0.113.{n}") == 200
        assert _portal_login(port, "correct horse", VISITOR_B) == 429
    finally:
        server.shutdown()
        server.server_close()


def _api_sign_in(client, password, forwarded_for, secret=None):
    from sentinel.totp import totp_at
    from tests.conftest import PINNED_TOTP_NOW

    code = totp_at(secret, PINNED_TOTP_NOW) if secret else "000000"
    return client.request("POST", "/api/v1/session",
                          {"email": ALICE, "password": password, "code": code, "device_name": "Phone"},
                          headers={"X-Forwarded-For": forwarded_for})


@pytest.fixture
def api_server(db, cipher, monkeypatch):
    from tests.helpers.api_client import ApiClient

    monkeypatch.setattr(api_module, "send_new_device_email", lambda db, address, name: None)
    servers = []

    def start():
        srv = serve_portal(db, 0, host="127.0.0.1", cipher=cipher)
        servers.append(srv)
        return ApiClient(srv.server_address[1])

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def test_the_apps_sign_in_throttle_sees_the_real_address(db, cipher, api_server, behind_the_proxy, caplog):
    """The app signs in through the same proxy and the same throttle
    (sentinel/api.py). One phone's wrong guesses must not lock every other
    phone out."""
    from tests.helpers.api_client import PASSWORD, add_account, enrolled_member

    add_account(db, ALICE)
    secret = enrolled_member(db, cipher, ALICE)
    client = api_server()
    with caplog.at_level("WARNING", logger="sentinel.api"):
        for _ in range(5):
            status, body, _ = _api_sign_in(client, "wrong", VISITOR_A)
            assert status == 401, body
    status, body, _ = _api_sign_in(client, "wrong", VISITOR_A)
    assert status == 429 and body["error"] == "too_many_attempts"
    status, body, _ = _api_sign_in(client, PASSWORD, VISITOR_B, secret)
    assert status == 200, body
    assert f"from {VISITOR_A}" in caplog.text


def test_the_apps_sign_in_ignores_a_spoofed_address_from_an_untrusted_peer(db, cipher, api_server):
    from tests.helpers.api_client import PASSWORD, add_account, enrolled_member

    add_account(db, ALICE)
    secret = enrolled_member(db, cipher, ALICE)
    client = api_server()
    for n in range(5):
        status, body, _ = _api_sign_in(client, "wrong", f"203.0.113.{n}")
        assert status == 401, body
    status, body, _ = _api_sign_in(client, PASSWORD, VISITOR_B, secret)
    assert status == 429 and body["error"] == "too_many_attempts"


def _dashboard_post(port, path, fields, forwarded_for):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", path, body=urllib.parse.urlencode(fields),
                 headers={"Content-Type": "application/x-www-form-urlencoded",
                          "X-Forwarded-For": forwarded_for})
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, body


def test_the_dashboard_throttles_the_visitor_not_the_proxy(db, cipher, behind_the_proxy):
    """The operator sign-in keys on the address too. Usernames rotate so
    only the per-address key can fill: the visitor who guessed is blocked,
    an operator elsewhere is not."""
    server = serve_dashboard(db, cipher, 0)
    try:
        port = server.server_address[1]
        for n in range(5):
            status, _ = _dashboard_post(port, "/login", {"username": f"guess{n}", "password": "x"}, VISITOR_A)
            assert status == 401
        status, _ = _dashboard_post(port, "/login", {"username": "guess5", "password": "x"}, VISITOR_A)
        assert status == 429
        status, _ = _dashboard_post(port, "/login", {"username": "other", "password": "x"}, VISITOR_B)
        assert status == 401, "an operator elsewhere was throttled by someone else's guesses"
    finally:
        server.shutdown()
        server.server_close()


def test_the_dashboard_ignores_a_spoofed_address_from_an_untrusted_peer(db, cipher):
    server = serve_dashboard(db, cipher, 0)
    try:
        port = server.server_address[1]
        for n in range(5):
            status, _ = _dashboard_post(port, "/login", {"username": f"guess{n}", "password": "x"},
                                        f"203.0.113.{n}")
            assert status == 401
        status, _ = _dashboard_post(port, "/login", {"username": "other", "password": "x"}, VISITOR_B)
        assert status == 429
    finally:
        server.shutdown()
        server.server_close()


def test_the_enrolment_audit_row_names_the_visitor(db, cipher, behind_the_proxy):
    """Audit records carry the address too: an operator's first-login
    enrolment is written with where it came from, and behind the proxy that
    must be the visitor, not the proxy."""
    from sentinel.totp import totp_at
    from sentinel.twofactor import OPERATOR, pending_secret
    from tests.conftest import PINNED_TOTP_NOW

    create_operator(db, "newbie", "correct-horse-battery")
    server = serve_dashboard(db, cipher, 0)
    try:
        port = server.server_address[1]
        status, body = _dashboard_post(port, "/login", {"username": "newbie", "password": "correct-horse-battery"},
                                       VISITOR_A)
        assert status == 200 and 'action="/login/enrol"' in body
        marker = 'name="pending" value="'
        pending = body[body.index(marker) + len(marker):]
        pending = pending[:pending.index('"')]
        secret = pending_secret(db, cipher, OPERATOR, "newbie")
        status, _ = _dashboard_post(port, "/login/enrol",
                                    {"pending": pending, "code": totp_at(secret, PINNED_TOTP_NOW)}, VISITOR_A)
        assert status == 200
    finally:
        server.shutdown()
        server.server_close()
    [event] = operator_events(db, "newbie")
    assert event["detail"] == f"first-login enrolment from {VISITOR_A}"
