"""Invitations: how a Minas member gets a login (decision 17, the owner
2026-10-28: "the signup is for minas members. also, adding keys should only
be done by admins during an onboarding process").

| Risk | Defence |
|---|---|
| A login nobody chose the password for, handed over by an unnamed channel | the member chooses it, from an emailed code |
| A half-finished invitation that somebody can sign in with | no `portal_members` row until the code, the password and a working two-factor code are all in |
| An invitation that opens more than it was issued for | a scoped login with a grant per invited account, each re-checked at acceptance (decision 10, 26a) |
| Guessing a code | 60 bits; five wrong codes retire the invitation in the database; the web's own limiter in front |
| Telling a customer's address from a stranger's | one answer, padded to one floor, for an unknown address and a wrong code |
| A database read yielding a working code | only the SHA-256 of the code is stored |
| An invitation somebody else used | the "login created" email to the address it belongs to |
"""
from __future__ import annotations

import http.client
import json
import re
import urllib.parse

import pytest

from sentinel import outbox
from sentinel.invitations import (
    CODE_ALPHABET,
    INVITATION_MAX_ATTEMPTS,
    InvitationRefused,
    check_code,
    complete_invitation,
    issue_invitation,
    make_step_token,
    normalize_code,
    open_invitation,
    send_invitation_email,
    store_password,
    verify_step_token,
)
from sentinel.portal import (
    make_pending_token,
    make_session_token,
    member_accounts,
    member_exists,
    set_member_password,
    verify_session_token,
)
from sentinel.totp import totp_at
from tests.conftest import PINNED_TOTP_NOW
from tests.helpers.api_client import add_account
from tests.helpers.landing_redirect import exchange

ALICE = "alice@example.com"
BOB = "bob@example.com"
PASSWORD = "a-long-enough-password"


# --- a mail provider that keeps what it is given ------------------------------

class _Mailbox:
    """In place of `sentinel.net.smtp`: accepts every message and keeps it."""

    messages: list = []

    def __init__(self, host, port, *, timeout):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, username, password):
        pass

    def send_message(self, msg):
        _Mailbox.messages.append(msg)


