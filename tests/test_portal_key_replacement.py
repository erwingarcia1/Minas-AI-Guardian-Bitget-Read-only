"""The portal's first write that accepts a secret (#69).

Everything else a member can POST — acknowledge, snooze, "that was me" — moves
a flag on a row they can already see. This one hands the server a credential
and asks it to talk to an exchange, which makes the ordering of its checks a
security property rather than a style choice: session, then scope, then the
expensive validation. An unauthenticated POST must not be able to make this
server open a connection to anybody.

`test_key_replacement.py` covers what the replacement itself accepts and
refuses. This file covers who is allowed to ask.
"""

from __future__ import annotations

import http.client
import urllib.parse
from unittest.mock import MagicMock

import pytest

from sentinel.portal import serve_portal, set_member_password
from tests.fixtures.ccxt_responses import account_info_response
from tests.helpers.landing_redirect import assert_sent_to_the_landing_page

OUR_UID = "12345"


def _free_port():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def calls(monkeypatch):
    """Every exchange client the portal builds, and what it answered.

    Also the assertion behind "an unauthenticated POST reaches no exchange":
    an empty list is the evidence, so it is collected even by the tests that
    expect nothing."""
    made = []

    def _make_client(exchange, api_key, secret, passphrase):
        made.append({"exchange": exchange, "api_key": api_key})
        client = MagicMock()
        client.exchange_id = exchange
        client.fetch_balance.return_value = {"total": {"USDT": 1}}
        info = account_info_response(authorities="spot_account_r")
        info["data"]["userId"] = OUR_UID
        client.fetch_account_info.return_value = info
        return client

    monkeypatch.setattr("sentinel.keyreplacement.make_client", _make_client)
    return made


@pytest.fixture
def portal(db, cipher):
    port = _free_port()
    srv = serve_portal(db, port, cipher=cipher)
    yield "127.0.0.1", port
    srv.shutdown()


def _account(db, cipher, label, email, uid=OUR_UID):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, exchange_uid, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', ?, ?, ?, ?, ?, 'active', '2026-01-01', '2026-01-01')",
            (label, cipher.encrypt("old-key"), cipher.encrypt("old-secret"),
             cipher.encrypt(""), email, uid),
        )
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
        return account_id


def _request(host_port, method, path, body=None, cookie=None, origin=None):
    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {}
    payload = None
    if body is not None:
        payload = urllib.parse.urlencode(body)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if cookie:
        headers["Cookie"] = f"minas_portal={cookie}"
    if origin:
        headers["Origin"] = origin
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode()
    conn.close()
    return resp.status, data


