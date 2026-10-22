"""The API's route registry is complete, every route in it is real, and every
route but sign-in refuses a request it cannot authenticate (decision 21).

The shape CONTRIBUTING asks for ("A test that posts to a path that does not
exist always passes"): a completeness test over an explicit registry, a
registration test that every classified route answers something other than
404, and only then the refusal tests, parametrised over the registry. Plus the
execution twin of tests/test_get_is_read_only.py for the API's GETs.
"""
from __future__ import annotations

import sqlite3

import pytest

from sentinel import api as api_module
from sentinel.api import MEMBER_READ, NON_PRIMARY_ALLOWED, PRE_AUTH, ROUTES, Api
from sentinel.passwords import LoginThrottle
from sentinel.portal import serve_portal
from tests.helpers.api_client import ApiClient, add_account, enrolled_member

ALICE = "alice@example.com"


@pytest.fixture
def server(db, cipher, monkeypatch):
    monkeypatch.setattr(api_module, "send_new_device_email", lambda db, address, name: None)
    srv = serve_portal(db, 0, host="127.0.0.1", cipher=cipher)
    yield ApiClient(srv.server_address[1])
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def signed_in(server, db, cipher):
    add_account(db, ALICE, "alice-1")
    secret = enrolled_member(db, cipher, ALICE)
    status, tokens, _ = server.sign_in(ALICE, secret)
    assert status == 200, tokens
    return tokens


def _path(template: str, device_id: int = 1) -> str:
    return template.replace("{id}", str(device_id))


# --- completeness -------------------------------------------------------------

def test_every_route_is_classified_exactly_once():
    buckets = (PRE_AUTH, MEMBER_READ, NON_PRIMARY_ALLOWED)
    for i, a in enumerate(buckets):
        for b in buckets[i + 1:]:
            assert not (a & b), f"classified twice: {sorted(a & b)}"
    assert PRE_AUTH | MEMBER_READ | NON_PRIMARY_ALLOWED == ROUTES
    assert all(buckets), "an empty bucket is a classification nobody made"


def test_reads_are_gets_and_nothing_else_is(db):
    assert {method for method, _ in MEMBER_READ} == {"GET"}
    assert all(method != "GET" for method, _ in PRE_AUTH | NON_PRIMARY_ALLOWED)


def test_the_dispatch_table_and_the_registry_name_the_same_routes(db):
    api = Api(db, b"secret", LoginThrottle(), lambda ip, who: f"{ip}|{who}")
    assert set(api.handlers) == ROUTES


# --- registration ----------------------------------------------------------------

@pytest.mark.parametrize("route", sorted(ROUTES))
def test_every_route_is_served(server, signed_in, route):
    method, template = route
    body = {} if method == "POST" else None
    status, data, headers = server.request(method, _path(template, signed_in["device_id"]), body,
                                           token=signed_in["access_token"])
    assert status != 404, (route, status, data)
    assert headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("path", ["/api/v1/nope", "/api/v2/session", "/api", "/api/v1/devices/abc",
                                  "/api/v1/devices/%C2%B2", "/api/v1/devices/" + "9" * 30])
def test_a_path_nobody_serves_is_a_json_404(server, signed_in, path):
    status, data, headers = server.request("GET", path, token=signed_in["access_token"])
    assert status == 404 and data["error"] == "not_found"
    assert headers["content-type"].startswith("application/json")


def test_a_real_path_with_the_wrong_method_is_405_and_says_which(server, signed_in):
    status, data, headers = server.request("GET", "/api/v1/session", token=signed_in["access_token"])
    assert status == 405 and data["error"] == "method_not_allowed"
    assert headers["allow"] == "DELETE, POST"


# --- refusal ---------------------------------------------------------------------

NEEDS_A_DEVICE = sorted(ROUTES - PRE_AUTH)


@pytest.mark.parametrize("route", NEEDS_A_DEVICE)
@pytest.mark.parametrize("authorization", [
    None, "", "Bearer", "Bearer ", "Basic YWxpY2U6cHc=", "Bearer not-a-token", "Bearer 1.9999999999.deadbeef",
])
def test_every_route_but_sign_in_refuses_without_a_valid_token(server, signed_in, db, route, authorization):
    method, template = route
    headers = {} if authorization is None else {"Authorization": authorization}
    with db.cursor() as cur:
        cur.execute("SELECT revoked_at FROM api_devices")
        before = [r["revoked_at"] for r in cur.fetchall()]
    status, data, response_headers = server.request(method, _path(template, signed_in["device_id"]),
                                                    headers=headers)
    assert status == 401 and data["error"] == "unauthorized"
    assert response_headers["www-authenticate"].startswith("Bearer")
    with db.cursor() as cur:
        cur.execute("SELECT revoked_at FROM api_devices")
        assert [r["revoked_at"] for r in cur.fetchall()] == before


@pytest.mark.parametrize("route", NEEDS_A_DEVICE)
def test_the_session_cookie_opens_nothing_in_the_api(server, db, route):
    """The API never reads the cookie - which is why it may sit outside the
    CSRF gate. A browser signed in to the portal gets nothing here."""
    from sentinel.portal import set_member_password

    add_account(db, ALICE, "alice-1")
    set_member_password(db, ALICE, "a-long-enough-password", notify=None)
    import http.client
    import urllib.parse

    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request("POST", "/login", body=urllib.parse.urlencode(
        {"email": ALICE, "password": "a-long-enough-password"}),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    cookie = resp.getheader("Set-Cookie").split(";")[0]
    conn.close()
    method, template = route
    status, data, _ = server.request(method, _path(template), headers={"Cookie": cookie})
    assert status == 401


def test_a_cross_site_header_does_not_change_what_the_api_does(server, db, cipher):
    """No ambient credential, so nothing for a cross-site request to borrow:
    the API answers on the token alone, whatever the fetch metadata says."""
    add_account(db, ALICE, "alice-1")
    secret = enrolled_member(db, cipher, ALICE)
    from sentinel.totp import totp_at
    from tests.conftest import PINNED_TOTP_NOW

    status, tokens, _ = server.request(
        "POST", "/api/v1/session",
        {"email": ALICE, "password": "a-long-enough-password", "code": totp_at(secret, PINNED_TOTP_NOW)},
        headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"})
    assert status == 200, tokens


def test_an_oversized_or_unmeasured_body_is_refused(server):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.putrequest("POST", "/api/v1/session")
    conn.putheader("Content-Length", str(9 * 1024))
    conn.endheaders()
    conn.send(b"x" * (9 * 1024))
    resp = conn.getresponse()
    assert resp.status == 400
    conn.close()

    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.putrequest("POST", "/api/v1/session")
    conn.putheader("Content-Length", "not-a-number")
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 400
    conn.close()


# --- no GET writes -----------------------------------------------------------------

def _dump(path):
    conn = sqlite3.connect(path)
    out = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
        out[name] = conn.execute(f"SELECT * FROM {name}").fetchall()
    conn.close()
    return out


@pytest.mark.parametrize("route", sorted(MEMBER_READ))
def test_no_api_get_writes_anything(server, signed_in, db, route):
    """The execution twin of tests/test_get_is_read_only.py: signed in and
    signed out, the whole database is identical before and after."""
    _, template = route
    path = _path(template, signed_in["device_id"])
    before = _dump(db._path)
    status, _, _ = server.request("GET", path, token=signed_in["access_token"])
    assert status == 200
    server.request("GET", path)
    assert _dump(db._path) == before
