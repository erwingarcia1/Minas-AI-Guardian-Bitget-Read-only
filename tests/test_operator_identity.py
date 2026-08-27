"""G-50 (#54): operators are named accounts, and their actions are attributable.

There were no operator accounts at all. Not weak ones - none. `schema.sql` had
`portal_members` for customers and nothing for us; the ops dashboard had no
login by design, with the network gate as the entire access control; and
`events` had no actor column, so the append-only log that exists to reconstruct
an incident could say *what* happened and *when* and never *who*.

Adding a customer, storing an API key, generating a member's portal password
and acknowledging a CRITICAL alert all happened with no record of who did them.

Two things fall out of this and neither could be built first: attribution needs
an identity to attribute to, and 2FA needs an account to protect.
"""

from __future__ import annotations

import http.client
import urllib.parse

import pytest

from sentinel.db import Database
from sentinel.operators import (
    check_operator_login,
    create_operator,
    disable_operator,
    enable_operator,
    list_operators,
    operator_count,
    set_operator_password,
)

# --- named accounts, separate from customers ---------------------------------

def test_an_operator_can_sign_in(db):
    create_operator(db, "erwin", "correct-horse-battery", display_name="Erwin Garcia")
    assert check_operator_login(db, "erwin", "correct-horse-battery") is True


def test_a_wrong_password_is_refused(db):
    create_operator(db, "erwin", "correct-horse-battery")
    assert check_operator_login(db, "erwin", "wrong") is False


def test_an_unknown_operator_is_refused(db):
    assert check_operator_login(db, "nobody", "anything") is False


def test_the_password_is_not_stored_in_the_clear(db):
    create_operator(db, "erwin", "correct-horse-battery")
    with db.cursor() as cur:
        cur.execute("SELECT password_hash FROM operators WHERE username = 'erwin'")
        stored = cur.fetchone()["password_hash"]
    assert "correct-horse-battery" not in stored
    assert stored.startswith("scrypt$")


def test_an_operator_is_not_a_customer(db):
    """The criterion: "an operator is not a customer with extra rights".
    Conflating the two is how an operator ends up reachable through a customer
    login, so the identities must not share a table or a lookup."""
    from sentinel.portal import check_member_login, set_member_password

    create_operator(db, "erwin", "operator-password")
    set_member_password(db, "customer@example.com", "customer-password")

    # Neither credential works against the other's login.
    assert check_member_login(db, "erwin", "operator-password") is False
    assert check_operator_login(db, "customer@example.com", "customer-password") is False

    with db.cursor() as cur:
        cur.execute("SELECT count(*) c FROM portal_members WHERE email = 'erwin'")
        assert cur.fetchone()["c"] == 0


def test_usernames_are_unique(db):
    create_operator(db, "erwin", "one")
    with pytest.raises(ValueError):
        create_operator(db, "erwin", "two")


def test_a_password_can_be_changed(db):
    create_operator(db, "erwin", "old-password")
    set_operator_password(db, "erwin", "new-password")

    assert check_operator_login(db, "erwin", "old-password") is False
    assert check_operator_login(db, "erwin", "new-password") is True


# --- offboarding is routine, not a migration ---------------------------------

def test_a_disabled_operator_cannot_sign_in(db):
    """"Operator accounts can be disabled without touching the database by
    hand - offboarding is a routine act, not a migration.\""""
    create_operator(db, "leaver", "password")
    assert disable_operator(db, "leaver") is True

    assert check_operator_login(db, "leaver", "password") is False


def test_disabling_keeps_the_account_for_attribution(db):
    """Deleting would orphan every action they ever took. A disabled operator
    is still a name the log can point at."""
    create_operator(db, "leaver", "password")
    disable_operator(db, "leaver")

    names = {o["username"]: o["status"] for o in list_operators(db)}
    assert names["leaver"] == "disabled"


def test_a_disabled_operator_can_be_re_enabled(db):
    create_operator(db, "leaver", "password")
    disable_operator(db, "leaver")
    assert enable_operator(db, "leaver") is True
    assert check_operator_login(db, "leaver", "password") is True


def test_disabling_an_unknown_operator_reports_it(db):
    assert disable_operator(db, "ghost") is False


