"""#101: a blank or whitespace TELEGRAM_BOT_TOKEN is UNSET everywhere Telegram
is read, and a clean token is untouched.

The property that matters is the router's: a member with a connected chat
and a whitespace token must get the one-shot "TELEGRAM_BOT_TOKEN is not
set" warning, not one failed post per chat per alert.
"""
from __future__ import annotations

import logging

import pytest

import sentinel.alerts.telegram_link as tl
from sentinel.alerts.contact import ContactRouter
from sentinel.alerts.telegram_link import create_link_code, process_update
from sentinel.cli import _build_named_channels
from tests.test_telegram_self_serve import EMAIL, _member_account, _start_update

TELEGRAM = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_TEAM_CHAT_ID")


def _clear(monkeypatch):
    for name in TELEGRAM + ("SMTP_HOST", "SMTP_SENDER", "SMTP_RECIPIENTS", "CLICKSEND_USERNAME",
                            "CLICKSEND_API_KEY", "CLICKSEND_TO_NUMBERS", "TWILIO_ACCOUNT_SID",
                            "TWILIO_TO_NUMBERS"):
        monkeypatch.delenv(name, raising=False)


def _connect_a_chat(db, monkeypatch):
    _member_account(db)
    code = create_link_code(db, EMAIL)

    class _OkResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": []}

    monkeypatch.setattr(tl.net, "post", lambda *a, **k: _OkResp())
    process_update(db, "tok", _start_update(code))


# --- the property: the router says so, once -------------------------------------

@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_whitespace_token_leaves_the_not_set_warning_and_posts_nothing(db, monkeypatch, caplog, blank):
    from tests.test_sms_notifications import _detection

    _clear(monkeypatch)
    _connect_a_chat(db, monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", blank)
    posts: list = []

    def tripwire(*a, **k):
        posts.append(a)
        raise AssertionError("no Bot API call may be made on a half-filled config")

    monkeypatch.setattr("sentinel.net.post", tripwire)
    router = ContactRouter(db=db)
    with caplog.at_level(logging.WARNING, logger="sentinel.alerts.contact"):
        router.send(_detection(severity="CRITICAL"), "acct-1", [EMAIL], [])
    assert any("TELEGRAM_BOT_TOKEN is not set" in r.message for r in caplog.records)
    assert posts == []


def test_a_clean_token_still_posts_to_the_connected_chat(db, monkeypatch):
    """What did not change: the live path, driven end to end."""
    from tests.test_sms_notifications import _detection

    _clear(monkeypatch)
    _connect_a_chat(db, monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF")
    posts: list = []

    class _Ok:
        def raise_for_status(self):
            pass

    monkeypatch.setattr("sentinel.net.post", lambda url, **k: posts.append(url) or _Ok())
    ContactRouter(db=db).send(_detection(severity="CRITICAL"), "acct-1", [EMAIL], [])
    assert posts == ["https://api.telegram.org/bot123456:ABC-DEF/sendMessage"]


# --- the cli builder -------------------------------------------------------------

def test_neither_ops_channel_is_built_on_a_whitespace_token(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "  ")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001")
    monkeypatch.setenv("TELEGRAM_TEAM_CHAT_ID", "-1002")
    named = _build_named_channels()
    assert "telegram" not in named and "telegram_team" not in named


def test_a_whitespace_chat_id_builds_no_channel_either(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", " ")
    assert "telegram" not in _build_named_channels()


def test_clean_values_build_the_same_channels_as_before(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001")
    monkeypatch.setenv("TELEGRAM_TEAM_CHAT_ID", "-1002")
    named = _build_named_channels()
    assert (named["telegram"]._bot_token, named["telegram"]._chat_id) == ("123456:ABC-DEF", "-1001")
    assert (named["telegram_team"]._bot_token, named["telegram_team"]._chat_id) == ("123456:ABC-DEF", "-1002")


def test_padded_values_reach_the_channel_stripped(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", " 123456:ABC-DEF\t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001 ")
    channel = _build_named_channels()["telegram"]
    assert (channel._bot_token, channel._chat_id) == ("123456:ABC-DEF", "-1001")


def test_no_telegram_read_is_left_on_the_raw_helper():
    import inspect

    import sentinel.alerts.contact as contact
    import sentinel.cli as cli
    for module in (cli, contact):
        src = inspect.getsource(module)
        for line in src.splitlines():
            if "TELEGRAM_" in line and ("_env_raw(" in line or "os.environ.get(" in line):
                raise AssertionError(f"a Telegram read bypasses env_setting: {line.strip()}")
