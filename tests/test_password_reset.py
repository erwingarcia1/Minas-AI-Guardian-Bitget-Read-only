"""Resetting a forgotten portal password (G-29, #33).

A password-reset flow is the single most attacked surface on any product that
has one, because it is an *authentication bypass by design*: it hands out
access to someone who cannot prove who they are. Everything below is organised
by the attack it defends against rather than by the criterion it satisfies,
because the criteria do not name most of them.

| Attack | Defence |
|---|---|
| Enumerate customers by asking for resets | identical response *and* identical timing whether the address exists |
| Steal a token from a database read, a log, or a backup | only the **hash** of the token is stored; the plaintext exists once, in one email |
| Reuse a link found in a mailbox months later | single-use, and one hour to live |
| Keep a session obtained before the compromise was noticed | resetting bumps a per-member epoch that invalidates every existing cookie |
| Reset someone else's password with your own token | the token is bound to the email and the binding is checked, not assumed |
| Have a password changed without the owner noticing | every change emails the owner, including one they performed themselves |
| Brute-force the token | 32 bytes of `secrets`, compared with `compare_digest` |
| Request thousands of resets to spam a customer or bury a real alert | the same throttle the login path uses |

The criterion "resetting signs out all other sessions" is the one that forced a
design change. Portal sessions are stateless HMAC cookies with no server-side
record, so there was nothing to invalidate. `portal_members.session_epoch` is
that record: it is baked into every token and checked on every request, so
bumping it retires every cookie ever issued to that member.
"""
from __future__ import annotations

import time

import pytest

from sentinel.portal import (
    consume_reset_token,
    create_reset_token,
    make_session_token,
    set_member_password,
    verify_session_token,
)
from tests.helpers.landing_redirect import exchange

EMAIL = "alice@example.com"


@pytest.fixture
def member(db):
    set_member_password(db, EMAIL, "old-password")
    return EMAIL


def _epoch(db, email):
    with db.cursor() as cur:
        cur.execute("SELECT session_epoch FROM portal_members WHERE email = ?", (email,))
        row = cur.fetchone()
        return row["session_epoch"] if row else None


# --- the token itself ---------------------------------------------------------


def test_a_token_works_once(db, member):
    token = create_reset_token(db, member)

    assert consume_reset_token(db, token) == member
    assert consume_reset_token(db, token) is None


def test_an_expired_token_is_refused(db, member):
    token = create_reset_token(db, member, ttl_seconds=1)
    with db.cursor() as cur:  # wind it past expiry rather than sleeping
        cur.execute("UPDATE password_resets SET expires_at = '2000-01-01T00:00:00+00:00'")

    assert consume_reset_token(db, token) is None


def test_the_lifetime_is_capped_at_an_hour(db, member):
    """"Expires within an hour" is a ceiling, not a suggestion. A caller
    passing a longer TTL - a future convenience feature, a test, a mistake -
    must not be able to mint a link that outlives the criterion."""
    from datetime import datetime, timedelta, timezone

    before = datetime.now(timezone.utc)
    create_reset_token(db, member, ttl_seconds=86_400 * 30)

    with db.cursor() as cur:
        cur.execute("SELECT expires_at FROM password_resets ORDER BY id DESC LIMIT 1")
        expires = datetime.fromisoformat(cur.fetchone()["expires_at"])
    assert expires <= before + timedelta(hours=1, seconds=5)


def test_the_plaintext_token_is_never_stored(db, member):
    """A database read, a backup, or a stray log line must not yield a working
    link. The token is a bearer credential for exactly as long as it lives, so
    it is stored the way passwords are."""
    token = create_reset_token(db, member)

    with db.cursor() as cur:
        cur.execute("SELECT * FROM password_resets")
        rows = [dict(r) for r in cur.fetchall()]
    assert rows
    for row in rows:
        for value in row.values():
            assert token not in str(value)


def test_a_forged_token_is_refused(db, member):
    create_reset_token(db, member)

    assert consume_reset_token(db, "not-a-real-token") is None
    assert consume_reset_token(db, "") is None


def test_a_token_is_bound_to_the_address_that_asked_for_it(db, member):
    """Two people requesting resets at once must not be able to redeem each
    other's link. The email comes back *from the token record*, never from the
    form."""
    set_member_password(db, "bob@example.com", "bob-password")
    alice_token = create_reset_token(db, EMAIL)
    bob_token = create_reset_token(db, "bob@example.com")

    assert consume_reset_token(db, alice_token) == EMAIL
    assert consume_reset_token(db, bob_token) == "bob@example.com"


