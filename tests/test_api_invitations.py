"""Accepting an invitation, and setting up two-factor, from the app
(decision 17, 2026-10-28; decision 21c).

The web's /welcome step for step, on the same functions in
sentinel/invitations.py and in the portal's words: the code, a password,
then two-factor - and only then the login, with this phone signed in to it.
And the dead end the app had since 21c, closed: a member with a password
and no second factor can set one up here, authorised by the email and
password, as the portal's Security page is after a password sign-in.
"""
from __future__ import annotations

import pytest

from sentinel import api as api_module
from sentinel import outbox
from sentinel.invitations import CODE_NOT_ACCEPTED, issue_invitation
from sentinel.portal import WELCOME_NO_MATCH, WELCOME_STEP_EXPIRED, member_exists, serve_portal, set_member_password
from sentinel.totp import totp_at
from sentinel.twofactor import MEMBER, is_enabled
from tests.conftest import PINNED_TOTP_NOW
from tests.helpers.api_client import PASSWORD, ApiClient, add_account
from tests.helpers.landing_redirect import exchange

CAROL = "carol@example.com"
BOB = "bob@example.com"


@pytest.fixture
def server(db, cipher, monkeypatch):
    monkeypatch.setattr(api_module, "send_new_device_email", lambda db, address, name: None)
    srv = serve_portal(db, 0, host="127.0.0.1", cipher=cipher)
    client = ApiClient(srv.server_address[1])
    client.host_port = ("127.0.0.1", srv.server_address[1])
    yield client
    srv.shutdown()
    srv.server_close()


def _code(server, email, code):
    return server.request("POST", "/api/v1/invitation/code", {"email": email, "code": code})


def _password(server, token, password=PASSWORD):
    return server.request("POST", "/api/v1/invitation/password", {"step_token": token, "password": password})


def _finish(server, token, code, device="Carol's Pixel"):
    return server.request("POST", "/api/v1/invitation/two-factor",
                          {"step_token": token, "code": code, "device_name": device})


def _up_to_two_factor(server, db):
    account = add_account(db, CAROL, "carol-1")
    invitation = issue_invitation(db, CAROL, account, actor="erwin")
    status, step, _ = _code(server, CAROL, invitation.code)
    assert status == 200 and step["email"] == CAROL
    status, setup, _ = _password(server, step["step_token"])
    assert status == 200
    return account, setup


# --- accepting an invitation -----------------------------------------------------

def test_the_whole_flow_makes_the_login_at_the_end_and_signs_this_phone_in(server, db):
    account, setup = _up_to_two_factor(server, db)
    assert setup["otpauth_uri"].startswith("otpauth://totp/") and f"secret={setup['secret']}" in setup["otpauth_uri"]
    assert not member_exists(db, CAROL), "a chosen password is not a login yet"

    status, done, _ = _finish(server, setup["step_token"], totp_at(setup["secret"], PINNED_TOTP_NOW))
    assert status == 200
    assert len(done["recovery_codes"]) >= 8 and done["token_type"] == "Bearer"
    assert member_exists(db, CAROL) and is_enabled(db, MEMBER, CAROL)
    status, accounts, _ = server.request("GET", "/api/v1/accounts", token=done["access_token"])
    assert status == 200 and [a["id"] for a in accounts["accounts"]] == [account]
    with db.cursor() as cur:
        cur.execute("SELECT name FROM api_devices WHERE email = ?", (CAROL,))
        assert [r["name"] for r in cur.fetchall()] == ["Carol's Pixel"]
        cur.execute("SELECT event_type FROM events WHERE account_id = ? ORDER BY id", (account,))
        assert [r["event_type"] for r in cur.fetchall()] == [
            "member_invitation_sent", "member_invitation_accepted", "member_two_factor_enabled",
            "member_app_signed_in"]
    assert outbox.wait_until_sent(10)


def test_a_wrong_code_and_an_unknown_address_get_the_portals_one_answer(server, db):
    account = add_account(db, CAROL, "carol-1")
    issue_invitation(db, CAROL, account, actor="erwin")
    wrong = _code(server, CAROL, "0000-0000-0000")
    unknown = _code(server, "nobody@example.com", "0000-0000-0000")
    assert wrong[0] == unknown[0] == 400
    assert wrong[1] == unknown[1] == {"error": "invitation_not_accepted", "message": WELCOME_NO_MATCH}


def test_the_web_and_the_app_share_one_invitation_limiter(server, db):
    """A guesser cannot double the budget by alternating the website and
    the API."""
    account = add_account(db, CAROL, "carol-1")
    issue_invitation(db, CAROL, account, actor="erwin")
    for _ in range(3):
        status, _, _ = exchange(server.host_port, "POST", "/welcome",
                                {"step": "code", "email": "x@example.com", "code": "0000-0000-0000"})
        assert status == 400
    for _ in range(2):
        assert _code(server, "y@example.com", "0000-0000-0000")[0] == 400
    assert _code(server, "z@example.com", "0000-0000-0000")[0] == 429


def test_a_forged_step_token_is_an_expired_step(server, db):
    status, body, _ = _password(server, "invite.1.x.9.y")
    assert status == 400 and body == {"error": "step_expired", "message": WELCOME_STEP_EXPIRED}
    status, body, _ = _finish(server, "made-up", "123456")
    assert status == 400 and body["error"] == "step_expired"


