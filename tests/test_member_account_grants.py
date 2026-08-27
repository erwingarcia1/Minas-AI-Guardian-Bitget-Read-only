"""#61 / decision 10: a portal credential opens exactly what was granted.

Identity stays the email; scope is no longer inferred. A credential issued
from one account's page opens that account and nothing else. A login
nobody has scoped yet keeps the legacy rule (every account the address is
a contact on) until an operator touches its scope - and at that moment its
contact rows are materialised into grants FIRST, so the first explicit
change starts from what it already opened and revoking never widens.
"""
from __future__ import annotations

import http.client
import urllib.parse

import pytest

from sentinel.portal import (
    contact_accounts,
    grant_account,
    is_scoped,
    materialise_scope,
    member_accounts,
    revoke_account,
    set_member_password,
)

ALICE = "alice@example.com"


def _account(db, label, email=None, secondary=None):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, alert_email_secondary, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, ?, 'active', '2026-01-01', '2026-01-01')",
            (label, email, secondary))
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
        return account_id


def _labels(rows):
    return sorted(r["label"] for r in rows)


def _events(db, account_id):
    with db.cursor() as cur:
        cur.execute("SELECT event_type, actor FROM events WHERE account_id = ? ORDER BY id", (account_id,))
        return [(r["event_type"], r["actor"]) for r in cur.fetchall()]


# --- legacy logins are untouched -------------------------------------------------

def test_a_login_nobody_scoped_keeps_the_contact_rule(db):
    _account(db, "a1", ALICE)
    _account(db, "a2", ALICE)
    _account(db, "b1", "bob@example.com", secondary=ALICE)
    set_member_password(db, ALICE, "pw-long-enough-1")
    assert not is_scoped(db, ALICE)
    assert _labels(member_accounts(db, ALICE)) == ["a1", "a2", "b1"]


# --- a credential issued from one page opens that page's account only --------------

def test_a_new_credential_from_one_account_opens_only_that_account(db):
    a1 = _account(db, "a1", ALICE)
    _account(db, "a2", ALICE)
    set_member_password(db, ALICE, "pw-long-enough-1", grant_account_id=a1, actor="erwin")
    assert is_scoped(db, ALICE)
    assert _labels(member_accounts(db, ALICE)) == ["a1"]
    assert ("member_account_granted", "erwin") in _events(db, a1)


def test_resetting_an_existing_legacy_login_from_one_page_keeps_what_it_opened(db):
    """A password reset must not silently shrink a customer's access: the
    legacy scope is materialised, then this account is ensured."""
    a1 = _account(db, "a1", ALICE)
    a2 = _account(db, "a2", ALICE)
    set_member_password(db, ALICE, "pw-long-enough-1")
    set_member_password(db, ALICE, "pw-long-enough-2", grant_account_id=a1, actor="erwin")
    assert is_scoped(db, ALICE)
    assert _labels(member_accounts(db, ALICE)) == ["a1", "a2"]
    # Materialisation is not a new grant: no grant event lands on a2 (the
    # password-change fan-out rows are the audit trail of the reset itself).
    assert not any(kind == "member_account_granted" for kind, _ in _events(db, a2))


# --- grant and revoke ---------------------------------------------------------------

def test_granting_adds_exactly_one_account_and_is_recorded(db):
    a1 = _account(db, "a1", ALICE)
    c1 = _account(db, "c1", "carol@example.com")
    set_member_password(db, ALICE, "pw-long-enough-1", grant_account_id=a1, actor="erwin")
    assert grant_account(db, ALICE, c1, actor="erwin") is True
    assert grant_account(db, ALICE, c1, actor="erwin") is False
    assert _labels(member_accounts(db, ALICE)) == ["a1", "c1"]
    assert _events(db, c1) == [("member_account_granted", "erwin")]


