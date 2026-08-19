"""A customer acknowledging their own alert, and nobody else's (G-22, #26).

Acknowledgement already existed for *operators* (#54: `sentinel acknowledge
--as <name>`). This is the customer half, and the difference that matters is
the isolation boundary: an operator is trusted with the fleet, a member is
trusted with the accounts they are a contact on. So the interesting tests here
are not "does the button work" but "what happens when someone points it
somewhere else".

Acknowledging is a write, reachable from a session cookie, that changes whether
an escalation fires. That makes it the first member-facing write in the portal
and the first place a member can make the product *quieter*. The tests below
pin, in order of what they cost if wrong:

- a member cannot acknowledge an alert on an account that isn't theirs, and
  cannot learn from the response whether that alert exists;
- an unauthenticated POST acknowledges nothing;
- the first acknowledgement wins — a later one cannot rewrite who responded;
- acknowledging stops escalation, which is the whole point of the button;
- the portal shows who acknowledged and how long it took.
"""
from __future__ import annotations

import http.client
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

from sentinel.alerts.escalation import Escalator
from sentinel.portal import acknowledge_alert_as_member, member_accounts, serve_portal, set_member_password


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


def _alert(db, account_id, minutes_ago=30, severity="CRITICAL", rule="withdrawal_initiated"):
    sent_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at) VALUES (?, ?, ?, 'k', '0.5 BTC left the account', '{}', 'high', ?)",
            (account_id, rule, severity, sent_at),
        )
        return cur.lastrowid


