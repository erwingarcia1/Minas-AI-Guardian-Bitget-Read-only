"""The structural half of the SMS-body fix: NO customer-facing channel may
emit raw `alert.message`, and no customer-facing body may contain a URL.

Email was fixed with render_customer() while SMS kept shipping the raw
detector message - amounts, addresses, a link - with nothing catching it,
because the rule lived in one channel's implementation instead of over all
of them. This is the guard in the house style (registration + execution):

  registration: every AlertChannel subclass in sentinel/ must be claimed
    below as customer-capable or operator-only-with-a-reason. A channel
    added later that is in neither list FAILS HERE before its first send.
  execution: every customer-capable channel is driven with a poisoned
    detection - canary text, an amount, an address, a URL - through a
    stubbed transport, and what would leave the building is asserted
    clean: no canary, no fragment of the raw message, and no URL.

The customer Telegram leg lives in ContactRouter rather than a channel
class; it is driven the same way below so the guard covers every
customer-facing body, not every subclass.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

import sentinel.alerts
from sentinel.alerts.base import AlertChannel, AlertMessage

CANARY = "CANARY-RAW-MESSAGE-7f3a"
POISON = AlertMessage(
    account_label="acct-g",
    rule="withdrawal_to_unseen_address",
    severity="CRITICAL",
    confidence="high",
    message=f"{CANARY} withdrawal of 1250.0 USDT to 0xDeadBeefCafe1234 https://evil.example/x",
    evidence={"address": "0xDeadBeefCafe1234"},
)

# Channel class -> how the test captures its outgoing customer body.
# Adding a customer-capable channel means adding a capture here - which is
# the point: you cannot wire a customer channel without facing this guard.
CUSTOMER_CAPABLE = {
    "EmailChannel": "smtp",
    "ClickSendSmsChannel": "sms_post",
    "TwilioSmsChannel": "sms_post",
}

# Channel class -> why customers never receive it. A reason, not a waiver:
# if one of these is ever pointed at customers, move it up and it gets the
# poison treatment.
OPERATOR_ONLY = {
    "TelegramChannel": (
        "constructed only from TELEGRAM_CHAT_ID / TELEGRAM_TEAM_CHAT_ID - "
        "Minas's own chats. The CUSTOMER telegram leg is ContactRouter."
        "_send_telegram, which renders customer copy and is driven below."
    ),
}


def _all_channel_classes():
    found = {}
    for module_info in pkgutil.iter_modules(sentinel.alerts.__path__):
        module = importlib.import_module(f"sentinel.alerts.{module_info.name}")
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, AlertChannel) and obj is not AlertChannel \
                    and obj.__module__.startswith("sentinel.alerts"):
                found[name] = obj
    return found


def _assert_clean(body: str, where: str) -> None:
    assert CANARY not in body, f"{where} emits raw alert.message"
    assert "1250.0" not in body, f"{where} leaks an amount from the raw message"
    assert "0xDeadBeefCafe1234" not in body, f"{where} leaks an address"
    assert "http" not in body.lower(), f"{where} carries a URL"


# --- registration -------------------------------------------------------------

def test_every_channel_class_is_claimed_by_exactly_one_list():
    found = set(_all_channel_classes())
    claimed = set(CUSTOMER_CAPABLE) | set(OPERATOR_ONLY)
    assert found - claimed == set(), (
        f"unclaimed AlertChannel subclass(es): {sorted(found - claimed)} - "
        "a channel nobody classified is a channel that ships raw alert.message "
        "the day someone routes a customer through it. Claim it above.")
    assert claimed - found == set(), (
        f"stale claims for channels that no longer exist: {sorted(claimed - found)}")
    assert set(CUSTOMER_CAPABLE) & set(OPERATOR_ONLY) == set()


# --- execution: what would actually leave, asserted clean ---------------------

def test_customer_email_body_is_clean(monkeypatch):
    from sentinel.alerts.email import EmailChannel

    sent = {}

    class _Smtp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            sent["body"] = msg.get_content()
            sent["subject"] = msg["Subject"]

    monkeypatch.setattr("sentinel.alerts.email.net.smtp", lambda *a, **k: _Smtp())
    EmailChannel("h", 25, "u", "p", "s@x", ["c@x"], audience="customer").send(POISON)
    _assert_clean(sent["body"], "customer email body")
    _assert_clean(sent["subject"], "customer email subject")


@pytest.mark.parametrize("audience", ["customer", "operator"])
def test_sms_bodies_are_clean_for_both_audiences(monkeypatch, audience):
    """SMS is stricter than email for BOTH audiences: an operator's lock
    screen must not show a customer's amounts or addresses either. The
    difference is the rule name, which is Minas's own vocabulary."""
    from sentinel.alerts.clicksend import ClickSendSmsChannel
    from sentinel.alerts.sms import TwilioSmsChannel

    bodies = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response_code": "SUCCESS",
                    "data": {"messages": [{"status": "SUCCESS", "to": "+1", "message_id": "m"}]},
                    "sid": "m"}

    def capture(url, **kw):
        payload = kw.get("json") or {}
        if "messages" in payload:
            bodies.extend(m["body"] for m in payload["messages"])
        if "data" in kw and kw["data"].get("Body"):
            bodies.append(kw["data"]["Body"])
        return _Resp()

    monkeypatch.setattr("sentinel.alerts.sms.net.post", capture)
    monkeypatch.setattr("sentinel.alerts.clicksend.net.post", capture)

    ClickSendSmsChannel("u", "k", None, ["+1"], audience=audience).send(POISON)
    TwilioSmsChannel("sid", "tok", "+0", ["+1"], audience=audience).send(POISON)

    assert len(bodies) == 2
    for body in bodies:
        _assert_clean(body, f"{audience} SMS body")
    if audience == "customer":
        for body in bodies:
            assert "withdrawal_to_unseen_address" not in body, (
                "internal rule names never reach a customer's lock screen")
            assert "Sign in to the Minas portal" in body
    else:
        for body in bodies:
            assert "withdrawal_to_unseen_address" in body, (
                "the rule name is the one thing an operator's pager needs")


def test_the_customer_telegram_leg_is_clean(db, monkeypatch):
    """Not an AlertChannel subclass - the customer telegram leg lives in
    ContactRouter - so it is driven here by name rather than by discovery."""
    from sentinel.alerts.contact import ContactRouter
    from sentinel.alerts.telegram_link import confirm_code, create_link_code

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES ('acct-g', 'bitget', 'independent', 'x', 'y', 'z', 'g@example.com', "
            "'active', '2026-01-01', '2026-01-01')")
    code = create_link_code(db, "g@example.com")
    confirm_code(db, code, "777")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")

    texts = []

    class _Resp:
        def raise_for_status(self):
            pass

    def capture(url, **kw):
        texts.append((kw.get("json") or {}).get("text", ""))
        return _Resp()

    monkeypatch.setattr("sentinel.net.post", capture)
    ContactRouter(db=db).send(POISON, "acct-g", ["g@example.com"], [])

    assert texts, "the connected chat should have been sent a copy"
    for text in texts:
        _assert_clean(text, "customer telegram body")


def test_the_rendered_customer_sms_is_exactly_the_conservative_copy():
    """The string itself, pinned - so a future 'helpful' addition of detail
    or a link is a test failure, not a review comment."""
    from sentinel.alerts.sms import format_sms_body

    body = format_sms_body(POISON, audience="customer")
    assert body == ("Minas AI Guardian CRITICAL alert on acct-g. "
                    "Sign in to the Minas portal to review. We never send links by SMS.")