def test_revoking_a_legacy_login_never_widens(db):
    """The trap decision 10 names: a login with no explicit grants that had
    one account 'revoked' must end with the others, not with everything and
    not with nothing."""
    _account(db, "a1", ALICE)
    a2 = _account(db, "a2", ALICE)
    _account(db, "a3", ALICE)
    set_member_password(db, ALICE, "pw-long-enough-1")
    assert revoke_account(db, ALICE, a2, actor="erwin") is True
    assert is_scoped(db, ALICE)
    assert _labels(member_accounts(db, ALICE)) == ["a1", "a3"]
    assert _events(db, a2)[-1] == ("member_account_revoked", "erwin")
    assert revoke_account(db, ALICE, a2, actor="erwin") is False


def test_revoking_the_last_grant_leaves_nothing_open(db):
    a1 = _account(db, "a1", ALICE)
    set_member_password(db, ALICE, "pw-long-enough-1", grant_account_id=a1)
    revoke_account(db, ALICE, a1, actor="erwin")
    assert member_accounts(db, ALICE) == []
    assert contact_accounts(db, ALICE) != [], "the contact row still exists - the scope is what changed"


def test_materialising_twice_is_a_no_op(db):
    _account(db, "a1", ALICE)
    _account(db, "a2", ALICE)
    set_member_password(db, ALICE, "pw-long-enough-1")
    assert materialise_scope(db, ALICE, actor="erwin") == 2
    assert materialise_scope(db, ALICE, actor="erwin") == 0


def test_a_grant_needs_a_login_and_a_real_account(db):
    a1 = _account(db, "a1", ALICE)
    with pytest.raises(ValueError, match="no portal login"):
        grant_account(db, "nobody@example.com", a1)
    set_member_password(db, ALICE, "pw-long-enough-1")
    with pytest.raises(ValueError, match="no such account"):
        grant_account(db, ALICE, 999999)


def test_a_new_contact_row_does_not_widen_a_scoped_login(db):
    """Being added as a contact on another account alerts you about it; it
    does not open it in the portal any more. That is the model."""
    a1 = _account(db, "a1", ALICE)
    set_member_password(db, ALICE, "pw-long-enough-1", grant_account_id=a1)
    _account(db, "later", ALICE)
    assert _labels(member_accounts(db, ALICE)) == ["a1"]


# --- the portal enforces it --------------------------------------------------------

def test_a_scoped_member_cannot_read_an_incident_on_an_account_they_are_only_a_contact_on(db):
    from tests.test_incident_page import _incident, _login, _request

    a1 = _account(db, "a1", ALICE)
    a2 = _account(db, "a2", ALICE)
    set_member_password(db, ALICE, "pw-long-enough-1", grant_account_id=a1)
    other = _incident(db, a2)
    mine = _incident(db, a1)
    from sentinel.portal import serve_portal

    srv = serve_portal(db, 0)
    port = srv.server_address[1]
    try:
        token = _login(("127.0.0.1", port), ALICE, "pw-long-enough-1")
        status_other, _ = _request(("127.0.0.1", port), f"/incident?id={other}", cookie=token)
        status_mine, _ = _request(("127.0.0.1", port), f"/incident?id={mine}", cookie=token)
    finally:
        srv.shutdown()
    assert status_mine == 200 and status_other == 404


# --- the dashboard: what the card says and does -------------------------------------

def _text(html_src: str) -> str:
    import re

    return re.sub(r"<[^>]+>", " ", html_src)


def test_a_new_credential_card_says_this_account_only_and_names_the_rest_as_grantable(db):
    from sentinel.dashboard import _portal_access_card

    here = _account(db, "alice-trading", ALICE)
    _account(db, "alice-cold", ALICE)
    card = _portal_access_card(db, here, ALICE)
    assert "opens this account only" in card
    assert "alice-cold" in _text(card) and "grant those from their own pages" in _text(card)