@pytest.fixture
def mailbox(monkeypatch):
    from sentinel import net

    monkeypatch.setenv("SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setenv("SMTP_SENDER", "guardian@example.invalid")
    monkeypatch.setenv("SMTP_USERNAME", "guardian")
    monkeypatch.setenv("SMTP_PASSWORD", "not-a-real-password")
    monkeypatch.setattr(net, "smtp", _Mailbox)
    monkeypatch.setattr(_Mailbox, "messages", [])
    return _Mailbox.messages


def _code_in(message) -> str:
    match = re.search(r"Invitation code: ([0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4})", message.get_content())
    assert match, message.get_content()
    return match.group(1)


def _row(db, invitation_id):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM member_invitations WHERE id = ?", (invitation_id,))
        return cur.fetchone()


# --- the code -----------------------------------------------------------------

def test_a_code_is_twelve_characters_from_the_unambiguous_alphabet(db):
    account = add_account(db, ALICE, "alice-1")
    code = issue_invitation(db, ALICE, account, actor="erwin").code
    groups = code.split("-")
    assert [len(g) for g in groups] == [4, 4, 4]
    assert all(ch in CODE_ALPHABET for ch in "".join(groups))
    assert not set("ILOU") & set(code), "letters a person misreads are never issued"


@pytest.mark.parametrize("typed, read", [
    ("7kqm-3xrp-9wtd", "7KQM3XRP9WTD"),
    (" 7KQM 3XRP 9WTD ", "7KQM3XRP9WTD"),
    ("7KQM-3XRP-9WTO", "7KQM3XRP9WT0"),   # O read as zero
    ("7KQM-3XRP-9WTI", "7KQM3XRP9WT1"),   # I read as one
    ("7KQM-3XRP-9WTL", "7KQM3XRP9WT1"),   # L read as one
])
def test_codes_are_read_the_way_people_type_them(typed, read):
    assert normalize_code(typed) == read


@pytest.mark.parametrize("typed", ["", "7KQM-3XRP", "7KQM-3XRP-9WTD-1", "7KQM-3XRP-9WT!", "7KQM-3XRP-9WTU"])
def test_anything_else_is_no_code_at_all(typed):
    assert normalize_code(typed) == ""


# --- issuing ------------------------------------------------------------------

def test_issuing_stores_the_hash_and_never_the_code(db):
    account = add_account(db, ALICE, "alice-1")
    invitation = issue_invitation(db, ALICE, account, actor="erwin")
    row = _row(db, invitation.id)
    stored = " ".join(str(v) for v in dict(row).values())
    assert normalize_code(invitation.code) not in stored and invitation.code not in stored
    assert row["email"] == ALICE and json.loads(row["account_ids_json"]) == [account]
    assert row["created_by"] == "erwin" and row["password_hash"] is None


def test_an_address_with_a_login_is_not_invited(db):
    account = add_account(db, ALICE, "alice-1")
    set_member_password(db, ALICE, PASSWORD)
    with pytest.raises(InvitationRefused, match="already has a login"):
        issue_invitation(db, ALICE, account, actor="erwin")


def test_an_address_that_is_not_a_contact_on_the_account_is_not_invited(db):
    """An invitation must never open an account to an address that is not
    on it - whatever the form posted."""
    bobs = add_account(db, BOB, "bob-1")
    with pytest.raises(InvitationRefused, match="not a contact"):
        issue_invitation(db, ALICE, bobs, actor="erwin")


def test_an_erased_account_is_not_invited_to(db):
    account = add_account(db, ALICE, "alice-1")
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET erased_at = '2026-01-02' WHERE id = ?", (account,))
    with pytest.raises(InvitationRefused):
        issue_invitation(db, ALICE, account, actor="erwin")


def test_the_emergency_contact_can_be_invited_to_the_account_they_are_on(db):
    account = add_account(db, BOB, "bob-1", secondary=ALICE)
    assert issue_invitation(db, ALICE, account, actor="erwin").account_ids == (account,)


def test_a_second_invitation_retires_the_first_and_carries_its_accounts(db):
    """Invited from two accounts' pages, the member holds one code that
    opens both - and the first code no longer works."""
    one = add_account(db, ALICE, "alice-1")
    two = add_account(db, ALICE, "alice-2")
    first = issue_invitation(db, ALICE, one, actor="erwin")
    second = issue_invitation(db, ALICE, two, actor="erwin")
    assert second.account_ids == (one, two)
    assert check_code(db, ALICE, first.code) is None
    assert check_code(db, ALICE, second.code) == second.id
    assert _row(db, first.id)["retired_at"] is not None


def test_issuing_leaves_an_entry_on_the_account(db):
    from sentinel.events import ADMIN_EVENT_LABELS, ADMIN_EVENT_TYPES

    account = add_account(db, ALICE, "alice-1")
    issue_invitation(db, ALICE, account, actor="erwin")
    with db.cursor() as cur:
        cur.execute("SELECT event_type, actor FROM events WHERE account_id = ?", (account,))
        assert [tuple(r) for r in cur.fetchall()] == [("member_invitation_sent", "erwin")]
    for event in ("member_invitation_sent", "member_invitation_accepted"):
        assert event in ADMIN_EVENT_TYPES and ADMIN_EVENT_LABELS[event]


# --- checking the code --------------------------------------------------------

def test_the_right_code_is_accepted_however_it_is_typed(db):
    account = add_account(db, ALICE, "alice-1")
    invitation = issue_invitation(db, ALICE, account, actor="erwin")
    assert check_code(db, "  Alice@Example.com ", invitation.code.lower().replace("-", " ")) == invitation.id


def test_a_wrong_code_counts_against_the_invitation(db):
    account = add_account(db, ALICE, "alice-1")
    invitation = issue_invitation(db, ALICE, account, actor="erwin")
    assert check_code(db, ALICE, "0000-0000-0000") is None
    assert _row(db, invitation.id)["attempts"] == 1
    assert check_code(db, ALICE, invitation.code) == invitation.id


def test_five_wrong_codes_retire_the_invitation_and_the_right_one_then_fails(db):
    account = add_account(db, ALICE, "alice-1")
    invitation = issue_invitation(db, ALICE, account, actor="erwin")
    for _ in range(INVITATION_MAX_ATTEMPTS):
        assert check_code(db, ALICE, "0000-0000-0000") is None
    assert _row(db, invitation.id)["retired_at"] is not None
    assert check_code(db, ALICE, invitation.code) is None
    assert open_invitation(db, ALICE) is None


def test_four_wrong_codes_do_not_retire_it(db):
    account = add_account(db, ALICE, "alice-1")
    invitation = issue_invitation(db, ALICE, account, actor="erwin")
    for _ in range(INVITATION_MAX_ATTEMPTS - 1):
        check_code(db, ALICE, "0000-0000-0000")
    assert check_code(db, ALICE, invitation.code) == invitation.id


def test_a_malformed_code_costs_the_member_nothing(db):
    """It cannot match, so counting it would retire an invitation nobody
    was attacking."""
    account = add_account(db, ALICE, "alice-1")
    invitation = issue_invitation(db, ALICE, account, actor="erwin")
    for _ in range(INVITATION_MAX_ATTEMPTS + 2):
        assert check_code(db, ALICE, "too-short") is None
    assert _row(db, invitation.id)["attempts"] == 0
    assert check_code(db, ALICE, invitation.code) == invitation.id


def test_an_expired_invitation_is_not_accepted(db):
    account = add_account(db, ALICE, "alice-1")
    invitation = issue_invitation(db, ALICE, account, actor="erwin")
    with db.cursor() as cur:
        cur.execute("UPDATE member_invitations SET expires_at = '2000-01-01T00:00:00+00:00'")
    assert check_code(db, ALICE, invitation.code) is None


def test_another_address_cannot_use_the_code(db):
    account = add_account(db, ALICE, "alice-1")
    invitation = issue_invitation(db, ALICE, account, actor="erwin")
    assert check_code(db, BOB, invitation.code) is None


# --- the step token -------------------------------------------------------------

SECRET = b"k" * 32


def test_a_step_token_names_its_invitation_and_address():
    token = make_step_token(SECRET, 7, ALICE)
    assert verify_step_token(SECRET, token) == (7, ALICE)


def test_a_tampered_or_foreign_token_is_nothing():
    token = make_step_token(SECRET, 7, ALICE)
    assert verify_step_token(b"x" * 32, token) is None
    assert verify_step_token(SECRET, token.replace(".7.", ".8.", 1)) is None
    assert verify_step_token(SECRET, make_session_token(SECRET, ALICE)) is None
    assert verify_step_token(SECRET, make_pending_token(SECRET, ALICE)) is None
    assert verify_session_token(SECRET, token) is None, "a step token is not a session"
    assert verify_step_token(SECRET, "") is None and verify_step_token(SECRET, None) is None


def test_a_step_token_expires(monkeypatch):
    import time as _time

    token = make_step_token(SECRET, 7, ALICE)
    real = _time.time
    monkeypatch.setattr(_time, "time", lambda: real() + 16 * 60)
    assert verify_step_token(SECRET, token) is None


# --- completing -----------------------------------------------------------------

def _accepted_up_to_the_password(db, *accounts):
    for account in accounts:
        invitation = issue_invitation(db, ALICE, account, actor="erwin")
    store_password(db, invitation.id, PASSWORD)
    return invitation


def test_completing_makes_a_scoped_login_that_opens_only_the_invited_accounts(db):
    invited = add_account(db, ALICE, "alice-1")
    add_account(db, ALICE, "alice-other")  # Alice is a contact here too, but was not invited to it
    invitation = _accepted_up_to_the_password(db, invited)
    assert not member_exists(db, ALICE), "no login before the last step"
    assert complete_invitation(db, invitation.id, ALICE) == [invited]
    assert [r["label"] for r in member_accounts(db, ALICE)] == ["alice-1"]
    from sentinel.portal import check_member_login, is_scoped

    assert is_scoped(db, ALICE) and check_member_login(db, ALICE, PASSWORD)
    row = _row(db, invitation.id)
    assert row["used_at"] is not None and row["password_hash"] is None
    with db.cursor() as cur:
        cur.execute("SELECT granted_by FROM member_account_grants WHERE email = ?", (ALICE,))
        assert [r["granted_by"] for r in cur.fetchall()] == ["erwin"]
        cur.execute("SELECT actor FROM events WHERE event_type = 'member_invitation_accepted'")
        assert [r["actor"] for r in cur.fetchall()] == [ALICE]


def test_an_account_the_address_has_left_is_not_opened(db):
    """A contact move between the email and the acceptance takes the
    account off the invitation (26a)."""
    stays = add_account(db, ALICE, "alice-1")
    moves = add_account(db, ALICE, "alice-2")
    invitation = _accepted_up_to_the_password(db, stays, moves)
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = ? WHERE id = ?", (BOB, moves))
    assert complete_invitation(db, invitation.id, ALICE) == [stays]


def test_an_invitation_whose_accounts_all_moved_makes_no_login(db):
    account = add_account(db, ALICE, "alice-1")
    invitation = _accepted_up_to_the_password(db, account)
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = ? WHERE id = ?", (BOB, account))
    with pytest.raises(InvitationRefused):
        complete_invitation(db, invitation.id, ALICE)
    assert not member_exists(db, ALICE)


def test_completing_needs_the_password_step(db):
    account = add_account(db, ALICE, "alice-1")
    invitation = issue_invitation(db, ALICE, account, actor="erwin")
    with pytest.raises(InvitationRefused):
        complete_invitation(db, invitation.id, ALICE)
    assert not member_exists(db, ALICE)


def test_an_invitation_completes_once(db):
    account = add_account(db, ALICE, "alice-1")
    invitation = _accepted_up_to_the_password(db, account)
    complete_invitation(db, invitation.id, ALICE)
    with pytest.raises(InvitationRefused):
        complete_invitation(db, invitation.id, ALICE)


def test_a_login_made_meanwhile_stops_the_completion(db):
    """A second tab can finish first, or Minas can generate a password in
    between: the invitation does not overwrite a login that exists."""
    account = add_account(db, ALICE, "alice-1")
    invitation = _accepted_up_to_the_password(db, account)
    set_member_password(db, ALICE, "a-different-password")
    with pytest.raises(InvitationRefused, match="already has a login"):
        complete_invitation(db, invitation.id, ALICE)


def test_an_invitation_that_can_still_be_accepted_is_never_pruned(db, rules):
    """Its window runs from `expires_at`: a code lives days, longer than
    the shortest window the retention setting allows."""
    from sentinel.retention import prune_all

    account = add_account(db, ALICE, "alice-1")
    invitation = issue_invitation(db, ALICE, account, actor="erwin")
    with db.cursor() as cur:
        cur.execute("UPDATE member_invitations SET created_at = '2000-01-01T00:00:00+00:00'")
    prune_all(db, rules)
    assert check_code(db, ALICE, invitation.code) == invitation.id


def test_erasure_deletes_the_persons_invitations_rather_than_redacting_them(db):
    """Their own record, like their reset tokens: gone, not left behind
    with [erased] in it - the generic scan of other people's records would
    otherwise hide a missing entry in erasure's list."""
    from sentinel.erasure import erase_member

    account = add_account(db, ALICE, "alice-1")
    issue_invitation(db, ALICE, account, actor="erwin")
    erase_member(db, ALICE, actor=ALICE, notify=lambda *a: None)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM member_invitations")
        assert cur.fetchone()["n"] == 0


# --- the web flow ---------------------------------------------------------------

@pytest.fixture
def portal(db, cipher):
    from sentinel.portal import serve_portal

    srv = serve_portal(db, 0, host="127.0.0.1", cipher=cipher)
    yield "127.0.0.1", srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _hidden(body, name):
    match = re.search(rf'name="{name}" value="([^"]*)"', body)
    assert match, f"no hidden {name} in the page"
    return match.group(1)


def _secret(body):
    match = re.search(r'id="totp-secret"[^>]*>([A-Z2-7]+)<', body)
    assert match, "no secret on the page"
    return match.group(1)


def test_the_welcome_page_is_public(portal):
    status, _, body = exchange(portal, "GET", "/welcome")
    assert status == 200
    assert 'id="welcome-title">Accept your invitation<' in body
    assert 'name="email"' in body and 'name="code"' in body and 'action="/welcome"' in body


def test_the_landing_and_sign_in_pages_lead_to_it(portal):
    _, _, landing = exchange(portal, "GET", "/")
    assert '<a class="btn btn--secondary" href="/welcome">Accept an invitation</a>' in landing
    _, _, sign_in = exchange(portal, "GET", "/login")
    assert '<a href="/welcome">Accept an invitation</a>' in sign_in


def test_the_whole_flow_makes_the_login_only_at_the_end_and_signs_the_member_in(portal, db, mailbox):
    account = add_account(db, ALICE, "alice-1")
    invitation = issue_invitation(db, ALICE, account, actor="erwin")

    status, _, body = exchange(portal, "POST", "/welcome", {"step": "code", "email": ALICE,
                                                          "code": invitation.code})
    assert status == 200 and "Choose a password" in body
    token = _hidden(body, "token")
    assert not member_exists(db, ALICE)

    status, _, body = exchange(portal, "POST", "/welcome", {"step": "password", "token": token,
                                                          "password": PASSWORD})
    assert status == 200 and "Set up two-factor sign-in" in body
    assert 'class="btn btn--secondary" href="otpauth://totp/' in body, "tappable on the phone reading it"
    secret = _secret(body)
    assert not member_exists(db, ALICE), "a chosen password is not a login yet"

    status, message, body = exchange(portal, "POST", "/welcome", {
        "step": "twofactor", "token": _hidden(body, "token"), "code": totp_at(secret, PINNED_TOTP_NOW)})
    assert status == 200 and "Your login is ready" in body and "Save your recovery codes" in body
    assert member_exists(db, ALICE)
    from sentinel.twofactor import MEMBER, is_enabled

    assert is_enabled(db, MEMBER, ALICE)
    cookie = message.get("Set-Cookie").split("minas_portal=", 1)[1].split(";", 1)[0]
    status, _, home = exchange(portal, "GET", "/", cookie=cookie)
    assert status == 200 and "alice-1" in home
    with db.cursor() as cur:
        cur.execute("SELECT event_type FROM events WHERE account_id = ? ORDER BY id", (account,))
        events = [r["event_type"] for r in cur.fetchall()]
    assert events == ["member_invitation_sent", "member_invitation_accepted", "member_two_factor_enabled"]

    assert outbox.wait_until_sent(10)
    [notice] = [m for m in mailbox if m["To"] == ALICE]
    assert notice["Subject"] == "Your Minas AI Guardian login was created"
    assert "If this was not you" in notice.get_content()


def test_a_wrong_code_and_an_unknown_address_get_the_same_answer(portal, db):
    account = add_account(db, ALICE, "alice-1")
    issue_invitation(db, ALICE, account, actor="erwin")
    wrong = exchange(portal, "POST", "/welcome", {"step": "code", "email": ALICE, "code": "0000-0000-0000"})
    unknown = exchange(portal, "POST", "/welcome", {"step": "code", "email": "nobody@example.com",
                                                    "code": "0000-0000-0000"})
    assert wrong[0] == unknown[0] == 400

    def answer(body):
        return re.search(r'notice__title">([^<]*)<', body).group(1)

    assert answer(wrong[2]) == answer(unknown[2]) and "match an open invitation" in answer(wrong[2])


def test_the_web_limiter_stops_a_guesser_before_the_invitation_is_spent(portal, db):
    account = add_account(db, ALICE, "alice-1")
    invitation = issue_invitation(db, ALICE, account, actor="erwin")
    statuses = [exchange(portal, "POST", "/welcome", {"step": "code", "email": ALICE,
                                                     "code": "0000-0000-0000"})[0]
                for _ in range(6)]
    assert statuses[:5] == [400] * 5 and statuses[5] == 429
    # The fifth wrong code also retired the invitation itself: the database
    # is the limit that holds across sources and restarts.
    assert _row(db, invitation.id)["retired_at"] is not None


def test_a_forged_step_token_goes_back_to_the_first_step(portal, db):
    account = add_account(db, ALICE, "alice-1")
    issue_invitation(db, ALICE, account, actor="erwin")
    status, _, body = exchange(portal, "POST", "/welcome", {"step": "password", "token": "invite.1.x.9.y",
                                                          "password": PASSWORD})
    assert status == 400 and "Accept your invitation" in body and "took too long" in body


def test_a_token_for_a_replaced_invitation_goes_back_to_the_first_step(portal, db):
    one = add_account(db, ALICE, "alice-1")
    two = add_account(db, ALICE, "alice-2")
    first = issue_invitation(db, ALICE, one, actor="erwin")
    _, _, body = exchange(portal, "POST", "/welcome", {"step": "code", "email": ALICE, "code": first.code})
    token = _hidden(body, "token")
    issue_invitation(db, ALICE, two, actor="erwin")  # Minas sends a new one meanwhile
    status, _, body = exchange(portal, "POST", "/welcome", {"step": "password", "token": token,
                                                          "password": PASSWORD})
    assert status == 400 and "took too long" in body


def test_a_short_password_is_refused_and_nothing_is_stored(portal, db):
    account = add_account(db, ALICE, "alice-1")
    invitation = issue_invitation(db, ALICE, account, actor="erwin")
    _, _, body = exchange(portal, "POST", "/welcome", {"step": "code", "email": ALICE, "code": invitation.code})
    _, _, body = exchange(portal, "POST", "/welcome", {"step": "password", "token": _hidden(body, "token"),
                                                     "password": "short"})
    assert "Use at least 12 characters." in body and "Choose a password" in body
    assert _row(db, invitation.id)["password_hash"] is None


def test_a_wrong_two_factor_code_makes_no_login_and_keeps_the_same_secret(portal, db):
    account = add_account(db, ALICE, "alice-1")
    invitation = issue_invitation(db, ALICE, account, actor="erwin")
    _, _, body = exchange(portal, "POST", "/welcome", {"step": "code", "email": ALICE, "code": invitation.code})
    _, _, body = exchange(portal, "POST", "/welcome", {"step": "password", "token": _hidden(body, "token"),
                                                     "password": PASSWORD})
    secret = _secret(body)
    status, _, again = exchange(portal, "POST", "/welcome", {"step": "twofactor", "token": _hidden(body, "token"),
                                                           "code": "000000"})
    assert status == 400 and "That code was not accepted" in again
    assert _secret(again) == secret, "a mistyped code must not make the member scan a new secret"
    assert not member_exists(db, ALICE)


# --- the dashboard ----------------------------------------------------------------

def _dashboard_post(port, cookie, path, fields):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", path, urllib.parse.urlencode(fields),
                 {"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie})
    body = conn.getresponse().read().decode()
    conn.close()
    return body


@pytest.fixture
def dashboard(db, cipher):
    from sentinel.dashboard import serve_dashboard
    from tests.test_member_account_grants import _sign_in_ops

    srv = serve_dashboard(db, cipher, 0)
    port = srv.server_address[1]
    yield port, _sign_in_ops(db, cipher, port)
    srv.shutdown()
    srv.server_close()


def test_the_account_page_offers_an_invitation_to_a_contact_without_a_login(db):
    from sentinel.dashboard import _portal_access_card

    account = add_account(db, ALICE, "alice-1")
    card = _portal_access_card(db, account, ALICE)
    assert 'action="/portal-invite"' in card and "Send invitation" in card
    assert "no invitation sent yet" in card
    assert "Generate a portal password instead" in card, "the generated password stays as the fallback"
    issue_invitation(db, ALICE, account, actor="erwin")
    assert "invitation sent" in _portal_access_card(db, account, ALICE)


def test_a_contact_with_a_login_gets_no_invitation_button(db):
    from sentinel.dashboard import _portal_access_card

    account = add_account(db, ALICE, "alice-1")
    set_member_password(db, ALICE, PASSWORD, grant_account_id=account)
    card = _portal_access_card(db, account, ALICE)
    assert "/portal-invite" not in card and "Reset portal password" in card


def test_sending_an_invitation_from_the_dashboard_emails_a_code_that_works(db, dashboard, mailbox):
    port, cookie = dashboard
    account = add_account(db, ALICE, "alice-1")
    body = _dashboard_post(port, cookie, "/portal-invite", {"account_id": account, "contact": "primary"})
    assert "Invitation emailed to <b>alice@example.com</b>" in body
    [message] = mailbox
    assert message["To"] == ALICE and message["Subject"] == "Your invitation to Minas AI Guardian"
    text = message.get_content()
    assert "http" not in text, "a code, never a link (decision 16)"
    assert check_code(db, ALICE, _code_in(message)) is not None
    with db.cursor() as cur:
        cur.execute("SELECT actor FROM events WHERE event_type = 'member_invitation_sent'")
        assert [r["actor"] for r in cur.fetchall()] == ["erwin"]


def test_the_invitation_email_goes_to_the_mailbox_only(db, mailbox, monkeypatch):
    """Like the reset link (26c): whoever holds a connected Telegram chat
    must not be able to make a login with it."""
    import sentinel.alerts.contact as contact
    from sentinel.portal import _PlainNotice

    seen = []
    real_send = contact.ContactRouter.send

    def spy(self, detection, *args, **kwargs):
        seen.append(detection)
        return real_send(self, detection, *args, **kwargs)

    monkeypatch.setattr(contact.ContactRouter, "send", spy)
    account = add_account(db, ALICE, "alice-1")
    invitation = issue_invitation(db, ALICE, account, actor="erwin")
    assert send_invitation_email(db, invitation) == "accepted"
    assert isinstance(seen[0], _PlainNotice) and seen[0].email_only is True


def test_with_no_email_channel_the_operator_is_told_it_was_not_sent(db, dashboard):
    port, cookie = dashboard
    account = add_account(db, ALICE, "alice-1")
    body = _dashboard_post(port, cookie, "/portal-invite", {"account_id": account, "contact": "primary"})
    assert "was made but NOT sent" in body and 'class="banner err"' in body


def test_inviting_an_address_with_a_login_is_refused_on_the_page(db, dashboard, mailbox):
    port, cookie = dashboard
    account = add_account(db, ALICE, "alice-1")
    set_member_password(db, ALICE, PASSWORD, grant_account_id=account)
    body = _dashboard_post(port, cookie, "/portal-invite", {"account_id": account, "contact": "primary"})
    assert "No invitation sent" in body and "already has a login" in body
    assert mailbox == []


def test_the_emergency_contact_is_invited_from_their_row(db, dashboard, mailbox):
    port, cookie = dashboard
    account = add_account(db, BOB, "bob-1", secondary=ALICE)
    body = _dashboard_post(port, cookie, "/portal-invite", {"account_id": account, "contact": "secondary"})
    assert "Invitation emailed to <b>alice@example.com</b>" in body
    assert [m["To"] for m in mailbox] == [ALICE]


def test_adding_a_customer_invites_the_primary_contact(db, dashboard, mailbox, monkeypatch):
    from tests.test_add_customer import _mock_readonly_client

    monkeypatch.setattr("sentinel.onboarding.make_client", _mock_readonly_client)
    port, cookie = dashboard
    body = _dashboard_post(port, cookie, "/add", {
        "name": "Jane Doe", "exchange": "bitget", "topology": "independent", "email": "jane@x.com",
        "passphrase": "p", "api_key": "k", "secret": "s"})
    assert "Verified read-only and added" in body and "Invitation emailed to <b>jane@x.com</b>" in body
    [message] = [m for m in mailbox if m["Subject"] == "Your invitation to Minas AI Guardian"]
    assert message["To"] == "jane@x.com"
    with db.cursor() as cur:
        cur.execute("SELECT id FROM accounts WHERE customer_name = 'Jane Doe'")
        account = cur.fetchone()["id"]
    assert json.loads(open_invitation(db, "jane@x.com")["account_ids_json"]) == [account]


def test_adding_a_customer_whose_address_has_a_login_sends_nothing(db, dashboard, mailbox, monkeypatch):
    from tests.test_add_customer import _mock_readonly_client

    monkeypatch.setattr("sentinel.onboarding.make_client", _mock_readonly_client)
    set_member_password(db, "jane@x.com", PASSWORD)
    port, cookie = dashboard
    body = _dashboard_post(port, cookie, "/add", {
        "name": "Jane Doe", "exchange": "bitget", "topology": "independent", "email": "jane@x.com",
        "passphrase": "p", "api_key": "k", "secret": "s"})
    assert "already has a login" in body
    assert not [m for m in mailbox if m["Subject"] == "Your invitation to Minas AI Guardian"]
