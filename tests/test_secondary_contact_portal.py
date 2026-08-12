from __future__ import annotations

"""G-21 (#25): the second contact must actually be able to use the portal.

`alert_email_secondary` could be recorded, and alerts were routed to it, but the
portal was primary-only in both directions:

- `member_accounts` filtered on `lower(a.alert_email) = ?` alone, so a
  secondary contact who somehow had a password signed in to an empty portal.
- The operator's "Generate portal password" card read only `alert_email`, so
  there was no way to give the secondary contact a password in the first place.

A second pair of eyes that can be alerted but can't look anything up is not the
cover the customer was promised.
"""

import http.client
import urllib.parse

import pytest

from sentinel.portal import (
    check_member_login,
    member_accounts,
    set_member_password,
)

PRIMARY = "owner@example.com"
SECONDARY = "trusted.friend@example.com"
STRANGER = "nobody@example.com"


def _account(db, label="acct-1", primary=PRIMARY, secondary=SECONDARY):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, alert_email_secondary, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, ?, 'active', '2026-01-01', '2026-01-01')",
            (label, primary, secondary),
        )
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
        return account_id


# --- the isolation boundary --------------------------------------------------

def test_the_secondary_contact_sees_the_account(db):
    """The criterion. `member_accounts` is the single choke point every portal
    query goes through, so this is both the fix and the whole of it."""
    _account(db)
    labels = [r["label"] for r in member_accounts(db, SECONDARY)]
    assert labels == ["acct-1"]


def test_the_primary_contact_still_sees_the_account(db):
    _account(db)
    assert [r["label"] for r in member_accounts(db, PRIMARY)] == ["acct-1"]


def test_an_unrelated_email_still_sees_nothing(db):
    """Widening the boundary must not perforate it. This is the test that
    matters most: `member_accounts` is described in its own docstring as THE
    isolation boundary."""
    _account(db)
    assert member_accounts(db, STRANGER) == []


def test_the_match_is_case_insensitive_on_both_contacts(db):
    _account(db, primary="Owner@Example.COM", secondary="Trusted.Friend@Example.com")
    assert len(member_accounts(db, PRIMARY)) == 1
    assert len(member_accounts(db, SECONDARY)) == 1


def test_an_empty_email_matches_nothing(db):
    """An account with no secondary contact stores NULL or ''. An empty lookup
    must not collide with those and hand someone every account in the fleet."""
    _account(db, label="no-secondary", secondary=None)
    _account(db, label="blank-secondary", primary="a@b.com", secondary="")

    assert member_accounts(db, "") == []
    assert member_accounts(db, "   ") == []


def test_a_secondary_only_contact_is_not_given_the_primarys_other_accounts(db):
    """The secondary on one account has no claim on a different account owned
    by the same primary but not shared with them."""
    _account(db, label="shared", primary=PRIMARY, secondary=SECONDARY)
    _account(db, label="private", primary=PRIMARY, secondary=None)

    assert sorted(r["label"] for r in member_accounts(db, PRIMARY)) == ["private", "shared"]
    assert [r["label"] for r in member_accounts(db, SECONDARY)] == ["shared"]


# --- being able to sign in at all --------------------------------------------

def test_the_secondary_contact_can_sign_in_and_see_their_account(db):
    """End to end: a password exists for the secondary contact, the login is
    accepted, and the portal has something to show them."""
    _account(db)
    set_member_password(db, SECONDARY, "correct-horse-battery")

    assert check_member_login(db, SECONDARY, "correct-horse-battery") is True
    assert [r["label"] for r in member_accounts(db, SECONDARY)] == ["acct-1"]


def test_the_operator_can_generate_a_password_for_the_secondary_contact(db, cipher):
    """The other half of "can actually sign in": the ops dashboard offered the
    button only for `alert_email`, so there was no way to issue the secondary
    contact a password at all."""
    from sentinel.dashboard import serve_dashboard
    from sentinel.portal import member_exists

    account_id = _account(db)
    server = serve_dashboard(db, cipher, 0)
    port = server.server_address[1]
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        body = urllib.parse.urlencode({"account_id": account_id, "contact": "secondary"})
        conn.request("POST", "/portal-access", body,
                     {"Content-Type": "application/x-www-form-urlencoded"})
        response = conn.getresponse()
        page = response.read().decode()
        assert response.status == 200, page[:400]
    finally:
        server.shutdown()
        server.server_close()

    assert member_exists(db, SECONDARY), "no login was created for the secondary contact"
    assert SECONDARY in page, "the page should say which contact the password is for"


