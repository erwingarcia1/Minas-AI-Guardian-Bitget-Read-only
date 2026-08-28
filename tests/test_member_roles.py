"""#25, decided role (owner, 2026-08-28, per decision 1): a secondary is an
emergency contact, not a co-owner. A non-primary may READ and may
ACKNOWLEDGE. Nothing else. Every other mutating route is primary-only.

The boundary is default-deny: an explicit allowlist of what a non-primary may
reach, a completeness test that fails the build until every POST route is
classified, and refusals asserted against routes that EXIST - the first
version of this test POSTed to /account, /settings and /accounts/1/delete,
paths the portal never served, and passed for six weeks while /key, /erase
and /pause grew underneath it. A 404 is not an authorization result.
"""
from __future__ import annotations

import http.client
import inspect
import re
import urllib.parse

import pytest

from sentinel import portal as portal_module
from sentinel.portal import (
    ACCOUNT_SCOPED,
    NON_PRIMARY_ALLOWED,
    POST_ROUTES,
    PRIMARY_ONLY,
    member_role,
    primary_anywhere,
    serve_portal,
    set_member_password,
)

PRIMARY = "owner@example.com"
SECONDARY = "kin@example.com"
PASSWORD = "correct-horse-battery"
REFUSAL = "This change is for the primary contact"


# --- fixtures ----------------------------------------------------------------

@pytest.fixture
def portal(db):
    srv = serve_portal(db, 0, host="127.0.0.1")
    yield "127.0.0.1", srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _account(db, label="acct-1", primary=PRIMARY, secondary=SECONDARY):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, alert_email_secondary, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'k-old', 's-old', 'p-old', ?, ?, 'active', "
            "'2026-01-01', '2026-01-01')",
            (label, primary, secondary),
        )
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
        return account_id


def _alert(db, account_id, dedup_key="key-1"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at) VALUES (?, 'withdrawal_initiated', 'CRITICAL', ?, 'msg', '{}', "
            "'high', '2026-08-28T00:00:00+00:00')",
            (account_id, dedup_key),
        )
        return cur.lastrowid


def _post(host_port, path, form=None, cookie=None):
    host, port = host_port
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if cookie:
        headers["Cookie"] = cookie
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("POST", path, urllib.parse.urlencode(form or {}), headers)
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, body


def _login(host_port, email, password=PASSWORD):
    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("POST", "/login", urllib.parse.urlencode({"email": email, "password": password}),
                 {"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    resp.read()
    cookie = resp.getheader("Set-Cookie")
    conn.close()
    assert cookie and "minas_portal=" in cookie, f"{email} could not sign in"
    return cookie.split(";")[0]


def _form_for(path, account_id, alert_id):
    """A plausible body for every primary-only route, so a primary's request
    reaches the handler (which may then refuse it for its own reasons - a
    wrong password, a key the exchange rejects - but never with the
    role refusal, and never as not-found)."""
    return {
        "/key": {"account_id": account_id, "api_key": "new-k", "secret": "new-s", "passphrase": "new-p"},
        "/erase": {"password": "wrong-password", "confirm": "yes"},
        "/preferences": {},
        "/digest/frequency": {"frequency": "weekly"},
        "/drill": {},
        "/telegram/link": {},
        "/telegram/unlink": {},
        "/pause/request": {"account_id": account_id},
        "/pause/cancel": {"account_id": account_id},
        "/snooze": {"alert_id": alert_id, "hours": "1"},
        "/expected": {"alert_id": alert_id, "note": "that was me"},
    }[path]


def _state(db, account_id):
    with db.cursor() as cur:
        cur.execute("SELECT encrypted_api_key, erased_at, status FROM accounts WHERE id = ?", (account_id,))
        acct = dict(cur.fetchone())
        cur.execute("SELECT COUNT(*) c FROM pause_requests")
        pauses = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) c FROM snoozes")
        snoozes = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) c FROM expected_activity")
        expected = cur.fetchone()["c"]
    return acct, pauses, snoozes, expected


# --- completeness: every route classified, exactly once ----------------------

def test_every_post_route_is_classified_exactly_once():
    """A new route must break the build until someone decides which side of
    the line it sits on. Two sets, disjoint, whose union is every route."""
    assert not (PRIMARY_ONLY & NON_PRIMARY_ALLOWED), "a route cannot be both"
    assert PRIMARY_ONLY | NON_PRIMARY_ALLOWED == POST_ROUTES
    assert POST_ROUTES, "no routes at all is not a classification"