# --- actions carry an actor ---------------------------------------------------

def test_an_event_records_its_actor(db):
    from sentinel.events import append_event

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, onboarded_at, updated_at) "
            "VALUES ('a', 'independent', 'x', 'y', 'z', '2026-01-01', '2026-01-01')"
        )
        account_id = cur.lastrowid

    append_event(db, account_id, "customer_added", "LOW", {}, actor="erwin")

    with db.cursor() as cur:
        cur.execute("SELECT actor FROM events WHERE account_id = ?", (account_id,))
        assert cur.fetchone()["actor"] == "erwin"


def test_an_event_with_no_actor_is_still_readable(db):
    """"Existing rows without an actor stay readable and are not backfilled
    with a guess." A detector firing has no person behind it, and inventing one
    would make the log lie."""
    from sentinel.events import append_event

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, onboarded_at, updated_at) "
            "VALUES ('a', 'independent', 'x', 'y', 'z', '2026-01-01', '2026-01-01')"
        )
        account_id = cur.lastrowid

    append_event(db, account_id, "withdrawal_initiated", "CRITICAL", {"order_id": "wd-1"})

    with db.cursor() as cur:
        cur.execute("SELECT actor, event_type FROM events WHERE account_id = ?", (account_id,))
        row = cur.fetchone()
    assert row["actor"] is None
    assert row["event_type"] == "withdrawal_initiated"


def test_acknowledging_an_alert_records_who(db):
    from sentinel.operators import acknowledge_alert

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, onboarded_at, updated_at) "
            "VALUES ('a', 'independent', 'x', 'y', 'z', '2026-01-01', '2026-01-01')"
        )
        account_id = cur.lastrowid
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at) VALUES (?, 'withdrawal_initiated', 'CRITICAL', 'k', 'm', '{}', "
            "'high', '2026-01-01')",
            (account_id,),
        )
        alert_id = cur.lastrowid

    assert acknowledge_alert(db, alert_id, actor="erwin") is True

    with db.cursor() as cur:
        cur.execute("SELECT acknowledged_by, acknowledged_at FROM alerts WHERE id = ?", (alert_id,))
        row = cur.fetchone()
    assert row["acknowledged_by"] == "erwin"
    assert row["acknowledged_at"] is not None


def test_an_alert_is_not_acknowledged_twice(db):
    from sentinel.operators import acknowledge_alert

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, onboarded_at, updated_at) "
            "VALUES ('a', 'independent', 'x', 'y', 'z', '2026-01-01', '2026-01-01')"
        )
        account_id = cur.lastrowid
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at) VALUES (?, 'r', 'CRITICAL', 'k', 'm', '{}', 'high', '2026-01-01')",
            (account_id,),
        )
        alert_id = cur.lastrowid

    assert acknowledge_alert(db, alert_id, actor="erwin") is True
    assert acknowledge_alert(db, alert_id, actor="someone-else") is False

    with db.cursor() as cur:
        cur.execute("SELECT acknowledged_by FROM alerts WHERE id = ?", (alert_id,))
        assert cur.fetchone()["acknowledged_by"] == "erwin", "the first ack must keep its name"


# --- the dashboard requires a sign-in ----------------------------------------

def _free_port():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve(db, cipher):
    from sentinel.dashboard import serve_dashboard

    server = serve_dashboard(db, cipher, 0)
    return server, server.server_address[1]


def _sign_in(db, cipher, port, username, password):
    """The full two-step sign-in (#87): enrol the operator's second factor
    through the API, present the password, then a code. Returns the session
    cookie, or '' when any step refused."""
    from sentinel.totp import STEP_SECONDS, totp_at
    from sentinel.twofactor import OPERATOR, begin_enrolment, confirm_enrolment, is_enabled
    from tests.conftest import PINNED_TOTP_NOW

    if not is_enabled(db, OPERATOR, username):
        secret, _ = begin_enrolment(db, cipher, OPERATOR, username)
        assert confirm_enrolment(db, cipher, OPERATOR, username, totp_at(secret, PINNED_TOTP_NOW))
    else:
        raise AssertionError("helper expects to own the enrolment")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/login", urllib.parse.urlencode({"username": username, "password": password}),
                 {"Content-Type": "application/x-www-form-urlencoded"})
    response = conn.getresponse()
    body = response.read().decode()
    if response.status != 200 or 'name="pending"' not in body:
        return ""
    marker = 'name="pending" value="'
    start = body.index(marker) + len(marker)
    pending = body[start:body.index('"', start)]
    conn.request("POST", "/login/code",
                 urllib.parse.urlencode({"pending": pending, "code": totp_at(secret, PINNED_TOTP_NOW + STEP_SECONDS)}),
                 {"Content-Type": "application/x-www-form-urlencoded"})
    response = conn.getresponse()
    response.read()
    return (response.getheader("Set-Cookie") or "").split(";")[0]


