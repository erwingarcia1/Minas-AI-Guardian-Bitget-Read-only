"""#25 residue (a): nothing a GET does mutates state.

The whole primary/secondary gate lives on do_POST. That is only sound if no
GET path writes - an assumption nobody had asserted. This proves it by
EXECUTION: every GET route the portal serves is fetched, signed in as a
member and unauthenticated, and the entire database is compared before and
after. The route list is read out of do_GET's source so a new GET route is
covered the day it is added; a literal there that the list does not know
fails the test.
"""
from __future__ import annotations

import http.client
import inspect
import re
import sqlite3
import urllib.parse

import pytest

from sentinel import portal as portal_module
from sentinel.portal import serve_portal, set_member_password
from tests.conftest import make_account

MEMBER = "owner@example.com"
PASSWORD = "pw-long-enough-1"

# Every GET path, with a query string that reaches the handler's real body.
GET_ROUTES = {
    "/": "/", "/login": "/login", "/alerts": "/alerts?severity=CRITICAL", "/limits": "/limits", "/status": "/status",
    "/activity": "/activity", "/export.json": "/export.json", "/alerts.csv": "/alerts.csv",
    "/incident": "/incident?id={alert}", "/security": "/security", "/key": "/key?id={account}",
    "/account": "/account?id={account}", "/forgot": "/forgot", "/reset": "/reset?token=not-a-token",
    "/privacy": "/privacy", "/welcome": "/welcome",
    "/favicon.ico": "/favicon.ico", "/brand/mark.png": "/brand/mark.png", "/brand/icon.png": "/brand/icon.png",
    "/brand/apple-touch-icon.png": "/brand/apple-touch-icon.png",
}


def _get_literals() -> set[str]:
    src = inspect.getsource(portal_module.serve_portal)
    body = src[src.index("def do_GET"):src.index("def _pause")]
    # The logo files are served from one table (sentinel/brand.py), not by
    # literal: they count as GET routes all the same.
    assert "if path in WEB_ASSETS:" in body
    return set(re.findall(r'path == "(/[a-z0-9/._-]*)"', body)) | set(portal_module.WEB_ASSETS)


def test_the_route_list_matches_do_get():
    literals = _get_literals()
    assert literals == set(GET_ROUTES), (
        f"GET routes not covered by this test: {sorted(literals - set(GET_ROUTES))}; "
        f"listed but not served: {sorted(set(GET_ROUTES) - literals)}")


@pytest.fixture
def portal(db):
    aid = make_account(db)
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = ?, alert_email_secondary = 'kin@example.com' WHERE id = ?",
                    (MEMBER, aid))
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, confidence, "
            "sent_at, delivered_to) VALUES (?, 'withdrawal_initiated', 'CRITICAL', 'k', 'm', '{}', 'high', "
            "'2026-08-28T00:00:00+00:00', 0)", (aid,))
        alert_id = cur.lastrowid
    set_member_password(db, MEMBER, PASSWORD, notify=None)
    srv = serve_portal(db, 0, host="127.0.0.1")
    yield srv.server_address[1], aid, alert_id
    srv.shutdown()
    srv.server_close()


def _dump(path):
    conn = sqlite3.connect(path)
    out = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
        out[name] = conn.execute(f"SELECT * FROM {name}").fetchall()
    conn.close()
    return out


def _get(port, path, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers={"Cookie": cookie} if cookie else {})
    resp = conn.getresponse()
    resp.read()
    conn.close()
    return resp.status


def _login(port):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/login", urllib.parse.urlencode({"email": MEMBER, "password": PASSWORD}),
                 {"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    resp.read()
    cookie = resp.getheader("Set-Cookie")
    conn.close()
    assert cookie and "minas_portal=" in cookie
    return cookie.split(";")[0]


@pytest.mark.parametrize("route", sorted(GET_ROUTES))
def test_a_get_writes_nothing_signed_in_or_not(db, portal, route):
    port, aid, alert_id = portal
    path = GET_ROUTES[route].format(account=aid, alert=alert_id)
    cookie = _login(port)
    before = _dump(db._path)
    status = _get(port, path, cookie)
    assert status in (200, 204, 302, 303, 404), f"{path} answered {status} signed in"
    after = _dump(db._path)
    changed = [t for t in after if before.get(t) != after[t]]
    assert changed == [], f"GET {path} wrote to {changed}"
    before = _dump(db._path)
    _get(port, path)  # unauthenticated
    after = _dump(db._path)
    assert [t for t in after if before.get(t) != after[t]] == [], f"unauthenticated GET {path} wrote"


def test_a_signed_in_get_answers_the_page_not_the_login(portal):
    port, aid, _ = portal
    assert _get(port, f"/account?id={aid}", _login(port)) == 200