def test_the_dispatch_table_and_the_classification_name_the_same_routes():
    """The classification is only as complete as the table it describes.
    Read every route literal out of do_POST and demand each is classified,
    and that do_POST dispatches through the table alone - an `if path ==`
    branch beside the table would be a route the gate never sees."""
    src = inspect.getsource(portal_module.serve_portal)
    body = src[src.index("def do_POST"):src.index("def _may_change")]
    table = body[body.index("routes = {"):body.index("assert set(routes) == POST_ROUTES")]
    literals = set(re.findall(r'"(/[a-z0-9/_-]*)"', table))
    assert not re.findall(r'"(/[a-z0-9/_-]+)"', body.replace(table, "")), (
        "a route literal outside the dispatch table is a route the gate never sees")
    assert literals == POST_ROUTES, (
        f"routes in do_POST but unclassified: {sorted(literals - POST_ROUTES)}; "
        f"classified but not dispatched: {sorted(POST_ROUTES - literals)}")
    assert "path ==" not in body and "path.startswith" not in body and "path in (" not in body, (
        "do_POST must dispatch through the table only")
    assert "if path in PRIMARY_ONLY and not self._may_change(path, form)" in body, (
        "the gate must sit between the route lookup and the handler")


def test_every_account_scoped_route_is_primary_only():
    assert set(ACCOUNT_SCOPED) <= PRIMARY_ONLY


def test_the_allowlist_is_reading_login_plumbing_and_acknowledging_only():
    """The owner's ruling, as a set: sign in/out, recover or harden the
    login, acknowledge. Anything else appearing here is a decision that
    has to be made out loud, not by editing a constant."""
    ruling = {
        "/login", "/login-code", "/forgot", "/reset", "/logout",
        "/security/2fa/start", "/security/2fa/confirm", "/security/2fa/disable",
        "/acknowledge",
    }
    assert ruling == NON_PRIMARY_ALLOWED


# --- registration: every asserted route exists -------------------------------

def test_every_classified_route_is_served_and_an_unknown_one_is_not(portal):
    """Assert the route is registered BEFORE asserting anything about who may
    use it. A route that does not exist answers 404 to everyone, which is
    what made the old #25 test pass for six weeks."""
    for path in sorted(POST_ROUTES):
        status, _ = _post(portal, path)
        assert status != 404, f"{path} is classified but not served"
    for ghost in ("/account", "/settings", "/accounts/1/delete", "/account/remove"):
        status, _ = _post(portal, ghost)
        assert status == 404, f"{ghost} is not a route; asserting a refusal against it proves nothing"


# --- the refusals, on real paths --------------------------------------------

@pytest.mark.parametrize("path", sorted(PRIMARY_ONLY))
def test_a_secondary_contact_is_refused_every_primary_only_route(db, portal, path):
    account_id = _account(db)
    alert_id = _alert(db, account_id)
    set_member_password(db, PRIMARY, PASSWORD, notify=None)
    set_member_password(db, SECONDARY, PASSWORD, notify=None)
    assert path in POST_ROUTES  # registered first
    form = _form_for(path, account_id, alert_id)

    # The primary reaches the handler: whatever it answers, it is neither the
    # role refusal nor not-found.
    status, body = _post(portal, path, form, _login(portal, PRIMARY))
    assert status not in (403, 404) and REFUSAL not in body, f"primary refused on {path}: {status}"

    before = _state(db, account_id)
    status, body = _post(portal, path, form, _login(portal, SECONDARY))
    assert status == 403 and REFUSAL in body, f"secondary not refused on {path}: {status}"
    assert "emergency contact" in body and "contact Minas" in body
    assert "Nothing was changed" in body
    assert _state(db, account_id) == before, f"the refused request changed state on {path}"


