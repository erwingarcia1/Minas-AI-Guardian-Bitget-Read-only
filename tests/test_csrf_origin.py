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
import urllib.parse

import pytest

from sentinel.dashboard import serve_dashboard
from sentinel.portal import serve_portal


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