def test_requesting_a_new_token_retires_the_old_one(db, member):
    """A customer who clicks "forgot password" three times has three live
    links in their mailbox, each a standing bearer credential. Only the newest
    should work — and the older ones are exactly what an attacker with brief
    mailbox access would come back for."""
    first = create_reset_token(db, member)
    second = create_reset_token(db, member)

    assert consume_reset_token(db, first) is None
    assert consume_reset_token(db, second) == member


# --- customer enumeration -----------------------------------------------------


def test_a_token_is_issued_for_an_unknown_address_too(db):
    """Not stored against anybody, and never emailed anywhere — but the code
    path must run to completion so the *timing* of an unknown address matches
    a known one. The login page already does this with a dummy scrypt hash;
    this is the same defence on the same reasoning.

    Without it, "which of these 10,000 addresses is a Minas customer" is a
    stopwatch away — and a Minas customer is by definition someone holding
    crypto worth monitoring."""
    result = create_reset_token(db, "nobody@example.com")

    assert result is None  # nothing to send
    with db.cursor() as cur:
        cur.execute("SELECT count(*) c FROM password_resets")
        assert cur.fetchone()["c"] == 0


def test_the_forgot_endpoint_takes_the_same_time_either_way(portal, db, member):
    """Identical wording is not enough on its own.

    Issuing a token writes twice and sends mail; an unknown address does
    neither. Without equalisation a stopwatch answers the question the wording
    refuses to — and the answer identifies somebody holding crypto worth
    monitoring. Both paths are padded to a common floor.

    Measured at the endpoint, because that is where the timing is observable.
    A unit-level measurement of `create_reset_token` would pass while the
    thing an attacker actually times still leaked."""
    def _elapsed(email):
        start = time.perf_counter()
        _post(portal, "/forgot", {"email": email})
        return time.perf_counter() - start

    known = min(_elapsed(member) for _ in range(3))
    unknown = min(_elapsed("nobody@example.com") for _ in range(3))

    ratio = max(known, unknown) / min(known, unknown)
    assert ratio < 1.5, f"known={known:.4f}s unknown={unknown:.4f}s ratio={ratio:.2f}"


@pytest.mark.parametrize("email", ["alice@example.com", "nobody@example.com"])
def test_every_forgot_response_waits_for_the_floor(portal, db, member, email):
    """The ratio test above cannot see the padding disappear.

    On a local SQLite database with no SMTP configured, the real difference
    between issuing a token and not issuing one is already under the ratio's
    tolerance — so removing the equaliser leaves that test green. It passes
    for the right reason today and would keep passing on a machine where the
    difference is large.

    This one pins the mechanism instead of the symptom: both paths wait for a
    common floor, so the defence holds on the slow production database where
    the ratio would otherwise widen."""
    from sentinel.portal import _RESET_RESPONSE_FLOOR_SECONDS

    start = time.perf_counter()
    _post(portal, "/forgot", {"email": email})
    elapsed = time.perf_counter() - start

    assert elapsed >= _RESET_RESPONSE_FLOOR_SECONDS * 0.9, f"{elapsed:.4f}s"


# --- what a reset does to existing sessions -----------------------------------


def test_resetting_invalidates_every_existing_session(db, member):
    """The criterion that forced a design change.

    Portal sessions are stateless HMAC cookies — nothing server-side to
    delete. `session_epoch` is that missing record: it is baked into the token
    and checked on every request, so bumping it retires every cookie ever
    issued to this member. Without it, a customer resetting their password
    because they think they have been compromised leaves the attacker's
    session working."""
    secret = b"s" * 32
    old_cookie = make_session_token(secret, member, epoch=_epoch(db, member))
    assert verify_session_token(secret, old_cookie, epoch=_epoch(db, member)) == member

    token = create_reset_token(db, member)
    email = consume_reset_token(db, token)
    set_member_password(db, email, "new-password", invalidate_sessions=True)

    assert verify_session_token(secret, old_cookie, epoch=_epoch(db, member)) is None


def test_a_session_issued_after_the_reset_still_works(db, member):
    set_member_password(db, member, "new-password", invalidate_sessions=True)
    secret = b"s" * 32

    fresh = make_session_token(secret, member, epoch=_epoch(db, member))

    assert verify_session_token(secret, fresh, epoch=_epoch(db, member)) == member


def test_one_members_reset_does_not_sign_out_another(db, member):
    set_member_password(db, "bob@example.com", "bob-password")
    secret = b"s" * 32
    bobs_cookie = make_session_token(secret, "bob@example.com",
                                     epoch=_epoch(db, "bob@example.com"))

    set_member_password(db, member, "new-password", invalidate_sessions=True)

    assert verify_session_token(secret, bobs_cookie,
                                epoch=_epoch(db, "bob@example.com")) == "bob@example.com"