def test_an_existing_scoped_login_card_shows_what_it_opens_and_a_grant_control(db):
    from sentinel.dashboard import _portal_access_card

    a1 = _account(db, "a1", ALICE)
    a2 = _account(db, "a2", ALICE)
    set_member_password(db, ALICE, "pw-long-enough-1", grant_account_id=a1)
    card_a1 = _portal_access_card(db, a1, ALICE)
    assert "Remove access to this account" in card_a1
    card_a2 = _portal_access_card(db, a2, ALICE)
    assert "does not currently open this account" in _text(card_a2)
    assert "Grant this account" in card_a2


def _sign_in_ops(db, cipher, port):
    from sentinel.operators import create_operator
    from tests.test_operator_identity import _sign_in

    create_operator(db, "erwin", "correct-horse-battery")
    return _sign_in(db, cipher, port, "erwin", "correct-horse-battery")


def test_the_dashboard_grants_and_revokes_one_account_attributed(db, cipher):
    from sentinel.dashboard import serve_dashboard

    a1 = _account(db, "a1", ALICE)
    a2 = _account(db, "a2", ALICE)
    set_member_password(db, ALICE, "pw-long-enough-1", grant_account_id=a1)
    srv = serve_dashboard(db, cipher, 0)
    port = srv.server_address[1]
    try:
        cookie = _sign_in_ops(db, cipher, port)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/portal-grant",
                     urllib.parse.urlencode({"account_id": a2, "contact": "primary", "action": "grant"}),
                     {"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie})
        body = conn.getresponse().read().decode()
        assert "now opens this account" in body
        conn.request("POST", "/portal-grant",
                     urllib.parse.urlencode({"account_id": a1, "contact": "primary", "action": "revoke"}),
                     {"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie})
        body = conn.getresponse().read().decode()
        assert "no longer opens this account" in body
    finally:
        srv.shutdown()
        srv.server_close()
    assert _labels(member_accounts(db, ALICE)) == ["a2"]
    assert _events(db, a2)[-1] == ("member_account_granted", "erwin")
    assert _events(db, a1)[-1] == ("member_account_revoked", "erwin")


def test_generating_a_password_from_the_dashboard_grants_that_account_only(db, cipher):
    from sentinel.dashboard import serve_dashboard

    a1 = _account(db, "a1", ALICE)
    _account(db, "a2", ALICE)
    srv = serve_dashboard(db, cipher, 0)
    port = srv.server_address[1]
    try:
        cookie = _sign_in_ops(db, cipher, port)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/portal-access",
                     urllib.parse.urlencode({"account_id": a1, "contact": "primary"}),
                     {"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie})
        body = conn.getresponse().read().decode()
    finally:
        srv.shutdown()
        srv.server_close()
    assert "Member portal password" in body
    assert _labels(member_accounts(db, ALICE)) == ["a1"]
    with db.cursor() as cur:
        cur.execute("SELECT payload_json FROM events WHERE event_type = 'member_portal_password_generated'")
        payload = cur.fetchone()["payload_json"]
    assert '"granted_account_labels": ["a1"]' in payload


def test_an_existing_login_that_opens_only_this_account_gets_no_caution(db):
    """A caution shown every time is a caution nobody reads: an existing
    login scoped to exactly this account gets a plain reset button."""
    from sentinel.dashboard import _portal_access_card

    a1 = _account(db, "solo-1", ALICE)
    set_member_password(db, ALICE, "pw-long-enough-1", grant_account_id=a1)
    card = _portal_access_card(db, a1, ALICE)
    assert "not just this one" not in _text(card).lower()
    assert "opens" not in card.split('action="/portal-access"')[1].split("</form>")[0].lower()
    assert "Reset portal password" in card and "Remove access to this account" in card


def test_labels_an_existing_login_opens_are_escaped_on_the_card(db):
    from sentinel.dashboard import _portal_access_card

    here = _account(db, "<img src=x onerror=1>", ALICE)
    other = _account(db, "<script>y</script>", ALICE)
    set_member_password(db, ALICE, "pw-long-enough-1", grant_account_id=here)
    grant_account(db, ALICE, other)
    card = _portal_access_card(db, here, ALICE)
    assert "<script>" not in card and "<img src=x" not in card
    assert "&lt;script&gt;" in card
