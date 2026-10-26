"""Who has touched my account (G-32, #36).

The customer-facing question is "would I notice if somebody changed where my
alerts go". Until this, they would not: the destination could be rewritten and
the only trace was a log line on a box the customer cannot read.

Three properties, one per criterion, and each has a specific way of passing for
the wrong reason:

**Recorded with who and when.** The trap is a page that renders *something* for
every row - a blank actor cell reads as "fine" at a glance, when an
unattributed change is precisely the finding. So the tests assert the words,
not the presence of a row.

**Operator actions are visible to the customer.** An operator issuing a portal
password is the whole point; the test drives the real write path rather than
inserting an event, because a page that renders hand-inserted rows proves
nothing about whether anything writes them.

**The record cannot be edited or deleted.** No test of a page can show that -
the guarantee lives in the writer. `test_no_update_or_delete_against_events`
reads the source instead, which is the only place the claim is true or false.
"""
from __future__ import annotations

import argparse
import http.client
import re
import urllib.parse
from pathlib import Path

import pytest

from sentinel.cli import cmd_set_contact
from sentinel.events import append_event, record_member_security_event
from sentinel.portal import account_activity, serve_portal, set_member_password
from tests.conftest import PINNED_TOTP_NOW
from tests.helpers.landing_redirect import assert_sent_to_the_landing_page

ROOT = Path(__file__).resolve().parent.parent


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


def _request(host_port, method, path, body=None, cookie=None):
    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {}
    payload = None
    if body is not None:
        payload = urllib.parse.urlencode(body)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if cookie:
        headers["Cookie"] = f"minas_portal={cookie}"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode()
    conn.close()
    return resp.status, data


def _login(host_port, email, password):
    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("POST", "/login", body=urllib.parse.urlencode(
        {"email": email, "password": password}),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    resp.read()
    set_cookie = resp.getheader("Set-Cookie") or ""
    conn.close()
    assert "minas_portal=" in set_cookie
    return set_cookie.split("minas_portal=", 1)[1].split(";", 1)[0]


def _member_account(db, label, email):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', '2026-01-01', '2026-01-01')",
            (label, email),
        )
        return cur.lastrowid


EMAIL = "customer@example.com"
PASSWORD = "a-long-portal-password"


@pytest.fixture
def signed_in(db, portal):
    aid = _member_account(db, "Main", EMAIL)
    set_member_password(db, EMAIL, PASSWORD)
    token = _login(portal, EMAIL, PASSWORD)
    return aid, token


# --- criterion 1: every settings change is recorded with who and when ---------

def test_contact_change_appears_on_the_activity_page(db, signed_in, portal, monkeypatch):
    aid, token = signed_in
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    # A phone change, so the member remains the account's email contact and can
    # still read the page. The email case is the one below, and it is different.
    cmd_set_contact(argparse.Namespace(
        label="Main", alert_email=EMAIL, alert_phone="+15550001111", clear=False))
    _status, body = _request(portal, "GET", "/activity", cookie=token)
    assert "Alert destination changed" in body
    assert "+15550001111" in body
    # The old value too: "changed to X" without "from Y" does not tell a
    # customer whether the destination they recognise is the one that was
    # removed.
    assert "phone none →" in body


def test_redirecting_the_email_hides_the_change_from_the_old_contact(db, signed_in, portal,
                                                                    monkeypatch):
    """The limit of this page, pinned so nobody mistakes it for coverage.

    Portal scope IS the contact email (see `member_accounts`), so changing the
    destination removes the previous contact's sight of the account — along
    with the record of the change that removed them. The page cannot show a
    customer the one change that matters most, which is why the notice below
    exists; the two tests belong together."""
    aid, token = signed_in
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(argparse.Namespace(
        label="Main", alert_email="attacker@example.com", alert_phone=None, clear=False))
    _status, body = _request(portal, "GET", "/activity", cookie=token)
    assert "Alert destination changed" not in body
    assert "Nothing recorded" in body  # the account is gone from their scope
    # The record itself still exists and still names both addresses; it is the
    # customer's *access* to it that ended, not the row.
    rows = account_activity(db, [aid])
    assert rows[0]["event_type"] == "alert_contact_updated"


def test_redirecting_the_email_notifies_the_address_being_removed(db, signed_in, monkeypatch):
    """The tripwire that covers what the page cannot: the last message that
    address gets about this account, sent because nobody may have authorised
    it."""
    sent: list[tuple] = []

    import sentinel.alerts.contact as contact_module

    class _Capture:
        def __init__(self, *a, **kw):
            pass

        def send(self, detection, account_label, emails, phones, **kw):
            sent.append((detection.rule, detection.message, tuple(emails)))

    monkeypatch.setattr(contact_module, "ContactRouter", _Capture)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(argparse.Namespace(
        label="Main", alert_email="attacker@example.com", alert_phone=None, clear=False))

    assert len(sent) == 1
    rule, message, recipients = sent[0]
    assert rule == "alert_destination_changed"
    assert recipients == (EMAIL,)  # the OLD address, not the new one
    assert "attacker@example.com" in message