def test_generating_for_one_contact_does_not_create_a_login_for_the_other(db, cipher):
    """Issuing the secondary a password must not silently provision the primary
    as well, and vice versa - each is a separate credential handed to a
    separate person."""
    from sentinel.dashboard import serve_dashboard
    from sentinel.portal import member_exists

    account_id = _account(db)
    server = serve_dashboard(db, cipher, 0)
    port = server.server_address[1]
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        body = urllib.parse.urlencode({"account_id": account_id, "contact": "secondary"})
        conn.request("POST", "/portal-access", body,
                     {"Content-Type": "application/x-www-form-urlencoded"})
        conn.getresponse().read()
    finally:
        server.shutdown()
        server.server_close()

    assert member_exists(db, SECONDARY)
    assert not member_exists(db, PRIMARY)


@pytest.mark.parametrize("contact", ["", "nonsense", "alert_email", "../primary"])
def test_an_unknown_contact_selector_is_rejected_not_guessed(db, cipher, contact):
    """The selector names a database column. It must be resolved from a fixed
    set rather than interpolated, and an unrecognised value must fail rather
    than fall back to some default contact."""
    from sentinel.dashboard import serve_dashboard
    from sentinel.portal import member_exists

    account_id = _account(db)
    server = serve_dashboard(db, cipher, 0)
    port = server.server_address[1]
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        body = urllib.parse.urlencode({"account_id": account_id, "contact": contact})
        conn.request("POST", "/portal-access", body,
                     {"Content-Type": "application/x-www-form-urlencoded"})
        status = conn.getresponse().status
    finally:
        server.shutdown()
        server.server_close()

    if contact == "":
        # An absent selector keeps the historical behaviour: the primary.
        assert member_exists(db, PRIMARY)
        assert not member_exists(db, SECONDARY)
    else:
        assert status == 400, f"unknown selector {contact!r} was accepted"
        assert not member_exists(db, PRIMARY)
        assert not member_exists(db, SECONDARY)


# --- view but not change -----------------------------------------------------

def test_the_portal_offers_no_way_to_change_settings_or_remove_accounts(db, cipher):
    """Third criterion. It holds today because the member portal simply has no
    mutating routes - `/login` and `/logout` are the only POSTs, and neither
    touches an account.

    Asserted rather than assumed, because the criterion is satisfied by an
    absence: the day somebody adds a member-facing settings form, this is what
    says the secondary contact's read-only guarantee just changed. Written as a
    test that fails when the property silently stops holding, not one that
    proves it holds today."""
    from sentinel.portal import serve_portal

    _account(db)
    set_member_password(db, SECONDARY, "correct-horse-battery")

    server = serve_portal(db, 0)
    port = server.server_address[1]
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/login",
                     urllib.parse.urlencode({"email": SECONDARY, "password": "correct-horse-battery"}),
                     {"Content-Type": "application/x-www-form-urlencoded"})
        response = conn.getresponse()
        cookie = response.getheader("Set-Cookie")
        response.read()
        assert cookie, "the secondary contact could not establish a session"
        session = cookie.split(";")[0]

        # Every mutating shape a member portal might plausibly grow.
        for path in ("/account", "/settings", "/accounts/1/delete", "/account/remove",
                     "/contacts", "/thresholds", "/"):
            conn.request("POST", path, urllib.parse.urlencode({"account_id": 1}),
                         {"Content-Type": "application/x-www-form-urlencoded", "Cookie": session})
            status = conn.getresponse().status
            conn.getresponse if False else None
            assert status in (404, 405, 403), (
                f"POST {path} returned {status} - the member portal has grown a "
                f"state-changing route, and the secondary contact can reach it"
            )
    finally:
        server.shutdown()
        server.server_close()

    # And the account is untouched.
    with db.cursor() as cur:
        cur.execute("SELECT count(*) c FROM accounts")
        assert cur.fetchone()["c"] == 1
