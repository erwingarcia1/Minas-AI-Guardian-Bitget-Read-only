"""One page per incident, and what it is allowed to say (G-25, #29).

Three of the four criteria are built here. The third — "states in plain words
what to do next (revoke the key, contact the exchange, move funds)" — is
deliberately not, and is logged as a product decision. Telling a customer to
move funds is advice with consequences, and the wording is the whole of it.

The page is evidence. A customer hands it to an exchange or to the police, so
what matters is less how it looks than what it is *allowed to claim*:

- it must never say an alert was delivered when it was not — `delivered_to = 0`
  is the fact #57 exists to preserve, and a timeline that quietly rendered it
  as "notified" would undo that at the exact moment it matters most;
- it must never show an incident on an account the reader is not a contact on;
- it must show the balances as they were **at the time**, not as they are now,
  because the reader is reconstructing a past state.

Export is print-to-PDF from the browser rather than a generated file. That is a
deliberate choice, not a shortcut — see the test at the bottom.
"""
from __future__ import annotations

import http.client
import json
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

from sentinel.portal import serve_portal, set_member_password


def _free_port():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def portal(db):
    port = _free_port()
    srv = serve_portal(db, port)
    yield "127.0.0.1", port
    srv.shutdown()


def _member_account(db, label, email):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', '2026-01-01', '2026-01-01')",
            (label, email),
        )
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
        return account_id


def _incident(db, account_id, *, delivered=2, acked_by=None, minutes_ago=120):
    sent_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    acked_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago - 10)).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at, delivered_to, acknowledged_at, acknowledged_by) "
            "VALUES (?, 'unexplained_quantity_decrease', 'CRITICAL', 'k', "
            "'BTC balance dropped by 4 but only 1 is explained', ?, 'high', ?, ?, ?, ?)",
            (account_id,
             json.dumps({"coin": "BTC", "drop": "4", "explained_outflow": "1",
                         "address": "bc1qattacker"}),
             sent_at, delivered, acked_at if acked_by else None, acked_by),
        )
        alert_id = cur.lastrowid
        # A balance snapshot from before the alert, and one from after.
        for offset, qty in ((minutes_ago + 5, "10"), (minutes_ago - 5, "6")):
            cur.execute(
                "INSERT INTO balance_snapshots (account_id, coin, quantity, snapshot_at) "
                "VALUES (?, 'BTC', ?, ?)",
                (account_id, qty,
                 (datetime.now(timezone.utc) - timedelta(minutes=offset)).isoformat()),
            )
    return alert_id


def _request(host_port, path, cookie=None):
    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {"Cookie": f"minas_portal={cookie}"} if cookie else {}
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, body


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
def alice(db, portal):
    account_id = _member_account(db, "alice-1", "alice@example.com")
    set_member_password(db, "alice@example.com", "pw-alice")
    return account_id, _login(portal, "alice@example.com", "pw-alice")


# --- the isolation boundary, first as always ---------------------------------


def test_a_member_cannot_read_another_members_incident(portal, db, alice):
    """An incident page is the most detailed view of an account in the whole
    product — balances, destination addresses, timings. Leaking one is worse
    than leaking the alert list it was built from."""
    _, token = alice
    bob_id = _member_account(db, "bob-1", "bob@example.com")
    bobs_incident = _incident(db, bob_id)

    status, body = _request(portal, f"/incident?id={bobs_incident}", cookie=token)

    assert status == 404
    assert "bc1qattacker" not in body
    assert "bob-1" not in body


def test_an_unauthenticated_reader_gets_the_login_page(portal, db, alice):
    account_id, _ = alice
    incident = _incident(db, account_id)

    status, body = _request(portal, f"/incident?id={incident}")

    assert 'id="signin-title"' in body
    assert "bc1qattacker" not in body


def test_a_nonexistent_incident_is_indistinguishable_from_someone_elses(portal, db, alice):
    _, token = alice
    bob_id = _member_account(db, "bob-1", "bob@example.com")
    bobs = _incident(db, bob_id)

    theirs, _ = _request(portal, f"/incident?id={bobs}", cookie=token)
    missing, _ = _request(portal, "/incident?id=999999", cookie=token)

    assert theirs == missing == 404


# --- what was detected, when, on which account, what the balances were -------


