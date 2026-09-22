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


def test_an_ordinary_chat_message_is_ignored_silently(db, sent):
    """Survivor x_process_update__mutmut_21 (or-to-and): a plain message to
    the bot - no /start - must be ignored entirely. Replying 'code invalid'
    to every hello teaches people the bot is broken, and replying at all to
    arbitrary messages is surface nothing needs."""
    _member_account(db)
    create_link_code(db, EMAIL)
    process_update(db, "tok", {"update_id": 5,
                               "message": {"chat": {"id": 777}, "text": "hello there"}})
    assert sent == [], "no reply of any kind to a non-/start message"
    assert connected_link(db, EMAIL) is None


# --- the worker's loop, judged (2026-09-22, #104) ------------------------------
# run(), _learn_bot_username() and _poll_once() had decision-point mutants no
# selected test reached - the method-aware mutation population found them
# unjudged. These call the real methods with net.post faked.

def _worker_with_fake_api(db, monkeypatch, on_get_updates):
    from sentinel.alerts.telegram_link import TelegramLinkWorker

    calls: list[tuple[str, dict]] = []

    class _Resp:
        def __init__(self, payload, status=200):
            self._payload, self.status_code = payload, status

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self._payload

    def fake_post(url, **kw):
        calls.append((url, kw.get("json") or {}))
        if url.endswith("/getMe"):
            return _Resp({"ok": True, "result": {"username": "minas_guardian_bot"}})
        return _Resp(*on_get_updates(len([c for c in calls if c[0].endswith("/getUpdates")])))

    monkeypatch.setattr(tl.net, "post", fake_post)
    return TelegramLinkWorker(db, "tok", poll_timeout=1), calls


def test_learning_the_bot_username_records_it_and_a_failure_degrades_quietly(db, monkeypatch, caplog):
    import logging

    worker, calls = _worker_with_fake_api(db, monkeypatch, lambda n: ({"result": []},))
    worker._learn_bot_username()
    assert tl.bot_username(db) == "minas_guardian_bot"
    assert calls[-1][0].endswith("/bottok/getMe")

    def boom(*a, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(tl.net, "post", boom)
    with caplog.at_level(logging.WARNING, logger="sentinel.alerts.telegram_link"):
        worker._learn_bot_username()  # must not raise: the portal says 'the Minas bot'
    assert any("could not learn the bot username" in r.getMessage() for r in caplog.records)
    assert tl.bot_username(db) == "minas_guardian_bot", "a failure never erases what was learnt"


def test_a_blank_username_in_the_reply_is_not_recorded(db, monkeypatch):
    from sentinel.alerts.telegram_link import TelegramLinkWorker

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True, "result": {"username": ""}}

    monkeypatch.setattr(tl.net, "post", lambda *a, **k: _Resp())
    TelegramLinkWorker(db, "tok")._learn_bot_username()
    assert tl.bot_username(db) is None


def test_one_poll_processes_every_update_and_persists_the_offset_per_update(db, monkeypatch):
    account_id = _member_account(db)
    code = create_link_code(db, EMAIL)
    updates = [
        {"update_id": 700, "message": {"chat": {"id": 4242}, "text": f"/start {code}"}},
        {"update_id": 701, "message": {"chat": {"id": 4242}, "text": "hello"}},
    ]
    worker, calls = _worker_with_fake_api(db, monkeypatch, lambda n: ({"result": updates},))
    worker._poll_once()
    polls = [c for c in calls if c[0].endswith("/getUpdates")]
    assert polls[0][1] == {"timeout": 1}, "the first poll carries no offset"
    assert tl._meta_get(db, tl._OFFSET_KEY) == "702", "the offset is the last update id + 1"
    with db.cursor() as cur:
        cur.execute("SELECT status, chat_id FROM telegram_links WHERE member_email = ?", (EMAIL,))
        row = cur.fetchone()
    assert (row["status"], row["chat_id"]) == ("connected", "4242"), "the /start update was processed"
    del account_id

    worker._poll_once()
    assert [c for c in calls if c[0].endswith("/getUpdates")][-1][1] == {"timeout": 1, "offset": 702}, (
        "the next poll resumes from the persisted offset")


def test_a_poll_that_fails_mid_batch_keeps_the_offset_of_what_was_processed(db, monkeypatch):
    updates = [
        {"update_id": 800, "message": {"chat": {"id": 1}, "text": "hi"}},
        {"update_id": 801, "message": {"chat": {"id": 1}, "text": "hi"}},
    ]
    worker, _ = _worker_with_fake_api(db, monkeypatch, lambda n: ({"result": updates},))
    seen = []

    def process(db_, token, update):
        seen.append(update["update_id"])
        if update["update_id"] == 801:
            raise RuntimeError("db hiccup")

    monkeypatch.setattr(tl, "process_update", process)
    with pytest.raises(RuntimeError):
        worker._poll_once()
    assert seen == [800, 801]
    assert tl._meta_get(db, tl._OFFSET_KEY) == "801", "800 is done and will not replay; 801 will"


def test_an_http_error_from_get_updates_is_raised_to_the_loop(db, monkeypatch):
    worker, _ = _worker_with_fake_api(db, monkeypatch, lambda n: ({"result": []}, 502))
    with pytest.raises(RuntimeError, match="HTTP 502"):
        worker._poll_once()


def test_the_loop_learns_the_name_first_then_polls_until_stopped_and_survives_a_failure(db, monkeypatch):
    def on_get_updates(n):
        if n == 1:
            raise RuntimeError("telegram down")
        return ({"result": []},)

    worker, calls = _worker_with_fake_api(db, monkeypatch, on_get_updates)
    worker.BACKOFF_BASE_SECONDS = 0  # the wait after the failure must not cost the suite 30 s
    seen_failures = []
    original = worker._register_failure

    def register(exc):
        seen_failures.append(str(exc))
        if len(seen_failures) >= 3:
            worker.stop()  # a mutant that fails every poll must not loop the suite forever
        return original(exc)

    worker._register_failure = register
    real_poll = worker._poll_once

    def poll_then_stop():
        real_poll()
        worker.stop()  # the first SUCCESSFUL poll ends the loop

    worker._poll_once = poll_then_stop
    worker.run()
    assert calls[0][0].endswith("/getMe"), "the name is learnt before the first poll"
    assert seen_failures == ["telegram down"], "the failed poll was registered, not fatal"
    assert worker._consecutive_failures == 0, "the success after it reset the ladder"
    assert len([c for c in calls if c[0].endswith("/getUpdates")]) == 2


def test_a_stopped_worker_never_polls(db, monkeypatch):
    worker, calls = _worker_with_fake_api(db, monkeypatch, lambda n: ({"result": []},))
    polls = []

    def poll():
        polls.append(1)
        worker._stop.clear()  # bounds ANY loop shape: a mutant that polls while stopped exits here

    worker._poll_once = poll
    worker.stop()
    worker.run()
    assert polls == [], "stopped before the loop: nothing is polled"
    assert [c[0].rsplit("/", 1)[1] for c in calls] == ["getMe"], "the name is still learnt"
