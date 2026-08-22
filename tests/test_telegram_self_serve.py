"""#18: a member connects Telegram themselves, guided, test-messaged - and
a Telegram failure can never cost them the alert.

The Bot API cannot DM someone who has not started the bot, so the flow is a
one-time code the portal issues and the update worker redeems: /start
<code> binds the chat, the test message IS the confirmation, and delivery
becomes a second copy of what the contact's email already gets - customer
copy, never the operator format. Disconnecting is a security event with an
email tripwire, because silencing a channel is what an attacker does
before acting.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sentinel.alerts import telegram_link as tl
from sentinel.alerts.telegram_link import (
    chat_ids_for_emails,
    confirm_code,
    connected_link,
    create_link_code,
    disconnect,
    pending_link,
    process_update,
)

EMAIL = "alice@example.com"


@pytest.fixture
def sent(monkeypatch):
    """Every Telegram API call the code under test makes."""
    calls: list[tuple[str, dict]] = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": []}

    def fake_post(url, **kw):
        calls.append((url, kw.get("json") or {}))
        return _Resp()

    monkeypatch.setattr(tl.net, "post", fake_post)
    return calls


def _member_account(db, email=EMAIL):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES ('acct-t', 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', "
            "'2026-01-01', '2026-01-01')", (email,))
        return cur.lastrowid


def _start_update(code, chat_id=777):
    return {"update_id": 1,
            "message": {"chat": {"id": chat_id}, "text": f"/start {code}"}}


# --- the handshake ------------------------------------------------------------

def test_a_valid_code_binds_the_chat_and_sends_the_test_message(db, sent):
    _member_account(db)
    code = create_link_code(db, EMAIL)

    process_update(db, "tok", _start_update(code))

    link = connected_link(db, EMAIL)
    assert link is not None and link["chat_id"] == "777"
    assert link["link_code"] is None, "the code is cleared the moment it is used"
    (url, payload), = sent
    assert payload["chat_id"] == 777
    assert "test message" in payload["text"], (
        "the first message must BE the confirmation the criterion asks for")


def test_an_unknown_code_gets_a_refusal_and_binds_nothing(db, sent):
    _member_account(db)
    create_link_code(db, EMAIL)

    process_update(db, "tok", _start_update("not-a-code"))

    assert connected_link(db, EMAIL) is None
    (_, payload), = sent
    assert "isn't valid" in payload["text"]


def test_an_expired_code_is_refused(db, sent):
    _member_account(db)
    code = create_link_code(db, EMAIL)
    with db.cursor() as cur:
        cur.execute("UPDATE telegram_links SET code_expires_at = ?",
                    ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))

    process_update(db, "tok", _start_update(code))

    assert connected_link(db, EMAIL) is None


def test_a_code_works_exactly_once(db, sent):
    _member_account(db)
    code = create_link_code(db, EMAIL)
    process_update(db, "tok", _start_update(code, chat_id=777))
    process_update(db, "tok", _start_update(code, chat_id=888))

    link = connected_link(db, EMAIL)
    assert link["chat_id"] == "777", "the second chat must not steal the binding"


def test_a_fresh_code_revokes_the_pending_one(db, sent):
    _member_account(db)
    old = create_link_code(db, EMAIL)
    new = create_link_code(db, EMAIL)
    assert confirm_code(db, old, "999") is None, "two live codes is two phishable secrets"
    assert confirm_code(db, new, "999") == EMAIL


def test_connecting_is_a_recorded_security_event(db, sent):
    account_id = _member_account(db)
    code = create_link_code(db, EMAIL)
    process_update(db, "tok", _start_update(code))
    with db.cursor() as cur:
        cur.execute("SELECT event_type FROM events WHERE account_id = ?", (account_id,))
        assert "member_telegram_connected" in [r["event_type"] for r in cur.fetchall()]


# --- disconnect ---------------------------------------------------------------

def test_disconnect_revokes_and_delivery_stops(db, sent):
    _member_account(db)
    code = create_link_code(db, EMAIL)
    process_update(db, "tok", _start_update(code))
    assert chat_ids_for_emails(db, [EMAIL]) == ["777"]

    assert disconnect(db, EMAIL) is True
    assert connected_link(db, EMAIL) is None
    assert chat_ids_for_emails(db, [EMAIL]) == []
    assert disconnect(db, EMAIL) is False, "nothing connected - nothing to revoke"


# --- delivery is a second copy, in customer words -----------------------------

def _detection(notice=False):
    class _D:
        rule = "withdrawal_to_unseen_address"
        severity = "CRITICAL"
        confidence = "high"
        message = "raw detector text with evidence"
        evidence = {"address": "0xdeadbeef"}
        is_notice = notice
    return _D()


def test_connected_chats_get_customer_copy_never_the_operator_format(db, sent, monkeypatch):
    from sentinel.alerts.contact import ContactRouter

    _member_account(db)
    code = create_link_code(db, EMAIL)
    process_update(db, "tok", _start_update(code))
    sent.clear()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")

    ContactRouter(db=db).send(_detection(), "acct-t", [EMAIL], [])

    tg = [(u, p) for u, p in sent if "sendMessage" in u]
    assert len(tg) == 1
    text = tg[0][1]["text"]
    assert "0xdeadbeef" not in text, "evidence values never reach customer copy"
    assert "withdrawal_to_unseen_address" not in text, "rule names are internal"
    assert "address this account has not used before" in text


def test_a_telegram_failure_does_not_cost_the_email(db, monkeypatch):
    """The fallback criterion, as this product actually does it: channels
    are parallel, so email is not a fallback that must be triggered - it was
    always going to arrive, and a Telegram outage cannot interfere."""
    import sentinel.alerts.contact as contact_module
    from sentinel.alerts.contact import ContactRouter

    _member_account(db)
    code = create_link_code(db, EMAIL)

    class _OkResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": []}

    monkeypatch.setattr(tl.net, "post", lambda *a, **k: _OkResp())
    process_update(db, "tok", _start_update(code))

    def boom(*a, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr("sentinel.net.post", boom)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")

    mailed = []

    class _CaptureEmail:
        def __init__(self, host, port, username, password, sender, recipients, **kw):
            mailed.append(tuple(recipients))

        def send(self, alert):
            pass

    monkeypatch.setattr(contact_module, "EmailChannel", _CaptureEmail)
    router = ContactRouter(db=db)
    router._smtp = {"host": "h", "port": 25, "username": "u", "password": "p", "sender": "s@x"}

    router.send(_detection(), "acct-t", [EMAIL], [])

    assert mailed == [(EMAIL,)], "the email leg must be untouched by the telegram outage"


# --- the portal page ----------------------------------------------------------

def test_the_security_page_walks_through_the_three_states(db, sent):
    from sentinel.portal import _render_security

    _member_account(db)
    page = _render_security(db, EMAIL)
    assert "Telegram alerts are off" in page
    assert 'action="/telegram/link"' in page

    code = create_link_code(db, EMAIL)
    page = _render_security(db, EMAIL)
    assert "Finish connecting Telegram" in page
    assert f"/start {code}" in page
    assert "test message" in page, "the guide promises the confirmation up front"

    process_update(db, "tok", _start_update(code))
    page = _render_security(db, EMAIL)
    assert "Telegram alerts are on" in page
    assert 'action="/telegram/unlink"' in page


def test_the_deep_link_appears_once_the_bot_username_is_known(db, sent):
    from sentinel.portal import _render_security

    _member_account(db)
    create_link_code(db, EMAIL)
    tl._meta_set(db, tl._BOT_USERNAME_KEY, "MinasGuardianBot")
    page = _render_security(db, EMAIL)
    assert "https://t.me/MinasGuardianBot?start=" in page


def test_pending_expires_back_to_the_connect_button(db, sent):
    from sentinel.portal import _render_security

    _member_account(db)
    create_link_code(db, EMAIL)
    with db.cursor() as cur:
        cur.execute("UPDATE telegram_links SET code_expires_at = ?",
                    ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
    page = _render_security(db, EMAIL)
    assert "Telegram alerts are off" in page
    assert pending_link(db, EMAIL) is None


# --- the worker's backoff (#18 hardening) -------------------------------------

def test_repeated_failures_back_off_to_the_cap_and_reset_on_success(db):
    from sentinel.alerts.telegram_link import TelegramLinkWorker

    worker = TelegramLinkWorker(db, "tok")
    delays = [worker._register_failure(RuntimeError("down")) for _ in range(6)]
    assert delays == [30, 60, 120, 240, 300, 300], "30s doubling to a 5-minute cap"
    worker._consecutive_failures = 0  # what a successful poll does
    assert worker._register_failure(RuntimeError("down")) == 30, "success resets the ladder"


def test_a_409_is_named_loudly_exactly_once(db, caplog):
    import logging

    from sentinel.alerts.telegram_link import TelegramLinkWorker

    class _Resp:
        status_code = 409

    class _Conflict(Exception):
        response = _Resp()

    worker = TelegramLinkWorker(db, "tok")
    with caplog.at_level(logging.ERROR, logger="sentinel.alerts.telegram_link"):
        worker._register_failure(_Conflict())
        worker._register_failure(_Conflict())
    conflicts = [r for r in caplog.records if "409 Conflict" in r.getMessage()]
    assert len(conflicts) == 1, "one clear line, not a drumbeat"
    assert "sendMessage is unaffected" in conflicts[0].getMessage(), (
        "the line must say alert delivery is safe, or it reads as an alerting outage")
