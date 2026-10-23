"""The ops dashboard under a base path (decision 22a; 22e step 3).

Caddy forwards /admin and /admin/* to the dashboard with the path UNCHANGED
and everything else to the member portal, on one hostname. So every link,
form action, redirect and the session cookie's Path the dashboard emits must
carry the base path - a sign-in form posting to a bare /login would reach
the PORTAL's /login and fail without a word - and a request outside the base
path is not the dashboard's to answer. SENTINEL_DASHBOARD_BASE_PATH blank
keeps the root exactly as before: the rest of the dashboard suite runs
unchanged.

These tests drive a real server started with base_path="/admin", the way
docker-compose.yml runs it, and one more with the default to show the root
still works. They also close a gap: nothing pinned that a POST to an action
route WITHOUT a session is refused - every such POST in the suite carried a
cookie.
"""
from __future__ import annotations

import http.client
import re
import urllib.parse

import pytest

from sentinel import dashboard
from sentinel.dashboard import BASE_PATH_SETTING, normalize_base_path, serve_dashboard
from sentinel.operators import create_operator
from sentinel.portal import set_member_password
from sentinel.totp import STEP_SECONDS, totp_at
from sentinel.twofactor import OPERATOR, begin_enrolment, confirm_enrolment
from tests.conftest import PINNED_TOTP_NOW

PASSWORD = "correct-horse-battery"
ALICE = "alice@example.com"
# Every href and action in a rendered page, whichever quote it uses.
_LINKS = re.compile(r"""(?:href|action)=["']([^"']*)["']""")


