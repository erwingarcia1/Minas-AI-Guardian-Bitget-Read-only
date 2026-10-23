"""The CSRF gate versus real browsers (#92).

The defect: the portal set `Referrer-Policy: no-referrer`, and per Fetch a
form navigation under no-referrer serialises its Origin as `null` — so the
`_same_origin` gate rejected EVERY browser POST (sign-in, password reset,
key replacement) at every address, always. curl succeeded because it
applies no referrer policy; the test client succeeded because it sent no
Origin at all — which is why the suite never saw it. Two correct hardening
measures colliding.

The fix, both surfaces: `Sec-Fetch-Site` is the primary signal (browser-set,
unforgeable by pages), Origin comparison the fallback, bare `Origin: null`
never accepted (sandboxed iframes send it too), and the referrer policy is
`same-origin` — which still keeps URLs (reset tokens included) out of every
cross-site Referer.

The refusal is the half worth proving: these tests drive the real servers
with the exact observed header shapes, hostile ones included.
"""
from __future__ import annotations

import http.client
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sentinel.dashboard import serve_dashboard
from sentinel.edge import EdgeHandler, with_frame_ancestors
from sentinel.operators import create_operator, make_session_token, session_secret
from sentinel.portal import serve_portal, set_member_password


@pytest.fixture
def portal(db):
    server = serve_portal(db, 0, host="127.0.0.1")
    yield ("127.0.0.1", server.server_address[1])
    server.shutdown()
    server.server_close()


@pytest.fixture
def dashboard(db, cipher):
    server = serve_dashboard(db, cipher, 0)
    yield ("127.0.0.1", server.server_address[1])
    server.shutdown()
    server.server_close()


def _post(host_port, path, headers, body=None):
    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    payload = urllib.parse.urlencode(body or {"email": "a@example.com", "password": "x"})
    conn.request("POST", path, body=payload,
                 headers={"Content-Type": "application/x-www-form-urlencoded", **headers})
    resp = conn.getresponse()
    data = resp.read().decode()
    conn.close()
    return resp.status, data, dict(resp.getheaders())


# --- the observed failure, exactly as Chrome sent it --------------------------

def test_the_exact_observed_chrome_request_is_no_longer_rejected(portal):
    """Host: 127.0.0.1:<port> | Origin: null | Sec-Fetch-Site: same-origin —
    the 2026-08-20 incognito capture. This was 403 "cross-origin POST
    rejected" for every sign-in ever attempted from a browser."""
    status, body, _ = _post(portal, "/login",
                            {"Origin": "null", "Sec-Fetch-Site": "same-origin"})
    assert status != 403, body
    # It proceeds into the real login flow (wrong credentials, not a veto).
    assert "cross-origin" not in body


def test_a_user_initiated_post_passes(portal):
    status, body, _ = _post(portal, "/login",
                            {"Origin": "null", "Sec-Fetch-Site": "none"})
    assert status != 403


# --- the refusals -------------------------------------------------------------

def test_null_origin_from_a_cross_site_page_is_still_rejected(portal):
    """The trap the fix must not fall into: accepting Origin: null
    unconditionally would readmit sandboxed iframes and cross-site
    navigations, defeating the gate entirely."""
    status, body, _ = _post(portal, "/login",
                            {"Origin": "null", "Sec-Fetch-Site": "cross-site"})
    assert status == 403


def test_bare_null_origin_with_no_fetch_metadata_is_rejected(portal):
    """A client sending Origin: null and no Sec-Fetch-Site gets no benefit
    of the doubt - null alone is exactly what a hostile embed looks like."""
    status, body, _ = _post(portal, "/login", {"Origin": "null"})
    assert status == 403


def test_a_cross_site_post_with_a_matching_origin_is_still_rejected(portal):
    """Sec-Fetch-Site outranks Origin when both are present: the browser's
    own classification says cross-site, so a plausible-looking Origin does
    not rescue the request."""
    host, port = portal
    status, body, _ = _post(portal, "/login",
                            {"Origin": f"http://{host}:{port}",
                             "Host": f"{host}:{port}",
                             "Sec-Fetch-Site": "cross-site"})
    assert status == 403


def test_a_foreign_origin_without_fetch_metadata_is_rejected(portal):
    status, body, _ = _post(portal, "/login", {"Origin": "https://evil.example"})
    assert status == 403


# --- the fallbacks that must keep working -------------------------------------

def test_a_client_with_neither_header_still_passes(portal):
    """curl, scripts, and the rest of this test suite send neither header;
    the gate's job is browsers, and a headerless client is not a browser
    being coerced by a page."""
    status, body, _ = _post(portal, "/login", {})
    assert status != 403


def test_a_matching_origin_without_fetch_metadata_passes(portal):
    host, port = portal
    status, body, _ = _post(portal, "/login",
                            {"Origin": f"http://{host}:{port}",
                             "Host": f"{host}:{port}"})
    assert status != 403