def test_no_notice_when_the_destination_did_not_change(db, signed_in, monkeypatch):
    """A notice on every run would train the customer to ignore the one that
    matters."""
    sent: list = []
    import sentinel.alerts.contact as contact_module

    class _Capture:
        def __init__(self, *a, **kw):
            pass

        def send(self, *a, **kw):
            sent.append(a)

    monkeypatch.setattr(contact_module, "ContactRouter", _Capture)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(argparse.Namespace(
        label="Main", alert_email=EMAIL, alert_phone="+15550001111", clear=False))
    assert sent == []


def test_a_failed_notice_does_not_fail_the_change(db, signed_in, monkeypatch):
    """A dead SMTP server must not look like a failed settings update."""
    import sentinel.alerts.contact as contact_module

    class _Broken:
        def __init__(self, *a, **kw):
            pass

        def send(self, *a, **kw):
            raise RuntimeError("smtp is down")

    monkeypatch.setattr(contact_module, "ContactRouter", _Broken)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    assert cmd_set_contact(argparse.Namespace(
        label="Main", alert_email="new@example.com", alert_phone=None, clear=False)) == 0
    with db.cursor() as cur:
        cur.execute("SELECT alert_email FROM accounts WHERE label = 'Main'")
        assert cur.fetchone()["alert_email"] == "new@example.com"


def test_a_failed_audit_write_does_not_fail_the_password_change(db, portal, monkeypatch):
    """The audit row must never become a reason a locked-out customer stays
    locked out.

    Written because a probe survived: the guard around the record was in place
    and nothing was checking it, which is exactly the shape of a test passing
    for the wrong reason. A broken recorder is logged; the password still
    changes and the customer can still sign in."""
    import sentinel.events as events_module

    _member_account(db, "Main", EMAIL)

    def _explode(*a, **kw):
        raise RuntimeError("the events table is unavailable")

    monkeypatch.setattr(events_module, "record_member_security_event", _explode)
    set_member_password(db, EMAIL, PASSWORD)

    from sentinel.portal import check_member_login

    assert check_member_login(db, EMAIL, PASSWORD) is True


def test_an_unattributed_change_says_so_rather_than_rendering_blank(db, signed_in, portal,
                                                                   monkeypatch):
    aid, token = signed_in
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(argparse.Namespace(
        label="Main", alert_email=EMAIL, alert_phone="+15550001111", clear=False))
    _status, body = _request(portal, "GET", "/activity", cookie=token)
    # The CLI carries no operator identity, and the page has to say that out
    # loud - a blank cell reads as "fine".
    assert "not attributed" in body


def test_a_login_change_is_recorded_against_every_account_it_opens(db, portal):
    first = _member_account(db, "One", EMAIL)
    second = _member_account(db, "Two", EMAIL)
    stranger = _member_account(db, "Stranger", "other@example.com")
    written = record_member_security_event(db, EMAIL, "member_two_factor_disabled")
    assert written == 2
    ids = [r["account_id"] for r in account_activity(db, [first, second, stranger])]
    assert sorted(ids) == sorted([first, second])


def test_a_login_with_no_accounts_records_nothing(db):
    assert record_member_security_event(db, "nobody@example.com", "member_password_changed") == 0


def test_setting_a_password_records_it_without_being_asked(db, portal):
    """Recorded inside set_member_password, so no route can set a password
    and leave no trace."""
    aid = _member_account(db, "Main", EMAIL)
    set_member_password(db, EMAIL, PASSWORD)
    rows = account_activity(db, [aid])
    assert [r["event_type"] for r in rows] == ["member_password_changed"]


def test_two_factor_disable_is_recorded(db, cipher, portal):
    """The line a customer most needs to see, driven through the real route."""
    from sentinel.totp import STEP_SECONDS, totp_at
    from sentinel.twofactor import MEMBER, begin_enrolment, confirm_enrolment

    port = _free_port()
    srv = serve_portal(db, port, cipher=cipher)
    try:
        host_port = ("127.0.0.1", port)
        aid = _member_account(db, "Main", EMAIL)
        set_member_password(db, EMAIL, PASSWORD)
        secret, _uri = begin_enrolment(db, cipher, MEMBER, EMAIL)
        # Each of these three actions spends a step, and a spent step is
        # refused afterwards — replay protection working, not a test quirk.
        confirm_enrolment(db, cipher, MEMBER, EMAIL,
                          totp_at(secret, PINNED_TOTP_NOW - STEP_SECONDS))
        token = _login_with_code(host_port, secret)
        _request(host_port, "POST", "/security/2fa/disable",
                 body={"code": totp_at(secret, PINNED_TOTP_NOW + STEP_SECONDS)}, cookie=token)
        kinds = [r["event_type"] for r in account_activity(db, [aid])]
        assert "member_two_factor_disabled" in kinds
        assert "member_two_factor_enabled" in kinds
    finally:
        srv.shutdown()