def test_an_operator_issued_reset_also_signs_out_old_sessions(db, member):
    """The ops dashboard's "Reset portal password" is the path used when a
    customer phones in saying they have been compromised. It would be a poor
    joke if that were the one path that left the attacker's session alive."""
    secret = b"s" * 32
    old_cookie = make_session_token(secret, member, epoch=_epoch(db, member))

    set_member_password(db, member, "operator-issued")

    assert verify_session_token(secret, old_cookie, epoch=_epoch(db, member)) is None


# --- the owner is told --------------------------------------------------------


def test_every_password_change_notifies_the_owner(db, member):
    """Including one they performed themselves. The notification is not a
    receipt, it is the tripwire: a change the owner did not make is the only
    signal they will get that someone else has their mailbox."""
    sent = []

    set_member_password(db, member, "new-password", notify=sent.append)

    assert sent == [member]


def test_a_failing_notification_does_not_prevent_the_reset(db, member):
    """A customer locked out with a dead SMTP server must not stay locked out.
    The change is the point; the email is important and secondary, and it is
    logged loudly when it fails."""
    def explode(email):
        raise RuntimeError("smtp is down")

    set_member_password(db, member, "new-password", notify=explode)

    from sentinel.portal import check_member_login

    assert check_member_login(db, member, "new-password") is True


# --- the routes a customer actually touches -----------------------------------


def _free_port():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def portal(db):
    from sentinel.portal import serve_portal

    port = _free_port()
    srv = serve_portal(db, port)
    yield "127.0.0.1", port
    srv.shutdown()


def _post(host_port, path, body, cookie=None, origin=None):
    import http.client
    import urllib.parse

    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if cookie:
        headers["Cookie"] = f"minas_portal={cookie}"
    if origin:
        headers["Origin"] = origin
    conn.request("POST", path, body=urllib.parse.urlencode(body), headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode()
    conn.close()
    return resp.status, data


def _get(host_port, path):
    import http.client

    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    data = resp.read().decode()
    conn.close()
    return resp.status, data


def test_the_forgot_response_is_identical_for_known_and_unknown_addresses(portal, db, member):
    """The criterion, at the level a customer can observe. Byte-identical,
    not merely similar: a difference in wording, a difference in length, or a
    different status code all answer the question the attacker asked."""
    _, known = _post(portal, "/forgot", {"email": member})
    _, unknown = _post(portal, "/forgot", {"email": "nobody@example.com"})

    assert known == unknown
    assert "check your email" in known.lower() or "if that address" in known.lower()


def test_a_cross_origin_reset_request_is_rejected(portal, db, member):
    status, _ = _post(portal, "/forgot", {"email": member}, origin="https://evil.example")

    assert status == 403


def test_the_reset_form_gives_no_hint_whether_the_token_is_real(portal, db, member):
    """A valid and an invalid token render the same form.

    Deliberate, and two properties at once. Validating on GET would burn a
    single-use token just to draw a form — so a customer who reloads the page
    locks themselves out with their own browser. And a GET that rejected bad
    tokens would be an oracle: an attacker could test guesses without ever
    submitting one. The check belongs on the submit, where it costs the
    attacker an attempt."""
    token = create_reset_token(db, member)

    ok, good_body = _get(portal, f"/reset?token={token}")
    bad, bad_body = _get(portal, "/reset?token=nonsense")

    assert ok == bad == 200
    assert "new password" in good_body.lower()
    assert bad_body.replace("nonsense", "TOKEN") == good_body.replace(token, "TOKEN")


def test_submitting_the_reset_changes_the_password_and_burns_the_token(portal, db, member):
    from sentinel.portal import check_member_login

    token = create_reset_token(db, member)

    status, message, _ = exchange(portal, "POST", "/reset", body={"token": token, "password": "brand-new-password"})

    # To the form, not the front door (decision 25): the member has a
    # password to type now. No session is issued - see the handler.
    assert status == 303 and message.get("Location") == "/login"
    assert "minas_portal=" not in (message.get("Set-Cookie") or "")
    assert check_member_login(db, member, "brand-new-password") is True
    assert check_member_login(db, member, "old-password") is False
    assert consume_reset_token(db, token) is None


def test_a_reset_will_not_accept_a_trivially_short_password(portal, db, member):
    """A reset flow that accepts "a" is a reset flow that turns a stolen link
    into a permanently weak account."""
    from sentinel.portal import check_member_login

    token = create_reset_token(db, member)

    _post(portal, "/reset", {"token": token, "password": "short"})

    assert check_member_login(db, member, "short") is False


def test_the_login_page_offers_the_reset_route(portal, db):
    """A recovery flow nobody can find is a support call. The form lives at
    /login since decision 25; the root is the landing page."""
    status, body = _get(portal, "/login")

    assert status == 200 and 'id="signin-title"' in body
    assert "/forgot" in body
