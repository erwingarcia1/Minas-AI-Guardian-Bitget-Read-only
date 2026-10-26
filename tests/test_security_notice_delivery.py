"""Decisions 26b and 26c (the owner, 2026-10-25): where a security notice
goes, and what happens when one does not arrive.

26c - the password-reset link goes to the mailbox and nowhere else. A
notice bypasses the member's channel preferences, so the contact router
sent it to every channel the address has, and the link reached a Telegram
chat the member had connected (#18) as well as the mailbox. The link
proves control of the mailbox, which is what a reset is meant to prove;
with the Telegram copy, whoever held the member's Telegram could take over
their portal login without it.

26b - a refused security tripwire pages Minas. "Your password was
changed", "two-factor was turned off" and "your alerts were redirected"
are the notices an attacker hopes never arrive. A notice is LOW, so the
refused-email page (2026-09-22) never fired for one - and every sender
built its router with no operator alerter, so nothing could have. A
tripwire the email provider REFUSED means the member was not warned by
email, and only a person can make sure they are warned now. Every
refusal pages, whatever the Telegram leg did: whoever is signed in to a
login can connect a chat of their own, and the member is not emailed
when one is, so the page says how many connected chats took the copy
and is never silenced by them. A send that never reached the provider
stays a log line, as for an alert (#58). The page names the address
masked and points at the notice's failed delivery row, which has it in
full.
"""
from __future__ import annotations

import argparse
import inspect
import json
import smtplib
import sqlite3
from datetime import datetime, timedelta

import pytest

from sentinel.detectors.base import Detection
from tests.conftest import PINNED_TOTP_NOW
from tests.helpers.landing_redirect import exchange
from tests.test_operator_alerting import Recording, rules_yaml
from tests.test_portal_two_factor_login import (  # noqa: F401 - alice_with_2fa is a fixture
    ALICE,
    PASSWORD,
    _pending_from,
    _post,
    alice_with_2fa,
)

EMAIL = "alice@example.com"
MASKED = "al***@example.com"
LOGIN_LABEL = "your Minas AI Guardian login"
PAGE = "security_notice_undelivered"
TRIPWIRES = ("password_changed", "two_factor_disabled", "alert_destination_changed")
# The two about the login, sent under its label: their page names the
# accounts the login opens.
LOGIN_TRIPWIRES = ("password_changed", "two_factor_disabled")
# What the member has NOT been told when each tripwire is refused, with
# 'Main' as the account whose alerts were redirected.
UNTOLD = {
    "password_changed": "their portal password was changed",
    "two_factor_disabled": "two-factor was turned off on their login",
    "alert_destination_changed": "the alerts for 'Main' now go to another primary address",
}
QUOTA = smtplib.SMTPDataError(550, b"You have reached your daily email sending quota.")
DEAD_WIRE = ConnectionRefusedError(111, "Connection refused")
TELEGRAM_REASON = ("If the provider is refusing all of Minas's mail, this page's own email copy fails the "
                   "same way - Telegram still carries it, and SMS where it is configured.")
# What the page says when a connected chat took the tripwire's copy - the
# chat may be the attacker's, so the page goes and says so.
TOLD_ON_TELEGRAM = ("The member has NOT been told by email that their portal password was changed. 1 Telegram "
                    "chat(s) connected to this login took its copy - but whoever is signed in to a login can "
                    "connect a chat, and the member is not emailed when one is, so a chat that took it may "
                    "not be the member's.")
POINTER = ("The full address is in this notice's failed delivery row (failed_at); the overview's "
           "\"Failed email deliveries\" card shows it while it is among the latest 20.")


# --- stand-ins -----------------------------------------------------------------------------


class _Pager:
    """A recording operator alerter: every page, as its keywords."""

    def __init__(self):
        self.pages = []

    def page(self, **kw):
        self.pages.append(kw)
        return len(self.pages)


class _RaisingPager:
    """An operator alerter whose every page fails - the operator_pages write, say."""

    def __init__(self):
        self.calls = 0

    def page(self, **kw):
        self.calls += 1
        raise RuntimeError("the operator_pages write failed")


class _Texts:
    """`build_sms_channel` stand-in: every text, by its recipients."""

    sent: list = []

    def __init__(self, recipients, audience="customer"):
        self.recipients = list(recipients)

    def send(self, alert):
        _Texts.sent.append(self.recipients)
        return [{"to": r, "provider": "test", "provider_message_id": "1"} for r in self.recipients]


def _email_channel(failure):
    """An EmailChannel stand-in that raises `failure` on every send, or
    accepts and keeps the message when it is None."""

    class _Mail:
        sent: list = []

        def __init__(self, host, port, username, password, sender, recipients, **kw):
            self.recipients = list(recipients)

        def send(self, alert):
            if failure is not None:
                raise failure
            _Mail.sent.append((tuple(self.recipients), alert))

    return _Mail


def _provider(refusal=None):
    """`sentinel.net.smtp` stand-in - the real EmailChannel runs above it:
    keeps every message it is given, or refuses each one with `refusal`."""

    class _Provider:
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
            if refusal is not None:
                raise refusal
            _Provider.messages.append(msg)

    return _Provider


def _smtp_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setenv("SMTP_SENDER", "guardian@example.invalid")


def _router(db, monkeypatch, failure, alerter):
    """A router with SMTP configured, its email leg the stand-in above."""
    from sentinel.alerts.contact import ContactRouter

    _smtp_env(monkeypatch)
    mail = _email_channel(failure)
    monkeypatch.setattr("sentinel.alerts.contact.EmailChannel", mail)
    return ContactRouter(db=db, operator_alerter=alerter), mail


def _notice(rule, label=LOGIN_LABEL):
    """A notice as every sender builds one: an AlertMessage in the portal's adapter."""
    from sentinel.alerts.base import AlertMessage
    from sentinel.portal import _PlainNotice

    return _PlainNotice(AlertMessage(account_label=label, rule=rule, severity="LOW", confidence="n/a",
                                     message=f"The {rule} notice.", evidence={}))


def _label(rule):
    return "Main" if rule == "alert_destination_changed" else LOGIN_LABEL