def _ack_row(db, alert_id):
    with db.cursor() as cur:
        cur.execute(
            "SELECT acknowledged_at, acknowledged_by FROM alerts WHERE id = ?", (alert_id,)
        )
        return cur.fetchone()


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
    conn.request(
        "POST", "/login",
        body=urllib.parse.urlencode({"email": email, "password": password}),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = conn.getresponse()
    resp.read()
    set_cookie = resp.getheader("Set-Cookie") or ""
    conn.close()
    return set_cookie.split("minas_portal=", 1)[1].split(";", 1)[0] if "minas_portal=" in set_cookie else ""


@pytest.fixture
def alice(db, portal):
    account_id = _member_account(db, "alice-1", "alice@example.com")
    set_member_password(db, "alice@example.com", "pw-alice")
    return account_id, _login(portal, "alice@example.com", "pw-alice")


# --- the isolation boundary ---------------------------------------------------


def test_a_member_cannot_acknowledge_another_members_alert(portal, db, alice):
    """The one that costs the most if wrong.

    Acknowledging silences escalation. A member who can acknowledge across the
    isolation boundary can silence a stranger's CRITICAL — turning a
    read-scoped leak into a write that suppresses someone else's alarm while
    their account is being drained."""
    _, token = alice
    bob_id = _member_account(db, "bob-1", "bob@example.com")
    bobs_alert = _alert(db, bob_id)

    status, _ = _request(portal, "POST", "/acknowledge",
                         body={"alert_id": str(bobs_alert)}, cookie=token)

    row = _ack_row(db, bobs_alert)
    assert row["acknowledged_at"] is None
    assert row["acknowledged_by"] is None
    # 404, and identical to a wholly nonexistent alert: whether bob's alert
    # exists is not something alice gets to find out.
    missing, _ = _request(portal, "POST", "/acknowledge",
                          body={"alert_id": "999999"}, cookie=token)
    assert status == missing == 404


def test_the_scope_is_enforced_inside_the_statement_that_silences(db):
    """The route checks visibility before writing. This checks the write
    itself, with the route out of the picture.

    Two guards in sequence look like belt and braces until one of them is
    quietly removed — and the removal of the inner one is invisible while the
    outer one holds. Since the inner guard is the one that survives a future
    route being added (a JSON endpoint, a bulk action, a mobile client), it is
    the one that has to be tested directly rather than through the only caller
    that exists today."""
    alice_id = _member_account(db, "alice-1", "alice@example.com")
    bob_id = _member_account(db, "bob-1", "bob@example.com")
    bobs_alert = _alert(db, bob_id)
    alice_ids = [r["id"] for r in member_accounts(db, "alice@example.com")]
    assert alice_ids == [alice_id]

    assert acknowledge_alert_as_member(db, bobs_alert, "alice@example.com", alice_ids) is False
    assert _ack_row(db, bobs_alert)["acknowledged_at"] is None

    # And it does work for her own, so the False above is the scope and not a
    # function that never returns True.
    own = _alert(db, alice_id)
    assert acknowledge_alert_as_member(db, own, "alice@example.com", alice_ids) is True


def test_an_unauthenticated_post_acknowledges_nothing(portal, db, alice):
    """Asserting only that nothing was acknowledged is not enough: a handler
    that dropped the session check and fell back to an unknown identity would
    also acknowledge nothing, because an unknown email is a contact on no
    account. That is the right outcome reached by an accident that a real
    member's email in the form would undo. So this pins the login page."""
    account_id, _ = alice
    alert_id = _alert(db, account_id)

    status, body = _request(portal, "POST", "/acknowledge", body={"alert_id": str(alert_id)})

    assert _ack_row(db, alert_id)["acknowledged_at"] is None
    assert 'id="signin-title"' in body and status == 200


def test_a_tampered_cookie_acknowledges_nothing(portal, db, alice):
    """The realistic form of the above: not a missing cookie but a forged one
    naming a member who really does own the alert."""
    account_id, token = alice
    alert_id = _alert(db, account_id)
    forged = token[:-1] + ("0" if token[-1] != "0" else "1")

    _, body = _request(portal, "POST", "/acknowledge",
                       body={"alert_id": str(alert_id)}, cookie=forged)

    assert _ack_row(db, alert_id)["acknowledged_at"] is None
    assert 'id="signin-title"' in body


def test_a_cross_origin_post_acknowledges_nothing(portal, db, alice):
    """A member's session cookie rides along on a request made by any page
    they happen to be visiting. Without the origin check, a link in a phishing
    email silences the alert about the withdrawal that phishing enabled."""
    account_id, token = alice
    alert_id = _alert(db, account_id)

    status, _ = _request(portal, "POST", "/acknowledge", body={"alert_id": str(alert_id)},
                         cookie=token, origin="https://evil.example")

    assert status == 403
    assert _ack_row(db, alert_id)["acknowledged_at"] is None


def test_a_malformed_alert_id_is_rejected_rather_than_coerced(portal, db, alice):
    """`int(...)` on form input raises; a bare `except: pass` around the whole
    handler would turn that into a silent success page."""
    _, token = alice

    status, _ = _request(portal, "POST", "/acknowledge",
                         body={"alert_id": "not-a-number"}, cookie=token)

    assert status == 404


# --- the record of who responded ---------------------------------------------


def test_acknowledging_records_the_member_and_the_time(portal, db, alice):
    account_id, token = alice
    alert_id = _alert(db, account_id)

    status, _ = _request(portal, "POST", "/acknowledge",
                         body={"alert_id": str(alert_id)}, cookie=token)

    assert status == 303  # redirect back to the account, not a blank page
    row = _ack_row(db, alert_id)
    assert row["acknowledged_by"] == "alice@example.com"
    assert row["acknowledged_at"] is not None


def test_the_first_acknowledgement_keeps_its_name(portal, db, alice):
    """An acknowledgement is a claim a specific person made at a specific
    moment. A second one must not overwrite it: if two people are looking at
    the same incident, the record of who picked it up first is the useful
    fact, and the one an exchange or an insurer will ask about."""
    account_id, token = alice
    alert_id = _alert(db, account_id)
    _request(portal, "POST", "/acknowledge", body={"alert_id": str(alert_id)}, cookie=token)
    first = _ack_row(db, alert_id)

    # A second contact on the same account acknowledges afterwards.
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email_secondary = ? WHERE id = ?",
                    ("carol@example.com", account_id))
    set_member_password(db, "carol@example.com", "pw-carol")
    carol = _login(portal, "carol@example.com", "pw-carol")
    _request(portal, "POST", "/acknowledge", body={"alert_id": str(alert_id)}, cookie=carol)

    after = _ack_row(db, alert_id)
    assert after["acknowledged_by"] == first["acknowledged_by"] == "alice@example.com"
    assert after["acknowledged_at"] == first["acknowledged_at"]