def test_the_dashboard_requires_a_sign_in_once_an_operator_exists(db, cipher):
    create_operator(db, "erwin", "correct-horse-battery")
    server, port = _serve(db, cipher)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/")
        response = conn.getresponse()
        body = response.read().decode()
    finally:
        server.shutdown()
        server.server_close()

    assert "Accounts" not in body or "sign in" in body.lower()
    assert "password" in body.lower(), "an unauthenticated visitor should get a login form"


def test_signing_in_gives_access(db, cipher):
    create_operator(db, "erwin", "correct-horse-battery")
    server, port = _serve(db, cipher)
    try:
        cookie = _sign_in(db, cipher, port, "erwin", "correct-horse-battery")
        assert cookie, "no session was issued"
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/", headers={"Cookie": cookie})
        body = conn.getresponse().read().decode()
    finally:
        server.shutdown()
        server.server_close()

    assert "Accounts" in body


def test_a_bad_password_gives_no_session(db, cipher):
    create_operator(db, "erwin", "correct-horse-battery")
    server, port = _serve(db, cipher)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST", "/login",
            urllib.parse.urlencode({"username": "erwin", "password": "nope"}),
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        cookie = response.getheader("Set-Cookie")
        response.read()
    finally:
        server.shutdown()
        server.server_close()

    assert not cookie or "session=" not in (cookie or "")


def test_a_disabled_operator_loses_dashboard_access(db, cipher):
    """Offboarding has to actually offboard, not just stop new sign-ins."""
    create_operator(db, "leaver", "password")
    server, port = _serve(db, cipher)
    try:
        cookie = _sign_in(db, cipher, port, "leaver", "password")
        assert cookie

        disable_operator(db, "leaver")

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/", headers={"Cookie": cookie})
        body = conn.getresponse().read().decode()
    finally:
        server.shutdown()
        server.server_close()

    assert "Accounts" not in body, "a disabled operator kept an existing session"


def test_with_no_operators_the_dashboard_still_works(db, cipher):
    """The upgrade path, and the lesson from the config reversal applied
    directly: shipping this must not lock an existing deployment out of its own
    dashboard. Before any operator exists the page behaves as it always has -
    and says loudly that it is unprotected."""
    assert operator_count(db) == 0
    server, port = _serve(db, cipher)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/")
        body = conn.getresponse().read().decode()
    finally:
        server.shutdown()
        server.server_close()

    assert "Accounts" in body
    assert "no operator accounts" in body.lower(), "the unprotected state must be visible"


# --- the four named actions carry a name --------------------------------------

def test_adding_a_customer_records_who(db, cipher, monkeypatch):
    """"Adding a customer" is the first of the four actions the issue names.
    Until now onboarding wrote no event at all, so there was nothing for an
    actor to hang off."""
    from unittest.mock import MagicMock

    from sentinel.onboarding import onboard_account
    from tests.fixtures.ccxt_responses import account_info_response

    client = MagicMock()
    client.fetch_balance.return_value = {"total": {"BTC": 1.0}}
    client.fetch_deposits.return_value = []
    client.fetch_withdrawals.return_value = []
    client.fetch_closed_orders.return_value = []
    client.fetch_transfers.return_value = []
    client.fetch_account_info.return_value = account_info_response()
    monkeypatch.setattr("sentinel.onboarding.make_client", lambda *a, **k: client)

    result = onboard_account(db, cipher, label="c1", topology="independent",
                             api_key="k", secret="s", passphrase="p", actor="erwin")

    with db.cursor() as cur:
        cur.execute(
            "SELECT actor FROM events WHERE account_id = ? AND event_type = 'customer_added'",
            (result.account_id,),
        )
        assert cur.fetchone()["actor"] == "erwin"