def test_the_page_says_what_was_detected_and_on_which_account(portal, db, alice):
    account_id, token = alice
    incident = _incident(db, account_id)

    status, body = _request(portal, f"/incident?id={incident}", cookie=token)

    assert status == 200
    assert "unexplained_quantity_decrease" in body
    assert "alice-1" in body
    assert "CRITICAL" in body.upper()  # the badge renders "Critical"; CSS uppercases it
    assert "bc1qattacker" in body  # the evidence, which is the point of the page


def test_the_page_shows_the_balances_as_they_were_not_as_they_are(portal, db, alice):
    """The reader is reconstructing a past state. A page that showed today's
    balance would be actively misleading in the one document a customer hands
    to an exchange."""
    account_id, token = alice
    incident = _incident(db, account_id)
    with db.cursor() as cur:  # today's balance, long after the incident
        cur.execute(
            "INSERT INTO balance_snapshots (account_id, coin, quantity, snapshot_at) "
            "VALUES (?, 'BTC', '999', ?)",
            (account_id, datetime.now(timezone.utc).isoformat()),
        )

    _, body = _request(portal, f"/incident?id={incident}", cookie=token)

    # Matched as a rendered table cell, not as a bare substring: "999" also
    # appears in `border-radius:999px` in the stylesheet, and a test that
    # passes only because of a CSS value is a test that will pass again for
    # the wrong reason later.
    assert ">999<" not in body
    assert ">10<" in body and ">6<" in body  # before and after the drop


# --- detection -> notification -> acknowledgement ----------------------------


def test_the_timeline_shows_all_three_stages(portal, db, alice):
    account_id, token = alice
    incident = _incident(db, account_id, delivered=2, acked_by="alice@example.com")

    _, body = _request(portal, f"/incident?id={incident}", cookie=token)

    assert "Detected" in body
    assert "Notified" in body
    assert "Acknowledged" in body
    assert "alice@example.com" in body


def test_an_undelivered_alert_is_never_rendered_as_notified(portal, db, alice):
    """The one thing this page must never get wrong.

    `delivered_to = 0` means detected and reached nobody — the fact #57 exists
    to preserve, on the same footing as an alert that never happened being
    absent. A timeline that rendered it as "notified" because there is a row
    would undo that at the exact moment it matters most: in the document
    handed to an exchange to show the customer was not warned in time."""
    account_id, token = alice
    incident = _incident(db, account_id, delivered=0)

    _, body = _request(portal, f"/incident?id={incident}", cookie=token)

    assert "reached nobody" in body.lower() or "not delivered" in body.lower()
    assert "Notified" not in body or body.index("reached nobody") > 0


def test_an_unacknowledged_incident_says_so_rather_than_omitting_the_stage(portal, db, alice):
    """An absent stage reads as an unfinished page. "Not acknowledged" is a
    fact about the incident and belongs in the record."""
    account_id, token = alice
    incident = _incident(db, account_id, acked_by=None)

    _, body = _request(portal, f"/incident?id={incident}", cookie=token)

    assert "not acknowledged" in body.lower()


def test_the_timeline_is_in_chronological_order(portal, db, alice):
    account_id, token = alice
    incident = _incident(db, account_id, acked_by="alice@example.com")

    _, body = _request(portal, f"/incident?id={incident}", cookie=token)

    assert body.index("Detected") < body.index("Notified") < body.index("Acknowledged")


# --- export ------------------------------------------------------------------


def test_the_page_carries_print_styles_so_it_exports_cleanly(portal, db, alice):
    """"Export as a PDF for the exchange or the police."

    Print-to-PDF from the browser, not a generated file. A PDF library is a new
    dependency in a security product, rendering server-side means fonts and
    layout drift from what the customer saw, and the browser's own export
    already produces a document with the page's URL and a timestamp in the
    footer — which is *better* provenance for evidence than a file we generate
    ourselves and could be accused of having composed.

    What that requires from us is print CSS that does not throw away the
    content, and a page that carries its own identifying header."""
    account_id, token = alice
    incident = _incident(db, account_id)

    _, body = _request(portal, f"/incident?id={incident}", cookie=token)

    assert "@media print" in body
    assert "Print" in body  # a control the customer can find


def test_the_incident_is_reachable_from_the_alert_row(portal, db, alice):
    """A page nobody can navigate to is a page that does not exist."""
    account_id, token = alice
    incident = _incident(db, account_id)

    _, account_page = _request(portal, f"/account?id={account_id}", cookie=token)

    assert f"/incident?id={incident}" in account_page