def _pages(db, rule=PAGE):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM operator_pages WHERE rule = ? ORDER BY id", (rule,))
        return [dict(r) for r in cur.fetchall()]


def _deliveries(db, rule):
    """The delivery record of one notice, leg by leg: (channel, recipient, status)."""
    with db.cursor() as cur:
        cur.execute("SELECT channel, recipient, status FROM deliveries WHERE rule = ? ORDER BY id", (rule,))
        return [tuple(r) for r in cur.fetchall()]


def _real_alerter(db):
    """The real OperatorAlerter, its CRITICAL route one recording channel."""
    from sentinel.alerts.operator import OperatorAlerter
    from sentinel.config import RulesConfig

    channel = Recording()
    return OperatorAlerter(db, {"CRITICAL": [channel]}, RulesConfig(rules_yaml())), channel


def _account(db, label, email):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', '2026-01-01', '2026-01-01')",
            (label, email),
        )
        return cur.lastrowid


def _primary(db, label):
    with db.cursor() as cur:
        cur.execute("SELECT alert_email FROM accounts WHERE label = ?", (label,))
        return cur.fetchone()["alert_email"]


def _free_port():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def portal(db):
    """Starts a member portal with whatever serve_portal is given; every one
    started is shut down after the test."""
    from sentinel.portal import serve_portal

    servers = []

    def start(**kw):
        port = _free_port()
        servers.append(serve_portal(db, port, **kw))
        return "127.0.0.1", port

    yield start
    for server in servers:
        server.shutdown()


@pytest.fixture
def telegram(db, monkeypatch):
    """Alice's own chat, connected through the real handshake (#18), the bot
    token set, and every Bot API call recorded as (url, payload)."""
    from sentinel import net
    from sentinel.alerts.telegram_link import confirm_code, create_link_code

    posts: list[tuple[str, dict]] = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": []}

    def fake_post(url, **kw):
        posts.append((url, kw.get("json") or {}))
        return _Resp()

    monkeypatch.setattr(net, "post", fake_post)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    assert confirm_code(db, create_link_code(db, EMAIL), "777") == EMAIL
    return posts


# --- 26c: the reset link goes to the mailbox only ----------------------------------------


def test_a_reset_request_mails_a_link_that_redeems_and_posts_it_to_no_chat(db, monkeypatch, portal, telegram):
    """The member has Telegram connected. Their reset request still mails
    them a link that works - and nothing is posted to the chat, because a
    link in the chat is a login for whoever holds it, no mailbox needed.
    The tripwire the reset then owes is not email-only: it is the one
    message on that path that reaches the chat."""
    from sentinel import net, outbox
    from sentinel.portal import check_member_login, set_member_password

    set_member_password(db, EMAIL, "old-password")
    _smtp_env(monkeypatch)
    provider = _provider()
    monkeypatch.setattr(net, "smtp", provider)
    host_port = portal()

    status, _, _ = exchange(host_port, "POST", "/forgot", body={"email": EMAIL})
    assert status == 200
    assert outbox.wait_until_sent(10), "the reset email never went"
    [message] = provider.messages
    assert message["To"] == EMAIL
    token = message.get_content().split("/reset?token=", 1)[1].split()[0]
    assert telegram == [], (
        "the reset link reached the member's Telegram chat: whoever holds that chat can take over "
        "the login without the mailbox the link is meant to prove")

    status, response, _ = exchange(host_port, "POST", "/reset",
                                   body={"token": token, "password": "brand-new-password"})
    assert status == 303 and response.get("Location") == "/login"
    assert check_member_login(db, EMAIL, "brand-new-password") is True, "the emailed link redeems"
    [(_, payload)] = telegram
    assert "was just changed" in payload["text"] and "/reset?token=" not in payload["text"]


@pytest.mark.parametrize("sender, words", [
    ("_send_password_changed_email", "was just changed"),
    ("_send_two_factor_disabled_email", "was just turned off"),
], ids=["password_changed", "two_factor_disabled"])
def test_a_security_tripwire_still_reaches_the_connected_chat(db, telegram, sender, words):
    """What must not change: only the reset link is email-only. A tripwire
    goes to every channel the member has - the one that still works is the
    point of it."""
    import sentinel.portal as portal_module

    getattr(portal_module, sender)(db, EMAIL)

    [(url, payload)] = telegram
    assert url.endswith("/sendMessage") and payload["chat_id"] == "777"
    assert words in payload["text"]


@pytest.mark.parametrize("email_only", [True, False], ids=["email-only", "every-channel"])
def test_an_email_only_notice_gets_no_telegram_copy_and_no_text(db, monkeypatch, telegram, email_only):
    """The router's half of 26c: a notice marked email-only goes to the
    mailbox and to no other channel the address has - no Telegram copy, no
    text - and one that is not keeps all three."""

    class _Notice:
        is_notice = True
        rule = "password_reset_requested"
        severity = "LOW"
        confidence = "n/a"
        message = "Open this link to choose a new password."
        evidence: dict = {}

    _Notice.email_only = email_only
    monkeypatch.setattr(_Texts, "sent", [])
    monkeypatch.setattr("sentinel.alerts.contact.build_sms_channel",
                        lambda recipients, audience="customer": _Texts(recipients))
    router, mail = _router(db, monkeypatch, None, None)

    router.send(_Notice(), LOGIN_LABEL, [EMAIL], ["+15550001111"])

    assert [to for to, _ in mail.sent] == [(EMAIL,)], "the mailbox gets it either way"
    assert (telegram == []) is email_only, "Telegram copy"
    assert (_Texts.sent == []) is email_only, "text message"


# --- 26b: a refused tripwire pages Minas, at the router ------------------------------------