def test_generating_a_member_password_records_who(db, cipher):
    create_operator(db, "erwin", "correct-horse-battery")
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, onboarded_at, updated_at) "
            "VALUES ('c1', 'independent', 'x', 'y', 'z', 'c@example.com', '2026-01-01', '2026-01-01')"
        )
        account_id = cur.lastrowid

    server, port = _serve(db, cipher)
    try:
        cookie = _sign_in(db, cipher, port, "erwin", "correct-horse-battery")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/portal-access",
                     urllib.parse.urlencode({"account_id": account_id, "contact": "primary"}),
                     {"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie})
        conn.getresponse().read()
    finally:
        server.shutdown()
        server.server_close()

    with db.cursor() as cur:
        cur.execute(
            "SELECT actor FROM events WHERE event_type = 'member_portal_password_generated'"
        )
        row = cur.fetchone()
    assert row is not None, "handing out a credential left no record"
    assert row["actor"] == "erwin"


# --- sentinel ack from the shell ----------------------------------------------

def _alert(db):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, onboarded_at, updated_at) "
            "VALUES ('a', 'independent', 'x', 'y', 'z', '2026-01-01', '2026-01-01')"
        )
        account_id = cur.lastrowid
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at) VALUES (?, 'r', 'CRITICAL', 'k', 'm', '{}', 'high', '2026-01-01')",
            (account_id,),
        )
        return cur.lastrowid


def _ack(db_path, alert_id, as_operator=None):
    import argparse
    import os

    from sentinel.cli import cmd_ack

    os.environ["SENTINEL_DB_PATH"] = str(db_path)
    return cmd_ack(argparse.Namespace(alert_id=alert_id, as_operator=as_operator))


def test_shell_ack_requires_a_name_once_operators_exist(db, tmp_path, capsys):
    """"`events` and `alerts` can answer 'who did this' ... including
    `sentinel ack` from the shell." That command is run by whoever has SSH, so
    an unattributed ack would be a hole straight through the record."""
    alert_id = _alert(db)
    create_operator(db, "erwin", "correct-horse-battery")
    db.close()

    assert _ack(db._path, alert_id) == 2
    assert "--as" in capsys.readouterr().err


def test_shell_ack_records_the_named_operator(db, tmp_path, capsys):
    alert_id = _alert(db)
    create_operator(db, "erwin", "correct-horse-battery")
    db.close()

    assert _ack(db._path, alert_id, as_operator="erwin") == 0

    reopened = Database(db._path)
    try:
        with reopened.cursor() as cur:
            cur.execute("SELECT acknowledged_by FROM alerts WHERE id = ?", (alert_id,))
            assert cur.fetchone()["acknowledged_by"] == "erwin"
    finally:
        reopened.close()


def test_shell_ack_refuses_an_unknown_or_disabled_operator(db, tmp_path, capsys):
    alert_id = _alert(db)
    create_operator(db, "erwin", "correct-horse-battery")
    disable_operator(db, "erwin")
    db.close()

    assert _ack(db._path, alert_id, as_operator="erwin") == 2
    assert _ack(db._path, alert_id, as_operator="ghost") == 2


def test_shell_ack_still_works_before_any_operator_exists(db, tmp_path):
    """The upgrade path again: a deployment with no operator accounts keeps
    working exactly as it did, and attribution becomes mandatory the moment
    there is somebody to attribute to."""
    alert_id = _alert(db)
    db.close()

    assert _ack(db._path, alert_id) == 0

    reopened = Database(db._path)
    try:
        with reopened.cursor() as cur:
            cur.execute("SELECT acknowledged_at, acknowledged_by FROM alerts WHERE id = ?", (alert_id,))
            row = cur.fetchone()
        assert row["acknowledged_at"] is not None
        assert row["acknowledged_by"] is None, "an actor was invented where there was none"
    finally:
        reopened.close()