def test_a_short_password_is_refused(server, db):
    account = add_account(db, CAROL, "carol-1")
    invitation = issue_invitation(db, CAROL, account, actor="erwin")
    _, step, _ = _code(server, CAROL, invitation.code)
    status, body, _ = _password(server, step["step_token"], "short")
    assert status == 400 and body["error"] == "password_too_short"


def test_a_wrong_two_factor_code_makes_no_login(server, db):
    _, setup = _up_to_two_factor(server, db)
    status, body, _ = _finish(server, setup["step_token"], "000000")
    assert status == 400 and body == {"error": "code_not_accepted", "message": CODE_NOT_ACCEPTED}
    assert not member_exists(db, CAROL)
    # The secret is unchanged: the app keeps showing the one already scanned.
    status, done, _ = _finish(server, setup["step_token"], totp_at(setup["secret"], PINNED_TOTP_NOW))
    assert status == 200


def test_an_invitation_finished_on_the_web_cannot_be_finished_again_here(server, db, cipher):
    """Two surfaces, one invitation: whichever finishes first makes the
    login, and the other's step token then names an invitation that is no
    longer open."""
    from sentinel.invitations import finish_invitation, open_invitation

    _, setup = _up_to_two_factor(server, db)
    row = open_invitation(db, CAROL)
    finish_invitation(db, cipher, row["id"], CAROL, totp_at(setup["secret"], PINNED_TOTP_NOW))
    status, body, _ = _finish(server, setup["step_token"], totp_at(setup["secret"], PINNED_TOTP_NOW + 30))
    assert status == 400 and body["error"] == "step_expired"


def test_without_a_cipher_two_factor_cannot_be_set_up(db, monkeypatch):
    monkeypatch.setattr(api_module, "send_new_device_email", lambda db, address, name: None)
    srv = serve_portal(db, 0, host="127.0.0.1")
    try:
        client = ApiClient(srv.server_address[1])
        account = add_account(db, CAROL, "carol-1")
        invitation = issue_invitation(db, CAROL, account, actor="erwin")
        _, step, _ = client.request("POST", "/api/v1/invitation/code", {"email": CAROL, "code": invitation.code})
        status, body, _ = client.request("POST", "/api/v1/invitation/password",
                                         {"step_token": step["step_token"], "password": PASSWORD})
        assert status == 503 and body["error"] == "two_factor_unavailable"
    finally:
        srv.shutdown()
        srv.server_close()


# --- two-factor before a first app sign-in -----------------------------------------

def test_a_member_with_a_password_sets_up_two_factor_and_is_signed_in(server, db):
    add_account(db, BOB, "bob-1")
    set_member_password(db, BOB, PASSWORD, notify=None)
    status, refused, _ = server.request("POST", "/api/v1/session", {"email": BOB, "password": PASSWORD, "code": ""})
    assert status == 403 and refused["error"] == "two_factor_required", "the dead end this closes"

    status, started, _ = server.request("POST", "/api/v1/two-factor/start", {"email": BOB, "password": PASSWORD})
    assert status == 200 and started["secret"]
    assert not is_enabled(db, MEMBER, BOB), "not on until a code works"
    status, done, _ = server.request("POST", "/api/v1/two-factor/confirm", {
        "email": BOB, "password": PASSWORD, "code": totp_at(started["secret"], PINNED_TOTP_NOW),
        "device_name": "Bob's phone"})
    assert status == 200 and done["recovery_codes"] and done["access_token"]
    assert is_enabled(db, MEMBER, BOB)
    status, _, _ = server.request("GET", "/api/v1/accounts", token=done["access_token"])
    assert status == 200


def test_setting_up_two_factor_needs_the_right_password(server, db):
    set_member_password(db, BOB, PASSWORD, notify=None)
    status, body, _ = server.request("POST", "/api/v1/two-factor/start", {"email": BOB, "password": "not-it-at-all"})
    assert status == 401 and body["error"] == "invalid_credentials"
    status, body, _ = server.request("POST", "/api/v1/two-factor/start",
                                     {"email": "nobody@example.com", "password": PASSWORD})
    assert status == 401 and body["error"] == "invalid_credentials"
    status, body, _ = server.request("POST", "/api/v1/two-factor/confirm",
                                     {"email": BOB, "password": "not-it-at-all", "code": "123456"})
    assert status == 401
    assert not is_enabled(db, MEMBER, BOB)


def test_two_factor_that_is_on_is_never_replaced_here(server, db, cipher):
    from tests.helpers.api_client import enrolled_member

    enrolled_member(db, cipher, BOB)
    status, body, _ = server.request("POST", "/api/v1/two-factor/start", {"email": BOB, "password": PASSWORD})
    assert status == 409 and body["error"] == "two_factor_already_on"


def test_wrong_passwords_here_count_on_the_sign_in_limiter(server, db):
    set_member_password(db, BOB, PASSWORD, notify=None)
    for _ in range(5):
        server.request("POST", "/api/v1/two-factor/start", {"email": BOB, "password": "not-it-at-all"})
    status, body, _ = server.request("POST", "/api/v1/session", {"email": BOB, "password": PASSWORD, "code": "1"})
    assert status == 429, "a guesser cannot move from the sign-in to the setup for a fresh budget"