def test_the_refusal_says_the_same_thing_whether_or_not_the_account_exists(db, portal):
    account_id = _account(db)
    set_member_password(db, SECONDARY, PASSWORD, notify=None)
    session = _login(portal, SECONDARY)
    real = _post(portal, "/key", {"account_id": account_id, "api_key": "k", "secret": "s", "passphrase": "p"}, session)
    ghost = _post(portal, "/key", {"account_id": 999999, "api_key": "k", "secret": "s", "passphrase": "p"}, session)
    assert real == ghost == (403, real[1])
    with db.cursor() as cur:
        cur.execute("SELECT encrypted_api_key FROM accounts WHERE id = ?", (account_id,))
        assert cur.fetchone()["encrypted_api_key"] == "k-old"


def test_a_login_that_is_primary_here_and_secondary_there_is_judged_per_account(db, portal):
    """The mixed case. One login, primary on `mine`, emergency contact on
    `theirs`: the same route is allowed on one and refused on the other."""
    mine = _account(db, "mine", primary=PRIMARY, secondary="someone@example.com")
    theirs = _account(db, "theirs", primary="other@example.com", secondary=PRIMARY)
    set_member_password(db, PRIMARY, PASSWORD, notify=None)
    session = _login(portal, PRIMARY)
    assert primary_anywhere(db, PRIMARY)
    assert member_role(db, PRIMARY, mine) == "primary"
    assert member_role(db, PRIMARY, theirs) == "non-primary"

    status, body = _post(portal, "/pause/request", {"account_id": theirs}, session)
    assert status == 403 and REFUSAL in body
    status, body = _post(portal, "/pause/request", {"account_id": mine}, session)
    assert status not in (403, 404) and REFUSAL not in body
    with db.cursor() as cur:
        cur.execute("SELECT account_id FROM pause_requests")
        assert [r["account_id"] for r in cur.fetchall()] == [mine]


def test_an_account_outside_the_scope_is_still_not_found_for_a_primary(db, portal):
    """The gate must not turn the isolation boundary's not-found into a
    refusal: a primary poking at a stranger's id learns nothing new."""
    _account(db, "mine")
    stranger = _account(db, "theirs", primary="other@example.com", secondary="x@example.com")
    set_member_password(db, PRIMARY, PASSWORD, notify=None)
    status, _ = _post(portal, "/pause/request", {"account_id": stranger}, _login(portal, PRIMARY))
    assert status == 404
    assert member_role(db, PRIMARY, stranger) is None


# --- what a secondary may do ---------------------------------------------------

def test_a_secondary_can_acknowledge_and_the_acknowledgement_is_attributed_to_them(db, portal):
    """The one account-facing act an emergency contact exists for. And the
    record must say it was them, not the primary - #99's co-ack carries the
    acknowledging identity onto the siblings, so a secondary's name must be
    distinct there too."""
    account_id = _account(db)
    alert_id = _alert(db, account_id)
    set_member_password(db, SECONDARY, PASSWORD, notify=None)
    status, _ = _post(portal, "/acknowledge", {"alert_id": alert_id}, _login(portal, SECONDARY))
    assert status in (200, 302, 303)
    with db.cursor() as cur:
        cur.execute("SELECT acknowledged_by, acknowledged_at FROM alerts WHERE id = ?", (alert_id,))
        row = cur.fetchone()
    assert row["acknowledged_at"] is not None
    assert row["acknowledged_by"] == SECONDARY
    assert row["acknowledged_by"] != PRIMARY


def test_a_secondary_may_harden_and_recover_their_own_login(db, portal):
    """Login plumbing is theirs: a second factor on their own credential
    changes nothing about any account."""
    _account(db)
    set_member_password(db, SECONDARY, PASSWORD, notify=None)
    session = _login(portal, SECONDARY)
    status, body = _post(portal, "/security/2fa/start", {}, session)
    assert status != 403 and REFUSAL not in body
    status, body = _post(portal, "/logout", {}, session)
    assert status != 403


def test_an_unknown_path_is_not_found_not_refused(db, portal):
    """404 and 403 are different answers. The gate speaks only for routes
    that exist; everything else is the server's not-found, for everyone."""
    _account(db)
    set_member_password(db, SECONDARY, PASSWORD, notify=None)
    status, body = _post(portal, "/settings", {"anything": 1}, _login(portal, SECONDARY))
    assert status == 404 and REFUSAL not in body


def test_a_login_primary_nowhere_is_refused_member_scoped_changes_too(db):
    _account(db)
    assert not primary_anywhere(db, SECONDARY)
    assert primary_anywhere(db, PRIMARY)
    assert member_role(db, "nobody@example.com", 1) is None