@pytest.mark.parametrize("rule", TRIPWIRES)
def test_a_refused_tripwire_pages_minas_at_critical(db, monkeypatch, rule):
    """No Telegram chat is connected, so the page says the member was told
    nowhere, and telegram_accepted is 0. Here the login opens no account;
    test_a_login_tripwire_page_names_the_accounts_the_login_opens gives it
    some."""
    pager = _Pager()
    router, _ = _router(db, monkeypatch, QUOTA, pager)

    router.send(_notice(rule, _label(rule)), _label(rule), [EMAIL], [])

    assert [p["rule"] for p in pager.pages] == [PAGE]
    page = pager.pages[0]
    assert page["severity"] == "CRITICAL", "the same footing as customer_email_refused"
    evidence = dict(page["evidence"])
    failed_at = evidence.pop("failed_at")
    assert datetime.fromisoformat(failed_at).utcoffset() == timedelta(0), "failed_at is UTC"
    expected = {"notice": rule, "recipient": MASKED, "smtp_code": 550, "detail": str(QUOTA),
                "account_label": _label(rule), "telegram_accepted": 0}
    if rule in LOGIN_TRIPWIRES:
        expected["accounts"] = []
    assert evidence == expected
    message = page["message"]
    assert message.startswith("A security notice's email did not reach the member: "), (
        "the email, not the notice: a connected chat may still have taken its copy")
    assert f"The member has NOT been told that {UNTOLD[rule]}, by email or on Telegram." in message
    assert f"the {rule} notice about '{_label(rule)}' to {MASKED} was REFUSED by the email provider " \
           "with SMTP 550: " in message
    assert "daily email sending quota" in message, "the provider's words"
    assert "Reach the member another way; if they did not make this change, treat it as an incident." in message
    assert TELEGRAM_REASON in message, "why a page about email may arrive somewhere else"
    assert message.endswith(" " + POINTER), "the last word: where the full address is, once the card moves on"
    assert ("That login opens no account." in message) is (rule in LOGIN_TRIPWIRES)
    assert EMAIL not in message


def test_a_tripwire_that_never_reached_the_provider_pages_nobody(db, monkeypatch):
    """26b as the owner approved it is a REFUSED tripwire. A send that never
    reached the provider - the connection refused, a timeout - is the dead
    wire #58 made a log line for an alert: nothing refused the notice, the
    owner was asked about refusals only, and the failed row still says
    what happened."""
    pager = _Pager()
    router, _ = _router(db, monkeypatch, DEAD_WIRE, pager)

    router.send(_notice("password_changed"), LOGIN_LABEL, [EMAIL], [])

    assert pager.pages == []
    assert _deliveries(db, "password_changed") == [("email", EMAIL, "failed")]


def test_a_refused_tripwire_whose_telegram_copy_was_accepted_still_pages_and_says_so(db, monkeypatch, telegram):
    """A chat connected to the login took the copy. That is not "the member
    was warned": whoever is signed in to a login can connect a chat, and
    the member is not emailed when one is, so the chat may be the
    attacker's. The page goes, as the owner approved it, and says what
    Telegram did."""
    pager = _Pager()
    router, _ = _router(db, monkeypatch, QUOTA, pager)

    router.send(_notice("password_changed"), LOGIN_LABEL, [EMAIL], [])

    [page] = pager.pages
    assert page["rule"] == PAGE and page["evidence"]["telegram_accepted"] == 1
    assert page["message"].startswith("A security notice's email did not reach the member: ")
    assert TOLD_ON_TELEGRAM in page["message"]
    assert "by email or on Telegram" not in page["message"]
    assert _deliveries(db, "password_changed") == [("email", EMAIL, "failed"), ("telegram", "777", "accepted")]


def test_two_connected_chats_that_took_the_copy_are_counted_as_two(db, monkeypatch, telegram):
    """The page says how many chats took the copy, not whether one did: a
    second chat is a second copy that may not be the member's."""
    from sentinel.alerts.telegram_link import confirm_code, create_link_code

    assert confirm_code(db, create_link_code(db, EMAIL), "666") == EMAIL
    pager = _Pager()
    router, _ = _router(db, monkeypatch, QUOTA, pager)

    router.send(_notice("password_changed"), LOGIN_LABEL, [EMAIL], [])

    [page] = pager.pages
    assert page["evidence"]["telegram_accepted"] == 2
    assert ". 2 Telegram chat(s) connected to this login took its copy" in page["message"]
    assert len(telegram) == 2


@pytest.mark.parametrize("answer", ["http-403", "no-answer"])
def test_a_refused_tripwire_whose_telegram_copy_failed_pages_once(db, monkeypatch, telegram, answer):
    """A connected chat that did not take the copy told nobody, and the
    page says so - whether Telegram answered 403 (the member blocked the
    bot: an answer, raised by raise_for_status) or never answered. Only an
    accepted post counts as a copy taken."""
    from sentinel import net

    class _Forbidden:
        def raise_for_status(self):
            raise RuntimeError("403 Client Error: Forbidden: bot was blocked by the user")

    def blocked(url, **kw):
        telegram.append((url, kw.get("json") or {}))
        if answer == "no-answer":
            raise ConnectionResetError(104, "Connection reset by peer")
        return _Forbidden()

    monkeypatch.setattr(net, "post", blocked)
    pager = _Pager()
    router, _ = _router(db, monkeypatch, QUOTA, pager)

    router.send(_notice("password_changed"), LOGIN_LABEL, [EMAIL], [])

    assert [p["rule"] for p in pager.pages] == [PAGE]
    assert pager.pages[0]["evidence"]["telegram_accepted"] == 0
    assert f"The member has NOT been told that {UNTOLD['password_changed']}, by email or on Telegram." \
        in pager.pages[0]["message"]
    assert _deliveries(db, "password_changed") == [("email", EMAIL, "failed"), ("telegram", "777", "failed")]
    assert len(telegram) == 1, "the copy was attempted"


def test_a_telegram_leg_that_raises_counts_as_no_copy(db, monkeypatch):
    """The Telegram leg is guarded at its call site; when it raises, no
    chat took the copy, and the page says the member was told nowhere."""

    def locked(db, emails):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("sentinel.alerts.telegram_link.chat_ids_for_emails", locked)
    pager = _Pager()
    router, _ = _router(db, monkeypatch, QUOTA, pager)

    router.send(_notice("password_changed"), LOGIN_LABEL, [EMAIL], [])

    assert [p["rule"] for p in pager.pages] == [PAGE]
    assert pager.pages[0]["evidence"]["telegram_accepted"] == 0
    assert "by email or on Telegram." in pager.pages[0]["message"]