def _login(host_port, email, password):
    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("POST", "/login",
                 body=urllib.parse.urlencode({"email": email, "password": password}),
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    resp.read()
    cookie = resp.getheader("Set-Cookie") or ""
    conn.close()
    return cookie.split("minas_portal=", 1)[1].split(";", 1)[0] if "minas_portal=" in cookie else ""


@pytest.fixture
def alice(db, cipher, portal):
    account_id = _account(db, cipher, "alice-1", "alice@example.com")
    set_member_password(db, "alice@example.com", "pw-alice-long")
    return account_id, _login(portal, "alice@example.com", "pw-alice-long")


def _stored_key(db, account_id, cipher):
    with db.cursor() as cur:
        cur.execute("SELECT encrypted_api_key FROM accounts WHERE id = ?", (account_id,))
        return cipher.decrypt(cur.fetchone()["encrypted_api_key"])


# --- who may ask ---------------------------------------------------------------


def test_an_unauthenticated_post_reaches_no_exchange(portal, db, cipher, alice, calls):
    """The check that has to come first. A stranger who can make this server
    dial an exchange has a way to use us as a proxy and a way to burn our rate
    limit, before anything about keys is even considered."""
    account_id, _ = alice

    assert_sent_to_the_landing_page(portal, "POST", "/key",
                                    body={"account_id": str(account_id), "api_key": "k", "secret": "s"})

    assert calls == []
    assert _stored_key(db, account_id, cipher) == "old-key"


def test_a_member_cannot_replace_the_key_on_somebody_elses_account(portal, db, cipher, alice, calls):
    """The isolation boundary, on the highest-stakes write in the portal.

    Getting this wrong is worse than a read leak: it points somebody else's
    monitoring at an account of the attacker's choosing, which is a way to
    switch off a stranger's alarm without ever touching their exchange."""
    _, token = alice
    bob = _account(db, cipher, "bob-1", "bob@example.com")

    status, _ = _request(portal, "POST", "/key",
                         body={"account_id": str(bob), "api_key": "k", "secret": "s"},
                         cookie=token)

    assert status == 404
    assert calls == []
    assert _stored_key(db, bob, cipher) == "old-key"


def test_the_form_for_somebody_elses_account_is_not_found(portal, db, cipher, alice):
    """404, not 403: whether an account exists is not something the portal
    tells a stranger, on this page any more than the others."""
    _, token = alice
    bob = _account(db, cipher, "bob-1", "bob@example.com")

    status, _ = _request(portal, "GET", f"/key?id={bob}", cookie=token)
    assert status == 404


def test_the_form_needs_a_session(portal, alice):
    account_id, _ = alice
    assert_sent_to_the_landing_page(portal, "GET", f"/key?id={account_id}")


def test_a_cross_origin_post_is_rejected(portal, db, cipher, alice, calls):
    account_id, token = alice

    status, _ = _request(portal, "POST", "/key",
                         body={"account_id": str(account_id), "api_key": "k", "secret": "s"},
                         cookie=token, origin="https://evil.example")

    assert status == 403
    assert calls == []
    assert _stored_key(db, account_id, cipher) == "old-key"


# --- what the page says --------------------------------------------------------


def test_the_form_states_the_two_rules_and_the_phishing_one(portal, alice):
    """A customer arrives here frightened. The page has to say what will be
    refused before they paste anything, and it has to teach the rule that
    protects them from the imitation of this page: we never send a link to it."""
    account_id, token = alice

    status, body = _request(portal, "GET", f"/key?id={account_id}", cookie=token)

    assert status == 200
    assert 'action="/key"' in body
    assert "read-only" in body.lower()
    assert "same exchange account" in body
    assert "never email you a link" in body


def test_a_submitted_secret_is_never_rendered_back(portal, db, cipher, alice, calls):
    """A wrong key is retyped, not corrected in place. Echoing it puts a live
    credential in browser history, in any proxy on the path, and on the
    shoulder of whoever is standing behind a frightened person."""
    account_id, token = alice

    status, body = _request(
        portal, "POST", "/key",
        body={"account_id": str(account_id), "api_key": "VISIBLE-KEY", "secret": "VISIBLE-SECRET"},
        cookie=token)

    assert status == 200
    assert "VISIBLE-KEY" not in body
    assert "VISIBLE-SECRET" not in body


# --- the whole path ------------------------------------------------------------


def test_a_member_replaces_their_own_key_end_to_end(portal, db, cipher, alice, calls):
    account_id, token = alice

    status, body = _request(
        portal, "POST", "/key",
        body={"account_id": str(account_id), "api_key": "brand-new-key",
              "secret": "brand-new-secret", "passphrase": ""},
        cookie=token)

    assert status == 200
    assert _stored_key(db, account_id, cipher) == "brand-new-key"
    assert calls and calls[0]["api_key"] == "brand-new-key"
    assert "Monitoring restarts" in body


def test_a_refusal_comes_back_as_an_error_and_changes_nothing(portal, db, cipher, alice, monkeypatch):
    """The exchange says this key is for a different account."""
    account_id, token = alice

    def _make_client(exchange, api_key, secret, passphrase):
        client = MagicMock()
        client.exchange_id = exchange
        client.fetch_balance.return_value = {"total": {"USDT": 1}}
        info = account_info_response(authorities="spot_account_r")
        info["data"]["userId"] = "99999"
        client.fetch_account_info.return_value = info
        return client

    monkeypatch.setattr("sentinel.keyreplacement.make_client", _make_client)

    status, body = _request(
        portal, "POST", "/key",
        body={"account_id": str(account_id), "api_key": "k", "secret": "s"},
        cookie=token)

    assert status == 400
    assert "different exchange account" in body
    assert _stored_key(db, account_id, cipher) == "old-key"


def test_without_a_cipher_the_route_refuses_rather_than_storing_plaintext(db, alice, calls):
    """`serve_portal` takes an optional cipher, and a server started without
    one has nowhere safe to put a key. The two-factor routes make the same
    refusal; this one matters more, because the thing being stored opens an
    exchange account."""
    port = _free_port()
    srv = serve_portal(db, port, cipher=None)
    try:
        account_id, _ = alice
        set_member_password(db, "alice@example.com", "pw-alice-long")
        token = _login(("127.0.0.1", port), "alice@example.com", "pw-alice-long")
        status, _ = _request(("127.0.0.1", port), "POST", "/key",
                             body={"account_id": str(account_id), "api_key": "k", "secret": "s"},
                             cookie=token)
    finally:
        srv.shutdown()

    assert status == 503
    assert calls == []


def test_the_account_page_offers_the_route(portal, db, cipher, alice):
    """The link is on every account, not only ones that already look broken:
    the customer who needs it has just revoked everything, and the freshness
    check may not have caught up yet."""
    account_id, token = alice

    status, body = _request(portal, "GET", f"/account?id={account_id}", cookie=token)

    assert status == 200
    assert f'href="/key?id={account_id}"' in body