def _request(port, method, path, *, fields=None, cookie=None, headers=None):
    """One request; (status, body, response headers). `fields` makes it a form POST."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    sent = dict(headers or {})
    if cookie:
        sent["Cookie"] = cookie
    body = None
    if fields is not None:
        body = urllib.parse.urlencode(fields)
        sent["Content-Type"] = "application/x-www-form-urlencoded"
    conn.request(method, path, body=body, headers=sent)
    response = conn.getresponse()
    data = response.read().decode()
    conn.close()
    return response.status, data, response.headers


def _pending_from(body):
    marker = 'name="pending" value="'
    start = body.index(marker) + len(marker)
    return body[start:body.index('"', start)]


def _sign_in(port, secret):
    """The two steps, under /admin. Returns (session cookie, the code step's headers)."""
    status, body, _ = _request(port, "POST", "/admin/login", fields={"username": "erwin", "password": PASSWORD})
    assert status == 200 and 'action="/admin/login/code"' in body, (status, body[:300])
    status, _, headers = _request(
        port, "POST", "/admin/login/code",
        fields={"pending": _pending_from(body), "code": totp_at(secret, PINNED_TOTP_NOW + STEP_SECONDS)})
    assert status == 303, status
    set_cookie = headers.get("Set-Cookie") or ""
    assert set_cookie.startswith("ops_session="), set_cookie
    return set_cookie.split(";")[0], headers


def _account_with_alert(db, email=ALICE):
    """One active account with a contact email and one open CRITICAL alert;
    (account id, alert id)."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES ('alice-trading', 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', "
            "'2026-01-01', '2026-01-01')", (email,))
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at) VALUES (?, 'r', 'CRITICAL', 'k', 'm', '{}', 'high', '2026-01-01')",
            (account_id,))
        return account_id, cur.lastrowid


def _count(db, table):
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) c FROM {table}")
        return cur.fetchone()["c"]


@pytest.fixture
def admin(db, cipher):
    """A dashboard served under /admin, as docker-compose.yml runs it."""
    server = serve_dashboard(db, cipher, 0, base_path="/admin")
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        # The base path is module state that every serve_dashboard sets. A
        # later test that renders a page directly (test_dashboard_problem_filter
        # calls _render) must not inherit this one's.
        dashboard._base_path = ""


@pytest.fixture
def erwin(db, cipher):
    """An enrolled operator; the TOTP secret."""
    create_operator(db, "erwin", PASSWORD)
    secret, _ = begin_enrolment(db, cipher, OPERATOR, "erwin")
    assert confirm_enrolment(db, cipher, OPERATOR, "erwin", totp_at(secret, PINNED_TOTP_NOW))
    return secret


# --- the setting ---------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("", ""), ("/", ""), (None, ""),
    ("admin", "/admin"), ("/admin", "/admin"), ("/admin/", "/admin"), ("  /admin/ ", "/admin"),
])
def test_normalize_base_path_gives_one_leading_slash_and_no_trailing_one(raw, expected):
    assert normalize_base_path(raw) == expected


def test_the_setting_is_read_once_at_start_normalised_and_logged(db, cipher, erwin, monkeypatch, caplog):
    """No `base_path` argument reads SENTINEL_DASHBOARD_BASE_PATH, once, at
    start - like the trusted proxies - and says so in the log."""
    monkeypatch.setenv(BASE_PATH_SETTING, " admin/ ")
    with caplog.at_level("INFO", logger="sentinel.dashboard"):
        server = serve_dashboard(db, cipher, 0)
    try:
        assert "served under /admin" in caplog.text
        port = server.server_address[1]
        status, body, _ = _request(port, "GET", "/admin")
        assert status == 401 and 'action="/admin/login"' in body
        monkeypatch.setenv(BASE_PATH_SETTING, "/elsewhere")  # read once: a later change changes nothing
        assert _request(port, "GET", "/admin")[0] == 401
        assert _request(port, "GET", "/elsewhere")[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        dashboard._base_path = ""


def test_with_the_default_the_dashboard_still_serves_the_root(db, cipher, erwin, caplog):
    """Blank is the root exactly as before the setting existed - the bare
    `sentinel run`, the demo server and every other dashboard test. Started
    after a /admin server ran in this process: every start sets the base
    path, so the default is back."""
    under_admin = serve_dashboard(db, cipher, 0, base_path="/admin")
    under_admin.shutdown()
    under_admin.server_close()
    with caplog.at_level("INFO", logger="sentinel.dashboard"):
        server = serve_dashboard(db, cipher, 0)
    try:
        assert "served at the root" in caplog.text
        port = server.server_address[1]
        status, body, _ = _request(port, "GET", "/")
        assert status == 401 and 'action="/login"' in body and "/admin" not in body
        assert _request(port, "GET", "/healthz")[0] in (200, 503)
        # the gap, pinned at the root too: an action POST with no session
        status, body, _ = _request(port, "POST", "/triage", fields={"alert_id": 1, "act": "handle"})
        assert status == 401 and "Operator sign-in" in body
    finally:
        server.shutdown()
        server.server_close()


# --- routing under the base path -----------------------------------------------------------------

def test_the_root_answers_at_the_base_path_with_and_without_the_slash(admin, erwin):
    """/admin and /admin/ are the same page - no redirect between them, since
    every link is root-absolute - and the sign-in form they show posts under
    the base path, or the browser would post to the PORTAL's /login."""
    for path in ("/admin", "/admin/"):
        status, body, _ = _request(admin, "GET", path)
        assert status == 401, path
        assert "Operator sign-in" in body and 'action="/admin/login"' in body, path
        assert 'action="/login"' not in body


def test_a_request_outside_the_base_path_is_not_found(admin, erwin):
    """Portal paths that reach this server, and /administrator - which
    Caddy's matcher would not forward and the dashboard refuses anyway: the
    cookie's Path=/admin is not sent there either (RFC 6265 path-match).
    Plain 404, never the sign-in page and never the fleet page."""
    for path in ("/login", "/foo", "/administrator", "/admin-x/", "/triage", "/metrics/raw"):
        status, body, _ = _request(admin, "GET", path)
        assert (status, body) == (404, "not found"), path


def test_the_bare_root_points_at_the_base_path(admin):
    """Only reachable off the proxy (the SSH tunnel's http://localhost:8390/):
    the one thing to know there is the base path, so it is said."""
    status, _, headers = _request(admin, "GET", "/")
    assert status == 303 and headers.get("Location") == "/admin/"


def test_healthz_answers_at_the_bare_path_only(admin, erwin):
    """The compose probe hits http://127.0.0.1:8390/healthz from inside the
    container, whatever base path the pages live under. Under the base path
    it is no route: /admin/healthz meets the sign-in gate like any other
    page, so the engine's liveness is not readable through Caddy."""
    status, body, _ = _request(admin, "GET", "/healthz")
    assert status in (200, 503) and body in ("ok\n", "stale\n")
    status, body, _ = _request(admin, "GET", "/admin/healthz")
    assert status == 401 and "Operator sign-in" in body
    assert body.strip() not in ("ok", "stale")


# --- the sign-in, the cookie and the pages --------------------------------------------------------

def test_the_sign_in_flow_and_its_cookie_stay_under_the_base_path(admin, erwin):
    cookie, headers = _sign_in(admin, erwin)
    assert headers.get("Location") == "/admin/"
    set_cookie = headers.get("Set-Cookie")
    attributes = [part.strip() for part in set_cookie.split(";")[1:]]
    assert "Path=/admin" in attributes, set_cookie
    assert {"HttpOnly", "Secure", "SameSite=Lax", "Max-Age=43200"} <= set(attributes), set_cookie
    status, body, _ = _request(admin, "GET", "/admin/", cookie=cookie)
    assert status == 200 and "Accounts" in body


def test_first_login_enrolment_and_its_recovery_page_stay_under_the_base_path(admin, db):
    """The other path that mints a session (200 with the recovery codes):
    its form, its cookie and its 'Continue' link carry the base path too."""
    create_operator(db, "newcomer", PASSWORD)
    status, body, _ = _request(admin, "POST", "/admin/login", fields={"username": "newcomer", "password": PASSWORD})
    assert status == 200 and 'action="/admin/login/enrol"' in body
    secret = re.search(r'id="totp-secret">([^<]+)<', body).group(1)
    pending = _pending_from(body)
    status, body, headers = _request(admin, "POST", "/admin/login/enrol",
                                     fields={"pending": pending, "code": totp_at(secret, PINNED_TOTP_NOW)})
    assert status == 200 and "recovery codes" in body
    assert 'href="/admin/"' in body
    set_cookie = headers.get("Set-Cookie") or ""
    assert set_cookie.startswith("ops_session=") and "Path=/admin" in set_cookie, set_cookie


def test_every_link_and_form_on_the_signed_in_pages_carries_the_base_path_once(admin, db, erwin):
    """The whole surface, link by link: the fleet page with its filter and
    sort links, triage with both toggles and a note form, the add form, an
    account page with both portal-credential forms, the account-not-found
    page and the metrics page. Every href and action starts with /admin and
    none doubles it - `_url` takes app-relative paths only."""
    account_id, _ = _account_with_alert(db)
    set_member_password(db, ALICE, "pw-long-enough-1", grant_account_id=account_id)
    cookie, _ = _sign_in(admin, erwin)
    pages = ("/admin/", "/admin/?state=trouble&sort=label", "/admin/triage", "/admin/triage?handled=1",
             "/admin/add", f"/admin/account?id={account_id}", "/admin/account?id=999999", "/admin/metrics")
    seen = set()
    for path in pages:
        status, body, _ = _request(admin, "GET", path, cookie=cookie)
        assert status == (404 if "999999" in path else 200), (path, status)
        links = _LINKS.findall(body)
        assert links, f"{path} rendered no link at all - the sweep is broken"
        for link in links:
            assert link == "/admin" or link.startswith("/admin/"), (path, link)
            assert "/admin/admin" not in link, (path, link)
        seen.update(links)
    # The sweep saw the forms and links the map lists, not only the nav.
    for expected in ("/admin/triage", "/admin/triage?handled=1", "/admin/add", "/admin/metrics",
                     "/admin/metrics/raw", "/admin/portal-access", "/admin/portal-grant",
                     f"/admin/account?id={account_id}", "/admin/?state=trouble", "/admin/?sort=label"):
        assert expected in seen, expected


def test_the_metrics_page_links_carry_the_base_path(admin, erwin):
    """Rendered by sentinel/metrics.py, a different module: without the base
    path its two links would land on the portal's root and a portal 404."""
    cookie, _ = _sign_in(admin, erwin)
    status, body, _ = _request(admin, "GET", "/admin/metrics", cookie=cookie)
    assert status == 200
    assert 'href="/admin/"' in body and 'href="/admin/metrics/raw"' in body
    assert 'href="/"' not in body and 'href="/metrics/raw"' not in body
    assert _request(admin, "GET", "/admin/metrics/raw", cookie=cookie)[0] == 200


def test_the_triage_redirect_and_the_sign_out_stay_under_the_base_path(admin, db, erwin):
    _, alert_id = _account_with_alert(db)
    cookie, _ = _sign_in(admin, erwin)
    status, _, headers = _request(admin, "POST", "/admin/triage", cookie=cookie,
                                  fields={"alert_id": alert_id, "act": "handle", "note": "seen"})
    assert status == 303 and headers.get("Location") == "/admin/triage"
    status, _, headers = _request(admin, "GET", "/admin/logout")
    assert status == 303 and headers.get("Location") == "/admin/login"
    cleared = headers.get("Set-Cookie") or ""
    assert cleared.startswith("ops_session=;") and "Max-Age=0" in cleared, cleared
    # Cleared with the SAME Path it was set with - a cookie is keyed by
    # name, domain and path, so any other Path would leave it in the browser.
    assert "Path=/admin" in cleared, cleared


# --- the POST gate under one hostname ----------------------------------------------------------

def test_a_post_whose_referer_is_a_portal_page_is_refused(admin, db, erwin):
    """On one hostname a portal page is same-origin to the dashboard, so
    Sec-Fetch-Site and Origin cannot tell our own form from one planted on a
    portal page. The Referer path can (decision 22a): our pages send it
    (Referrer-Policy: same-origin) and it starts with the base path. No
    Referer at all stays accepted - curl, and every other test here."""
    _, alert_id = _account_with_alert(db)
    cookie, _ = _sign_in(admin, erwin)
    fields = {"alert_id": alert_id, "act": "handle"}
    browser = {"Sec-Fetch-Site": "same-origin"}
    for referer in ("https://guardian.minastrade.com/login", "https://guardian.minastrade.com/",
                    "https://guardian.minastrade.com/administrator"):
        status, body, _ = _request(admin, "POST", "/admin/triage", cookie=cookie, fields=fields,
                                   headers={**browser, "Referer": referer})
        assert (status, body) == (403, "cross-origin POST rejected"), referer
    assert _count(db, "alert_triage") == 0, "a refused POST must write nothing"
    for referer in ("https://guardian.minastrade.com/admin/triage", "https://guardian.minastrade.com/admin"):
        status, _, _ = _request(admin, "POST", "/admin/triage", cookie=cookie, fields=fields,
                                headers={**browser, "Referer": referer})
        assert status == 303, referer
    status, _, _ = _request(admin, "POST", "/admin/triage", cookie=cookie, fields=fields)
    assert status == 303, "no Referer is a non-browser client, accepted as before"


@pytest.mark.parametrize("path", ["/admin/add", "/admin/triage", "/admin/portal-access", "/admin/portal-grant"])
def test_an_action_post_without_a_session_is_refused(admin, db, erwin, path):
    """401 with the sign-in page, no cookie minted, nothing written: no
    account added, no member password generated, no triage row."""
    account_id, alert_id = _account_with_alert(db)
    fields = {"alert_id": alert_id, "act": "handle", "account_id": account_id, "contact": "primary",
              "action": "grant", "name": "Mallory", "api_key": "k", "secret": "s", "email": ALICE}
    status, body, headers = _request(admin, "POST", path, fields=fields)
    assert status == 401 and "Operator sign-in" in body, (path, status)
    assert not (headers.get("Set-Cookie") or "").startswith("ops_session="), path
    assert _count(db, "accounts") == 1 and _count(db, "portal_members") == 0 and _count(db, "alert_triage") == 0