def test_a_refused_tripwire_whose_chat_has_no_bot_token_pages(db, monkeypatch, telegram):
    """A connected chat with no bot token to post with took no copy: the
    missing-provider page (#58) goes, and the tripwire page says the member
    was told nowhere."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    pager = _Pager()
    router, _ = _router(db, monkeypatch, QUOTA, pager)

    router.send(_notice("password_changed"), LOGIN_LABEL, [EMAIL], [])

    assert [p["rule"] for p in pager.pages] == ["contact_channel_has_no_provider", PAGE]
    assert pager.pages[1]["evidence"]["telegram_accepted"] == 0
    assert "by email or on Telegram." in pager.pages[1]["message"]
    assert telegram == []


@pytest.mark.parametrize("rule", TRIPWIRES)
def test_an_accepted_tripwire_pages_nobody(db, monkeypatch, rule):
    pager = _Pager()
    router, mail = _router(db, monkeypatch, None, pager)

    router.send(_notice(rule, _label(rule)), _label(rule), [EMAIL], [])

    assert len(mail.sent) == 1 and pager.pages == []


def test_unconfigured_smtp_is_not_this_page(db):
    """SMTP unset is a different fact with its own signals - the row is
    'skipped' and the liveness gate already reads Email DEAD. What a notice
    router with an alerter raises then is the router's existing
    missing-provider page (#58), logged once per router and paged at most
    once per operator cooldown, as the engine's is."""
    from sentinel.alerts.contact import ContactRouter

    pager = _Pager()
    ContactRouter(db=db, operator_alerter=pager).send(_notice("password_changed"), LOGIN_LABEL, [EMAIL], [])

    assert [p["rule"] for p in pager.pages] == ["contact_channel_has_no_provider"]
    with db.cursor() as cur:
        cur.execute("SELECT status FROM deliveries WHERE channel = 'email' AND rule = 'password_changed'")
        assert [r["status"] for r in cur.fetchall()] == ["skipped"]


@pytest.mark.parametrize("rule", [
    "password_reset_requested",  # the reset link: self-correcting, the member asks again (26b option 2)
    "secondary_contact_added",   # the emergency-contact note: carries no warning
    "telegram_disconnected",     # calls itself a tripwire; the owner was not asked (26b)
    "app_signed_in",             # likewise (decision 21c's notice)
    "data_erased",
])
def test_a_refused_notice_outside_the_tripwires_pages_nobody(db, monkeypatch, rule):
    pager = _Pager()
    router, _ = _router(db, monkeypatch, QUOTA, pager)

    router.send(_notice(rule), LOGIN_LABEL, [EMAIL], [])

    assert pager.pages == []


def test_a_detection_named_like_a_tripwire_is_not_a_notice_and_pages_nobody(db, monkeypatch):
    """`is_notice` is half the decision: a LOW detection that shares a
    tripwire's rule name is an alert, judged by the alert rules."""
    pager = _Pager()
    router, _ = _router(db, monkeypatch, QUOTA, pager)
    detection = Detection(rule="two_factor_disabled", severity="LOW", confidence="high",
                          message="m", evidence={})

    router.send(detection, "acct-1", [EMAIL], [])

    assert pager.pages == []


def test_without_an_alerter_a_refused_tripwire_raises_nothing(db, monkeypatch, caplog):
    """The CLI's notices before 26b, and the dev server's still: no alerter,
    no page - and the send still records its failure and returns. Not even
    an attempt: a page tried on a missing alerter would fail inside the
    guard and log, and the log line is how this test would see it."""
    router, _ = _router(db, monkeypatch, QUOTA, None)

    router.send(_notice("password_changed"), LOGIN_LABEL, [EMAIL], [])

    with db.cursor() as cur:
        cur.execute("SELECT status FROM deliveries WHERE channel = 'email' AND rule = 'password_changed'")
        assert [r["status"] for r in cur.fetchall()] == ["failed"]
    assert "could not page" not in caplog.text


def test_a_failing_page_never_costs_the_text_or_the_caller(db, monkeypatch):
    """The page is attempted and fails; the leg after it still sends, and
    the caller is not handed the exception. (The Telegram copy goes before
    the page is decided, so a failing page cannot cost that one.)"""
    pager = _RaisingPager()
    monkeypatch.setattr(_Texts, "sent", [])
    monkeypatch.setattr("sentinel.alerts.contact.build_sms_channel",
                        lambda recipients, audience="customer": _Texts(recipients))
    router, _ = _router(db, monkeypatch, QUOTA, pager)

    router.send(_notice("password_changed"), LOGIN_LABEL, [EMAIL], ["+15550001111"])

    assert pager.calls == 1, "the page was attempted - this test is about surviving its failure"
    assert _Texts.sent == [["+15550001111"]]


def test_a_failing_missing_provider_page_never_costs_the_tripwires_telegram_copy(db, telegram):
    """SMTP unset, so the email leg is skipped and the member's connected
    chat is the one channel the tripwire has. The router's
    missing-provider page (#58) is raised before that leg, and since 26b a
    notice's router carries an alerter to raise it with: when the page
    fails, the copy must still go."""
    from sentinel.alerts.contact import ContactRouter

    pager = _RaisingPager()
    ContactRouter(db=db, operator_alerter=pager).send(_notice("password_changed"), LOGIN_LABEL, [EMAIL], [])

    assert pager.calls == 1, "the missing-provider page was attempted, and failed"
    [(_, payload)] = telegram
    assert payload["chat_id"] == "777"


def test_one_members_repeat_is_one_page_and_two_members_are_two(db, monkeypatch):
    """Through the real OperatorAlerter, whose cooldown does the deduplicating:
    the scope is the notice and the address it was for."""
    alerter, channel = _real_alerter(db)
    router, _ = _router(db, monkeypatch, QUOTA, alerter)

    router.send(_notice("password_changed"), LOGIN_LABEL, [EMAIL], [])
    router.send(_notice("password_changed"), LOGIN_LABEL, ["Alice@Example.com"], [])
    assert len(_pages(db)) == 1, "one member's repeat inside the cooldown is one page, whatever the casing"
    router.send(_notice("password_changed"), LOGIN_LABEL, ["bob@example.com"], [])
    assert len(_pages(db)) == 2, "a second member is a second page"
    router.send(_notice("two_factor_disabled"), LOGIN_LABEL, [EMAIL], [])
    assert len(_pages(db)) == 3, "a second tripwire for the same member is a fact of its own"
    assert [json.loads(p["evidence_json"])["recipient"] for p in _pages(db)] == [MASKED, "bo***@example.com", MASKED]
    assert len(channel.sent) == 3


def test_two_accounts_moved_off_one_address_are_two_pages(db, monkeypatch):
    """The account is in the scope too. 'Savings' and then 'Trading' moved
    away from one address inside the cooldown are two facts - collapsing
    them would report the first and hide the second, the shape _dedup_key
    exists to refuse - and Trading's repeat is still one."""
    alerter, _ = _real_alerter(db)
    router, _ = _router(db, monkeypatch, QUOTA, alerter)

    router.send(_notice("alert_destination_changed", "Savings"), "Savings", [EMAIL], [])
    router.send(_notice("alert_destination_changed", "Trading"), "Trading", [EMAIL], [])
    router.send(_notice("alert_destination_changed", "Trading"), "Trading", [EMAIL], [])

    assert [json.loads(p["evidence_json"])["account_label"] for p in _pages(db)] == ["Savings", "Trading"]


def test_one_send_to_two_recipients_is_two_pages(db, monkeypatch):
    """One page per recipient, each masked: no sender passes two addresses
    today, and the router's promise holds if one ever does."""
    pager = _Pager()
    router, _ = _router(db, monkeypatch, QUOTA, pager)

    router.send(_notice("password_changed"), LOGIN_LABEL, [EMAIL, "bob@example.com"], [])

    assert [p["evidence"]["recipient"] for p in pager.pages] == [MASKED, "bo***@example.com"]
    for p in pager.pages:
        assert EMAIL not in p["message"] and "bob@example.com" not in p["message"]


def test_the_page_never_carries_the_full_address(db, monkeypatch):
    """A page goes to Telegram, SMS and the ops mailbox. A refusal quotes the
    address back - SMTPRecipientsRefused is a dict keyed by it, and the
    server's own words name it, here in another casing - and an operator may
    have labelled the account with it. None of the three reaches the page."""
    refusal = smtplib.SMTPRecipientsRefused({"Alice@Example.com": (
        550, b"5.1.1 <alice@example.com>: Recipient address rejected: User unknown")})
    alerter, channel = _real_alerter(db)
    router, _ = _router(db, monkeypatch, refusal, alerter)
    label = "alice@example.com savings, joint with bob.jones@example.org"

    router.send(_notice("alert_destination_changed", label), label, ["Alice@Example.com"], [])

    [row] = _pages(db)
    [alert] = channel.sent
    for text in (row["message"], row["evidence_json"], alert.render()):
        assert "alice@example.com" not in text.lower()
        assert "bob.jones@example.org" not in text.lower(), "any other address is masked too"
    evidence = json.loads(row["evidence_json"])
    assert evidence["recipient"] == "Al***@Example.com"
    assert evidence["smtp_code"] == 550
    assert evidence["account_label"] == "Al***@Example.com savings, joint with bo***@example.org"
    assert "<Al***@Example.com>: Recipient address rejected" in evidence["detail"], (
        "the server's words survive, the address in them masked")


@pytest.mark.parametrize("address, local, masked_local", [
    (EMAIL, "alice", "al***"),
    ("bob@example.com", "bob", "bo***"),  # three characters is enough to be masked
    ("Alice@Example.com", "alice", "Al***"),  # the server's casing is not the recipient's
])
def test_a_server_reply_naming_the_local_part_never_spells_the_address(db, monkeypatch, address, local,
                                                                     masked_local):
    """"550 5.1.1 User alice unknown" beside al***@example.com spells the
    address on every operator channel. The local part standing alone is
    masked; inside another word it is that word."""
    refusal = smtplib.SMTPRecipientsRefused({address: (550, f"5.1.1 User {local} unknown; no malice".encode())})
    pager = _Pager()
    router, _ = _router(db, monkeypatch, refusal, pager)

    router.send(_notice("password_changed"), LOGIN_LABEL, [address], [])

    [page] = pager.pages
    for text in (page["message"], json.dumps(page["evidence"])):
        assert f" {local} " not in text and f"'{local}" not in text, text
    assert f"5.1.1 User {masked_local} unknown; no malice" in page["evidence"]["detail"]


@pytest.mark.parametrize("address, reply, shown", [
    ("alice+minas@example.com", b"5.1.1 User alice unknown", "5.1.1 User al*** unknown"),
    ("josé@example.com", "5.1.1 <josé@example.com> unknown".encode(), "<jo***@example.com>"),
    ("josé@example.com", "5.1.1 User josé unknown".encode(), "5.1.1 User jo*** unknown"),
], ids=["plus-tag-base", "idn-bytes-repr", "idn-local-part-alone"])
def test_a_reply_spelling_the_address_another_way_is_masked(db, monkeypatch, address, reply, shown):
    """A refusal can spell the address otherwise than the member's row: a
    plus-addressed one by its base alone ("User alice" beside the mask of
    alice+minas@example.com spells the mailbox), and a non-ASCII one as the
    bytes repr of the server's reply ('jos\\xc3\\xa9@...'), whole or by its
    local part. Each spelling is masked."""
    pager = _Pager()
    router, _ = _router(db, monkeypatch, smtplib.SMTPRecipientsRefused({address: (550, reply)}), pager)

    router.send(_notice("password_changed"), LOGIN_LABEL, [address], [])

    [page] = pager.pages
    assert shown in page["evidence"]["detail"]
    for text in (page["message"], json.dumps(page["evidence"])):
        assert "alice unknown" not in text and "jos\\xc3" not in text and "jos\\\\xc3" not in text, text


def test_an_address_with_a_backslash_still_pages(db, monkeypatch):
    """Every mask goes in as a function, never as a template string: as a
    template, the mask of this address begins '\\1', reads as a reference
    to a group the pattern does not have, raises - and the page is lost."""
    address = "\\1lice@example.com"
    pager = _Pager()
    router, _ = _router(db, monkeypatch, QUOTA, pager)

    router.send(_notice("password_changed"), LOGIN_LABEL, [address], [])

    [page] = pager.pages
    assert page["evidence"]["recipient"] == "\\1***@example.com"
    assert address not in page["message"]


@pytest.mark.parametrize("rule", LOGIN_TRIPWIRES)
def test_a_login_tripwire_page_names_the_accounts_the_login_opens(db, monkeypatch, rule):
    """The two login notices go out under the login's label, so with the
    address masked nothing else in the page would say whose login it is.
    The accounts are read the way the portal reads them, masked like the
    rest of the page."""
    _account(db, "Savings", EMAIL)
    _account(db, "Main", EMAIL)
    _account(db, "alice@example.com trading", EMAIL)
    _account(db, "Someone else's", "carol@example.com")
    pager = _Pager()
    router, _ = _router(db, monkeypatch, QUOTA, pager)

    router.send(_notice(rule), LOGIN_LABEL, [EMAIL], [])

    [page] = pager.pages
    assert page["evidence"]["accounts"] == ["Main", "Savings", "al***@example.com trading"]
    assert "That login opens 'Main', 'Savings', 'al***@example.com trading'." in page["message"]
    assert EMAIL not in page["message"]


def test_a_login_whose_accounts_cannot_be_read_still_pages_and_says_so(db, monkeypatch, caplog):
    """The lookup is the page's garnish, never its condition - and a lookup
    that failed is not a login that opens nothing (None, not [])."""

    def locked(db, email):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("sentinel.portal.member_accounts", locked)
    pager = _Pager()
    router, _ = _router(db, monkeypatch, QUOTA, pager)

    router.send(_notice("two_factor_disabled"), LOGIN_LABEL, [EMAIL], [])

    [page] = pager.pages
    assert page["evidence"]["accounts"] is None
    assert "The accounts that login opens could not be read." in page["message"]
    assert "opens no account" not in page["message"]
    assert "could not read the accounts a login opens" in caplog.text


def test_a_login_opening_many_accounts_still_fits_one_telegram_message(db, monkeypatch):
    """A legacy login opens every account its address is a contact on, and
    a label has no length limit. The list is in the message and in the
    evidence, and the operator's Telegram refuses a message over 4096
    characters - in a refusal storm the channel that carries this page. So
    the page names ten, each cut to 60 characters, and says how many more."""
    description = "the long description an operator gave this sub-account, " * 3
    for i in range(60):
        _account(db, f"Account {i:03d}: {description}", EMAIL)
    alerter, channel = _real_alerter(db)
    router, _ = _router(db, monkeypatch, QUOTA, alerter)

    router.send(_notice("two_factor_disabled"), LOGIN_LABEL, [EMAIL], [])

    [row] = _pages(db)
    evidence = json.loads(row["evidence_json"])
    assert evidence["accounts"] == [f"Account {i:03d}: {description}"[:60] for i in range(10)]
    assert evidence["accounts_total"] == 60
    assert " and 50 more." in row["message"]
    [alert] = channel.sent
    assert len(alert.render()) <= 4096, "what the operator's Telegram is sent"


def test_a_login_opening_ten_accounts_names_all_ten(db, monkeypatch):
    """The boundary: ten is every account by name - no "and 0 more", no
    accounts_total."""
    for i in range(10):
        _account(db, f"Account {i:03d}", EMAIL)
    pager = _Pager()
    router, _ = _router(db, monkeypatch, QUOTA, pager)

    router.send(_notice("password_changed"), LOGIN_LABEL, [EMAIL], [])

    [page] = pager.pages
    assert page["evidence"]["accounts"] == [f"Account {i:03d}" for i in range(10)]
    assert "accounts_total" not in page["evidence"]
    named = ", ".join(f"'Account {i:03d}'" for i in range(10))
    assert f"That login opens {named}. Reach the member another way" in page["message"]


def test_the_page_points_at_the_notices_failed_rows_by_their_time(db, monkeypatch):
    """The overview's card lists the latest 20 failed emails of every kind,
    and a quota outage can scroll a tripwire off it in minutes. failed_at
    is the instant the notice's failed rows were written - one instant, not
    a second reading of the clock, which this test runs five seconds ahead
    for the delivery record to show it - and to the microsecond, so an
    equality on created_at finds the full address after the card has moved
    on."""
    from sentinel.alerts import delivery

    class _Ahead(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=5)

    monkeypatch.setattr(delivery, "datetime", _Ahead)
    pager = _Pager()
    router, _ = _router(db, monkeypatch, QUOTA, pager)

    router.send(_notice("password_changed"), LOGIN_LABEL, [EMAIL], [])

    [page] = pager.pages
    with db.cursor() as cur:
        cur.execute("SELECT recipient, created_at FROM deliveries "
                    "WHERE channel = 'email' AND status = 'failed' AND rule = 'password_changed'")
        [row] = cur.fetchall()
    assert row["recipient"] == EMAIL, "the full address is in the row"
    assert page["evidence"]["failed_at"] == row["created_at"], "failed_at is the row's created_at, exactly"


@pytest.mark.parametrize("address, masked", [
    ("alice@example.com", "al***@example.com"),
    ("Alice.Smith+guardian@Mail.Example.com", "Al***@Mail.Example.com"),
    ("ab@example.com", "a***@example.com"),     # never a whole local part
    ("a@example.com", "***@example.com"),
    ("  alice@example.com ", "al***@example.com"),
    ("not-an-address", "no***"),
    ("", "***"),
    (None, "***"),
])
def test_an_address_is_masked_to_its_first_characters_and_its_domain(address, masked):
    from sentinel.alerts.contact import mask_address

    assert mask_address(address) == masked


def test_the_tripwires_are_the_three_the_owner_decided():
    """Registration: 26b chose exactly these. telegram_disconnected and
    app_signed_in call themselves tripwires too, and stay out until the
    owner is asked. Adding one is not a line in TRIPWIRE_NOTICE_RULES alone:
    its sender builds its router with no operator alerter today, so it must
    be given one, and WIRED (at the end of this file) must name the test
    that shows its real path paging."""
    from sentinel.alerts.contact import TRIPWIRE_NOTICE_RULES

    assert set(TRIPWIRE_NOTICE_RULES) == set(TRIPWIRES)


# --- 26b, through the real wiring in every process -----------------------------------------


def test_a_refused_password_changed_notice_after_a_reset_pages_minas(db, monkeypatch, portal):
    """The portal: serve_portal's own alerter reaches the tripwire the reset
    sends. The reset stands whatever the notice did."""
    from sentinel import net
    from sentinel.portal import check_member_login, create_reset_token, set_member_password

    set_member_password(db, EMAIL, "old-password")
    _smtp_env(monkeypatch)
    monkeypatch.setattr(net, "smtp", _provider(refusal=smtplib.SMTPRecipientsRefused(
        {EMAIL: (550, b"5.1.1 <alice@example.com>: Recipient address rejected: User unknown")})))
    alerter, channel = _real_alerter(db)
    host_port = portal(operator_alerter=alerter)
    token = create_reset_token(db, EMAIL)

    status, response, _ = exchange(host_port, "POST", "/reset",
                                   body={"token": token, "password": "brand-new-password"})

    assert status == 303 and response.get("Location") == "/login"
    assert check_member_login(db, EMAIL, "brand-new-password") is True
    [page] = _pages(db)
    evidence = json.loads(page["evidence_json"])
    assert (evidence["notice"], evidence["recipient"], evidence["smtp_code"]) == ("password_changed", MASKED, 550)
    assert page["severity"] == "CRITICAL" and page["delivered_to"] == 1
    assert [a.rule for a in channel.sent] == [PAGE]


def test_a_refused_two_factor_disabled_notice_pages_minas(db, cipher, monkeypatch, portal,
                                                          alice_with_2fa):  # noqa: F811 - the fixture
    """The portal's two-factor removal, signed in with both factors, the
    next step's code switching it off."""
    from sentinel import net
    from sentinel.totp import STEP_SECONDS, totp_at
    from sentinel.twofactor import MEMBER, is_enabled

    secret, _ = alice_with_2fa
    _smtp_env(monkeypatch)
    monkeypatch.setattr(net, "smtp", _provider(refusal=QUOTA))
    alerter, channel = _real_alerter(db)
    host_port = portal(cipher=cipher, operator_alerter=alerter)
    _, body, _ = _post(host_port, "/login", {"email": ALICE, "password": PASSWORD})
    _, _, cookie = _post(host_port, "/login-code",
                         {"pending": _pending_from(body), "code": totp_at(secret, PINNED_TOTP_NOW)})
    assert cookie, "the sign-in this test depends on"

    _post(host_port, "/security/2fa/disable", {"code": totp_at(secret, PINNED_TOTP_NOW + STEP_SECONDS)},
          cookie=cookie)

    assert is_enabled(db, MEMBER, ALICE) is False
    [page] = _pages(db)
    evidence = json.loads(page["evidence_json"])
    assert (evidence["notice"], evidence["recipient"], evidence["smtp_code"]) == ("two_factor_disabled", MASKED, 550)
    assert [a.rule for a in channel.sent] == [PAGE]


def test_a_chat_the_session_connected_does_not_silence_the_two_factor_page(db, cipher, monkeypatch, portal,
                                                                          alice_with_2fa):  # noqa: F811
    """Whoever holds the session connects their OWN Telegram chat - the
    member is emailed nothing - and removes two-factor while the provider
    refuses mail. The one copy that arrived went to that chat: Minas is
    still paged, and told a chat took it."""
    from sentinel import net
    from sentinel.alerts.telegram_link import process_update
    from sentinel.totp import STEP_SECONDS, totp_at
    from sentinel.twofactor import MEMBER, is_enabled

    secret, _ = alice_with_2fa
    _smtp_env(monkeypatch)
    monkeypatch.setattr(net, "smtp", _provider(refusal=QUOTA))

    class _Resp:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(net, "post", lambda url, **kw: _Resp())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    alerter, channel = _real_alerter(db)
    host_port = portal(cipher=cipher, operator_alerter=alerter)
    _, body, _ = _post(host_port, "/login", {"email": ALICE, "password": PASSWORD})
    _, _, cookie = _post(host_port, "/login-code",
                         {"pending": _pending_from(body), "code": totp_at(secret, PINNED_TOTP_NOW)})
    assert cookie, "the sign-in this test depends on"
    _, security, _ = _post(host_port, "/telegram/link", {}, cookie=cookie)
    code = security.split("/start ", 1)[1].split("<", 1)[0]
    process_update(db, "tok", {"message": {"chat": {"id": 666}, "text": f"/start {code}"}})
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM deliveries")
        assert cur.fetchone()[0] == 0, "connecting a chat tells the member nothing"

    _post(host_port, "/security/2fa/disable", {"code": totp_at(secret, PINNED_TOTP_NOW + STEP_SECONDS)},
          cookie=cookie)

    assert is_enabled(db, MEMBER, ALICE) is False
    assert _deliveries(db, "two_factor_disabled") == [("email", ALICE, "failed"), ("telegram", "666", "accepted")]
    [page] = _pages(db)
    assert page["severity"] == "CRITICAL"
    assert json.loads(page["evidence_json"])["telegram_accepted"] == 1
    assert [a.rule for a in channel.sent] == [PAGE]


def _set_contact_env(db, monkeypatch, rules_path=None):
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    monkeypatch.setenv("SENTINEL_RULES_PATH", rules_path or rules_yaml())


def _move(label, email, **kw):
    return argparse.Namespace(label=label, alert_email=email, alert_phone=None, clear=False, **kw)


def test_set_contact_pages_minas_when_the_old_address_refuses_the_notice(db, monkeypatch):
    """The CLI: `sentinel set-contact` builds an alerter of its own, the way
    `sentinel drill` does, for the notice to the address being removed."""
    from sentinel import cli, net

    _account(db, "Main", EMAIL)
    _set_contact_env(db, monkeypatch)
    _smtp_env(monkeypatch)
    monkeypatch.setattr(net, "smtp", _provider(refusal=QUOTA))
    channel = Recording()
    monkeypatch.setattr(cli, "build_operator_channels", lambda rules: {"CRITICAL": [channel]})

    assert cli.cmd_set_contact(_move("Main", "new@example.com")) == 0

    assert _primary(db, "Main") == "new@example.com"
    [page] = _pages(db)
    evidence = json.loads(page["evidence_json"])
    assert (evidence["notice"], evidence["account_label"], evidence["recipient"]) == (
        "alert_destination_changed", "Main", MASKED)
    assert f"The member has NOT been told that {UNTOLD['alert_destination_changed']}, by email or on Telegram." \
        in page["message"]
    assert [a.rule for a in channel.sent] == [PAGE]


@pytest.mark.parametrize("breakage", ["rules-path-is-a-directory", "channels-raise"])
def test_set_contact_moves_and_notifies_when_paging_cannot_be_built(db, monkeypatch, capsys, tmp_path, breakage):
    """Paging is not the command: rules that cannot be read, or channels that
    cannot be built, cost one warning on stderr - never the move, never
    the notice, never the exit code."""
    from sentinel import cli, net

    _account(db, "Main", EMAIL)
    if breakage == "rules-path-is-a-directory":
        _set_contact_env(db, monkeypatch, rules_path=str(tmp_path))  # RulesConfig raises reading it
        cause = "Is a directory"
    else:
        _set_contact_env(db, monkeypatch)

        def broken(rules):
            raise ValueError("SMTP_PORT is not a number: 'abc'")

        monkeypatch.setattr(cli, "build_operator_channels", broken)
        cause = "SMTP_PORT is not a number"
    _smtp_env(monkeypatch)
    provider = _provider()
    monkeypatch.setattr(net, "smtp", provider)

    assert cli.cmd_set_contact(_move("Main", "new@example.com")) == 0

    assert _primary(db, "Main") == "new@example.com"
    [message] = provider.messages
    assert message["To"] == EMAIL and "new@example.com" in message.get_content()
    warnings = [line for line in capsys.readouterr().err.splitlines() if "without paging" in line]
    assert len(warnings) == 1 and cause in warnings[0], warnings
    assert _pages(db) == []


def test_a_missing_rules_file_still_pages_on_the_defaults(db, monkeypatch, capsys, tmp_path):
    """A rules file that is not there is not a failure to build: RulesConfig
    runs on the defaults, as `sentinel run` would, and the page is
    recorded - with nowhere to deliver it in this test's environment."""
    from sentinel import cli, net

    _account(db, "Main", EMAIL)
    _set_contact_env(db, monkeypatch, rules_path=str(tmp_path / "absent.yaml"))
    _smtp_env(monkeypatch)
    monkeypatch.setattr(net, "smtp", _provider(refusal=QUOTA))

    assert cli.cmd_set_contact(_move("Main", "new@example.com")) == 0

    assert [p["delivered_to"] for p in _pages(db)] == [0]
    assert "without paging" not in capsys.readouterr().err


def test_set_contact_builds_the_pager_only_when_the_notice_is_due(db, monkeypatch):
    """Built only when the destination-change notice is about to go: not for
    a phone restated beside the same email, an SMS toggle, or a first email
    with nobody to tell."""
    from sentinel import cli

    _account(db, "Main", EMAIL)
    _account(db, "Fresh", None)
    _set_contact_env(db, monkeypatch)
    built = []
    monkeypatch.setattr(cli, "build_operator_channels", lambda rules: built.append(rules) or {})

    assert cli.cmd_set_contact(argparse.Namespace(label="Main", alert_email=EMAIL, alert_phone="+15550001111",
                                                  clear=False)) == 0
    assert cli.cmd_set_contact(argparse.Namespace(label="Main", alert_email=None, alert_phone=None,
                                                  clear=False, sms_alerts="off")) == 0
    assert cli.cmd_set_contact(_move("Fresh", "first@example.com")) == 0
    assert built == [], "no destination-change notice was due"

    assert cli.cmd_set_contact(_move("Main", "new@example.com")) == 0
    assert len(built) == 1


# --- the registry's execution pair ---------------------------------------------------------

# Every tripwire, and the test that drives its REAL sender - the portal
# route or the CLI command, with the alerter that process builds - and sees
# the page (CONTRIBUTING: "Registration guards need execution guards").
WIRED = {
    "password_changed": test_a_refused_password_changed_notice_after_a_reset_pages_minas,
    "two_factor_disabled": test_a_refused_two_factor_disabled_notice_pages_minas,
    "alert_destination_changed": test_set_contact_pages_minas_when_the_old_address_refuses_the_notice,
}


def test_every_tripwire_has_a_test_of_its_real_path_paging():
    """A line in TRIPWIRE_NOTICE_RULES is a claim about intent; the router
    honours it only for a sender that hands its router an operator alerter,
    and the two left out today build theirs with none. So a new rule fails
    here until it has an execution test of its own - its real sender, a
    refusing provider, a page row, the rule named in it - and that test is
    named in WIRED. A test shared with another rule, or one that drives the
    router instead of the sender, does not count: either would keep this
    green while the new rule's real path paged nobody."""
    from sentinel.alerts.contact import TRIPWIRE_NOTICE_RULES

    assert set(WIRED) == set(TRIPWIRE_NOTICE_RULES)
    assert len(set(WIRED.values())) == len(WIRED), "one execution test per tripwire, never a shared one"
    for rule, test in WIRED.items():
        assert callable(test) and test.__name__.startswith("test_") and test.__module__ == __name__, rule
        source = inspect.getsource(test)
        assert f'"{rule}"' in source, f"{test.__name__} never names {rule}, so it cannot be asserting its page"
        assert "_router(" not in source and "ContactRouter(" not in source, (
            f"{test.__name__} drives the router, not {rule}'s real sender")