# --- the root cause stays fixed -----------------------------------------------

def test_the_portal_no_longer_asks_browsers_to_nullify_origin(portal):
    """Referrer-Policy: no-referrer is what made our own posts carry
    Origin: null. same-origin keeps reset-token URLs out of cross-site
    Referers - the protection that mattered - without breaking Origin."""
    status, body, headers = _post(portal, "/login", {})
    assert headers.get("Referrer-Policy") == "same-origin"


def test_the_dashboard_now_sets_the_same_policy_explicitly(dashboard):
    """The dashboard's POSTs only ever worked because it set NO referrer
    policy at all - an accident of omission. Now the choice is explicit and
    identical, so the two surfaces cannot drift apart again."""
    status, body, headers = _post(dashboard, "/login",
                                  {"Origin": "null", "Sec-Fetch-Site": "same-origin"},
                                  body={"username": "u", "password": "p"})
    assert status != 403
    assert headers.get("Referrer-Policy") == "same-origin"


def test_the_dashboard_refuses_cross_site_too(dashboard):
    status, body, headers = _post(dashboard, "/login",
                                  {"Origin": "null", "Sec-Fetch-Site": "cross-site"},
                                  body={"username": "u", "password": "p"})
    assert status == 403


# --- HSTS and clickjacking protection on every response (decision 22c item 3) --
#
# The portal is about to answer on the internet at guardian.minastrade.com,
# and the dashboard follows it. Neither sent HSTS or any framing policy
# (checked 2026-10-22). Both now do, from sentinel/edge.py's end_headers -
# the one call every response makes - so these tests walk each server's
# response paths, not one page: rendered pages, redirects, the server's own
# error replies, the logo files, /healthz, the JSON API, and the handful of
# routes that write their headers by hand. Each status is asserted first,
# so a route that quietly became a 404 cannot pass for one that was reached.

def _assert_edge_headers(message, where):
    """Exactly one of each, with the values 22c item 3 asks for."""
    hsts = message.get_all("Strict-Transport-Security") or []
    assert len(hsts) == 1, f"{where}: Strict-Transport-Security {hsts}"
    directives = [d.strip().lower() for d in hsts[0].split(";")]
    max_age = [int(d.split("=", 1)[1]) for d in directives if d.startswith("max-age=")]
    assert max_age and max_age[0] >= 365 * 24 * 3600, f"{where}: under a year: {hsts[0]}"
    # Other minastrade.com hosts are not ours to bind to HTTPS for a year.
    assert "includesubdomains" not in directives and "preload" not in directives, f"{where}: {hsts[0]}"
    assert (message.get_all("X-Frame-Options") or []) == ["DENY"], f"{where}: X-Frame-Options"
    csp = message.get_all("Content-Security-Policy") or []
    assert len(csp) == 1, f"{where}: one policy, never two: {csp}"
    assert "frame-ancestors 'none'" in csp[0], f"{where}: {csp[0]}"