def _login_with_code(host_port, secret):
    """Two-step sign-in, returning the session cookie."""
    from sentinel.totp import totp_at

    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("POST", "/login", body=urllib.parse.urlencode(
        {"email": EMAIL, "password": PASSWORD}),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    page = resp.read().decode()
    conn.close()
    pending = re.search(r"name=\"pending\" value=\"([^\"]+)\"", page).group(1)

    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("POST", "/login-code", body=urllib.parse.urlencode(
        {"pending": pending, "code": totp_at(secret, PINNED_TOTP_NOW)}),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    resp.read()
    set_cookie = resp.getheader("Set-Cookie") or ""
    conn.close()
    assert "minas_portal=" in set_cookie
    return set_cookie.split("minas_portal=", 1)[1].split(";", 1)[0]


# --- criterion 2: operator actions on my account are visible to me ------------

def test_operator_issuing_a_portal_password_is_visible_to_the_customer(db, signed_in, portal):
    aid, token = signed_in
    # What sentinel/dashboard.py writes when an operator clicks "portal access".
    append_event(db, aid, "member_portal_password_generated", "LOW",
                 {"contact": "primary", "email": EMAIL}, actor="alice")
    _status, body = _request(portal, "GET", "/activity", cookie=token)
    assert "Portal password issued by Minas" in body
    assert "alice" in body


# --- isolation ----------------------------------------------------------------

def test_activity_never_shows_another_members_accounts(db, signed_in, portal):
    aid, token = signed_in
    stranger = _member_account(db, "Stranger Corp", "other@example.com")
    append_event(db, stranger, "alert_contact_updated", "LOW",
                 {"email_from": "a@b.com", "email_to": "c@d.com"}, actor="mallory")
    _status, body = _request(portal, "GET", "/activity", cookie=token)
    assert "Stranger Corp" not in body
    assert "mallory" not in body


def test_activity_requires_a_session(db, portal):
    assert_sent_to_the_landing_page(portal, "GET", "/activity")


# --- the page shows changes, not detections -----------------------------------

def test_detections_are_not_listed_here(db, signed_in, portal):
    aid, token = signed_in
    append_event(db, aid, "withdrawal_unseen_address", "CRITICAL", {"amount": "0.5"})
    _status, body = _request(portal, "GET", "/activity", cookie=token)
    assert "withdrawal_unseen_address" not in body


def test_an_empty_record_says_it_is_empty_not_that_nothing_happened(db, signed_in, portal):
    """Two very different facts that read as one sentence if worded lazily."""
    aid, token = signed_in
    with db.cursor() as cur:
        cur.execute("DELETE FROM events WHERE account_id = ?", (aid,))
    _status, body = _request(portal, "GET", "/activity", cookie=token)
    assert "Nothing recorded" in body


# --- criterion 3: the record cannot be edited or deleted ----------------------

def test_no_update_or_delete_against_events_outside_retention():
    """The append-only claim, checked where it is true or false.

    `sentinel/retention.py` deletes whole rows once they age out (#60), which
    is a different act from rewriting one: nothing that survives has been
    altered. `sentinel/erasure.py` is the one module that rewrites: it
    deletes an erased customer's rows and replaces an erased person's
    details with "[erased]" in other accounts' rows (decision 6b), and the
    page names that exception in the same sentence as the promise (its
    tables are named at run time, so this scan could not see it anyway).
    Everything else must never issue either statement against `events` —
    including the portal, which is why this test lives beside the page that
    makes the promise to the customer."""
    from sentinel.portal import ACTIVITY_PROMISE

    assert "with one exception" in ACTIVITY_PROMISE and "[erased]" in ACTIVITY_PROMISE
    offenders = []
    for path in sorted((ROOT / "sentinel").rglob("*.py")):
        if path.name in ("retention.py", "erasure.py"):
            continue
        source = path.read_text()
        for match in re.finditer(r"(UPDATE\s+events|DELETE\s+FROM\s+events)", source,
                                 re.IGNORECASE):
            offenders.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
    assert offenders == [], (
        "the events table is the forensic record and the portal now promises a "
        "customer it is never edited: " + "; ".join(offenders)
    )