# --- what acknowledging is for -----------------------------------------------


def test_acknowledging_from_the_portal_stops_escalation(portal, db, alice):
    """The criterion this feature exists to satisfy. An unacknowledged
    CRITICAL re-fires; the same alert acknowledged through the portal must
    not, or the button does nothing a customer can feel."""
    account_id, token = alice
    alert_id = _alert(db, account_id, minutes_ago=30)

    _request(portal, "POST", "/acknowledge", body={"alert_id": str(alert_id)}, cookie=token)

    assert Escalator([], unacknowledged_minutes=15).check(db) == 0
    # And an identical un-acknowledged alert still does escalate, so the zero
    # above is the acknowledgement and not a broken escalator.
    _alert(db, account_id, minutes_ago=30)
    assert Escalator([], unacknowledged_minutes=15).check(db) == 1


# --- what the member sees ----------------------------------------------------


def test_an_unacknowledged_alert_offers_a_button_and_an_acknowledged_one_does_not(portal, db, alice):
    """The acknowledge control specifically, not "the alert id is absent".

    It used to assert the latter, which was equivalent while acknowledgement
    was the only control on the row. "That was me" (G-24, #28) is deliberately
    still offered after acknowledgement — people acknowledge in a hurry and
    recognise their own activity a minute later — so the row still carries the
    id, and the narrower assertion is the one that was always meant."""
    account_id, token = alice
    alert_id = _alert(db, account_id)

    _, before = _request(portal, "GET", f"/account?id={account_id}", cookie=token)
    assert f'value="{alert_id}"' in before
    assert "Acknowledge" in before

    _request(portal, "POST", "/acknowledge", body={"alert_id": str(alert_id)}, cookie=token)

    _, after = _request(portal, "GET", f"/account?id={account_id}", cookie=token)
    assert "action='/acknowledge'" not in after
    assert ">Acknowledge<" not in after


def test_the_portal_shows_who_acknowledged_and_how_long_it_took(portal, db, alice):
    """"The portal shows who acknowledged and when" and "time from alert to
    acknowledgement is recorded". The elapsed time is the number that says
    whether the alerting is working: an alert acknowledged in four minutes was
    read, one acknowledged in nine hours was found."""
    account_id, token = alice
    alert_id = _alert(db, account_id, minutes_ago=40)
    with db.cursor() as cur:  # acknowledged ten minutes after it was sent
        cur.execute(
            "UPDATE alerts SET acknowledged_at = ?, acknowledged_by = 'alice@example.com' WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(), alert_id),
        )

    _, body = _request(portal, "GET", f"/account?id={account_id}", cookie=token)

    assert "alice@example.com" in body
    assert "10m" in body


def test_the_acknowledging_member_is_escaped_not_injected(portal, db, alice):
    """`acknowledged_by` is written from a session identity, but it reaches the
    page as text and every other name on these pages is escaped. An email is
    allowed to contain characters HTML cares about."""
    account_id, _ = alice
    alert_id = _alert(db, account_id)
    with db.cursor() as cur:
        cur.execute(
            "UPDATE alerts SET acknowledged_at = ?, acknowledged_by = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), "<script>x</script>@example.com", alert_id),
        )
    set_member_password(db, "alice@example.com", "pw-alice")
    token = _login(portal, "alice@example.com", "pw-alice")

    _, body = _request(portal, "GET", f"/account?id={account_id}", cookie=token)

    assert "<script>x</script>" not in body
    assert "&lt;script&gt;" in body