def _exchange(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, resp.msg, data


_FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def _walk(port, routes):
    """(method, path, body, headers, expected status) each, asserted."""
    for method, path, body, headers, expected in routes:
        status, message, _ = _exchange(port, method, path, body, headers)
        where = f"{method} {path}"
        assert status == expected, f"{where}: answered {status}, expected {expected} - the route was not reached"
        _assert_edge_headers(message, where)


def test_every_kind_of_portal_response_carries_the_edge_headers(db, cipher, monkeypatch):
    from sentinel import api as api_module
    from tests.helpers.api_client import ApiClient, add_account, enrolled_member

    monkeypatch.setattr(api_module, "send_new_device_email", lambda db, address, name: None)
    add_account(db, "alice@example.com")
    secret = enrolled_member(db, cipher, "alice@example.com")
    add_account(db, "bob@example.com", "bob-1")
    set_member_password(db, "bob@example.com", "bob-password-1", notify=None)
    server = serve_portal(db, 0, host="127.0.0.1", cipher=cipher)
    try:
        port = server.server_address[1]
        login = urllib.parse.urlencode({"email": "bob@example.com", "password": "bob-password-1"})
        status, message, _ = _exchange(port, "POST", "/login", login, _FORM)
        assert status == 303, "the sign-in redirect"
        _assert_edge_headers(message, "POST /login (signed in)")
        cookie = {"Cookie": message.get("Set-Cookie").split(";")[0]}
        _walk(port, [
            ("GET", "/login", None, None, 200),
            ("GET", "/", None, None, 200),                       # no session: the sign-in page
            ("GET", "/forgot", None, None, 200),
            ("GET", "/reset?token=abc", None, None, 200),
            ("GET", "/brand/mark.png", None, None, 200),         # the logo files
            ("GET", "/favicon.ico", None, None, 200),
            ("POST", "/login", urllib.parse.urlencode({"email": "x@example.com", "password": "nope"}), _FORM, 200),
            ("POST", "/login", "", {**_FORM, "Sec-Fetch-Site": "cross-site"}, 403),
            ("POST", "/logout", "", _FORM, 303),                 # a redirect
            ("POST", "/no-such-route", "", _FORM, 404),
            ("DELETE", "/", None, None, 404),
            ("PUT", "/", None, None, 501),                       # the standard library's own error reply
            ("GET", "/", None, cookie, 200),                     # signed in
            ("GET", "/export.json", None, cookie, 200),          # headers written by hand
            ("GET", "/alerts.csv", None, cookie, 200),
            ("GET", "/incident?id=999999", None, cookie, 404),
            ("GET", "/no-such-page", None, cookie, 404),
            ("GET", "/api/v1/limits", None, None, 401),          # the JSON API
            ("GET", "/api/v1/no-such-route", None, None, 404),
            ("DELETE", "/api/v1/accounts", None, None, 405),
            ("POST", "/api/v1/session", b"{not json", {"Content-Type": "application/json"}, 400),
            ("POST", "/api/v1/session", b"x", {"Content-Length": "99999999"}, 400),
        ])
        client = ApiClient(port)
        status, body, _ = client.sign_in("alice@example.com", secret)
        assert status == 200, body
        bearer = {"Authorization": f"Bearer {body['access_token']}"}
        _walk(port, [
            ("GET", "/api/v1/limits", None, bearer, 200),
            ("GET", "/api/v1/accounts", None, bearer, 200),
        ])
        status, message, _ = _exchange(port, "POST", "/api/v1/session",
                                       json.dumps({"email": "alice@example.com", "password": "x"}),
                                       {"Content-Type": "application/json"})
        assert status == 401
        _assert_edge_headers(message, "POST /api/v1/session (refused)")
    finally:
        server.shutdown()
        server.server_close()


def test_every_kind_of_dashboard_response_carries_the_edge_headers(db, cipher):
    create_operator(db, "erwin", "correct-horse-battery")
    cookie = {"Cookie": f"ops_session={make_session_token(session_secret(db), 'erwin')}"}
    server = serve_dashboard(db, cipher, 0)
    try:
        port = server.server_address[1]
        _walk(port, [
            ("GET", "/healthz", None, None, 503),                # no engine cycle in this test
            ("GET", "/login", None, None, 200),
            ("GET", "/", None, None, 401),                       # no session: the sign-in page
            ("GET", "/logout", None, None, 303),                 # a redirect
            ("POST", "/login", urllib.parse.urlencode({"username": "erwin", "password": "nope"}), _FORM, 401),
            ("POST", "/login", "", {**_FORM, "Sec-Fetch-Site": "cross-site"}, 403),
            ("PUT", "/", None, None, 501),                       # the standard library's own error reply
            ("GET", "/", None, cookie, 200),                     # signed in
            ("GET", "/metrics", None, cookie, 200),
            ("GET", "/metrics/raw", None, cookie, 200),          # headers written by hand
            ("GET", "/favicon.ico", None, cookie, 204),
            ("GET", "/account?id=999999", None, cookie, 404),
            ("POST", "/triage", urllib.parse.urlencode({"alert_id": "999999", "act": "handle"}),
             {**_FORM, **cookie}, 404),
            ("POST", "/no-such-route", "", {**_FORM, **cookie}, 404),
        ])
    finally:
        server.shutdown()
        server.server_close()


def test_a_route_policy_is_merged_and_cannot_weaken_the_edge():
    """No route sends a policy of its own today. One that does gets ONE
    Content-Security-Policy carrying its directives and frame-ancestors
    'none' - two headers would each be enforced, and a stray
    frame-ancestors would be a second opinion - and a route's own
    X-Frame-Options or HSTS is overruled, never sent alongside ours."""

    class Handler(EdgeHandler, BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors *")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Strict-Transport-Security", "max-age=0")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, message, _ = _exchange(server.server_address[1], "GET", "/")
    finally:
        server.shutdown()
        server.server_close()
    assert status == 200
    _assert_edge_headers(message, "a route with its own policy")
    assert message.get_all("Content-Security-Policy") == ["default-src 'self'; frame-ancestors 'none'"]
    assert message.get_all("Strict-Transport-Security") == ["max-age=31536000"]


def test_frame_ancestors_is_added_or_replaced_never_duplicated():
    assert with_frame_ancestors(None) == "frame-ancestors 'none'"
    assert with_frame_ancestors("default-src 'self'") == "default-src 'self'; frame-ancestors 'none'"
    assert with_frame_ancestors("Frame-Ancestors https://evil.example; img-src *;") == (
        "img-src *; frame-ancestors 'none'")
