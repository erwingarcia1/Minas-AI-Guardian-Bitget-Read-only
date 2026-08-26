"""#100: blank and whitespace environment values are UNSET for the settings
that decide whether a channel is configured, and clean values are untouched.

The property that matters is the router's: a whitespace SMTP_HOST with
emails on file must leave "SMTP is not configured" in the log, not one
silent failure per email. The helper tests are the mechanism; the router
test would still fail if a refactor got the mechanism right and the
outcome wrong.
"""
from __future__ import annotations

import logging

import pytest

from sentinel.alerts.contact import ContactRouter
from sentinel.cli import _build_named_channels
from sentinel.config import env_setting
from tests.conftest import make_account

SMTP = ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_SENDER", "SMTP_RECIPIENTS")
CLEAN = {"SMTP_HOST": "smtp.example.com", "SMTP_PORT": "2525", "SMTP_USERNAME": "user@example.com",
         "SMTP_PASSWORD": "p4ss", "SMTP_SENDER": "guardian@example.com",
         "SMTP_RECIPIENTS": "ops@example.com, va@example.com"}


def _clear(monkeypatch, names=SMTP):
    for name in names:
        monkeypatch.delenv(name, raising=False)
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_TEAM_CHAT_ID",
                 "CLICKSEND_USERNAME", "CLICKSEND_API_KEY", "CLICKSEND_TO_NUMBERS",
                 "TWILIO_ACCOUNT_SID", "TWILIO_TO_NUMBERS"):
        monkeypatch.delenv(name, raising=False)


# --- the helper ----------------------------------------------------------------

@pytest.mark.parametrize("blank", ["", " ", "   ", "\t", "\n"])
def test_blank_and_whitespace_are_unset(monkeypatch, blank):
    monkeypatch.setenv("X_SETTING", blank)
    assert env_setting("X_SETTING") is None
    assert env_setting("X_SETTING", "587") == "587"


def test_absent_is_the_default(monkeypatch):
    monkeypatch.delenv("X_SETTING", raising=False)
    assert env_setting("X_SETTING") is None
    assert env_setting("X_SETTING", "") == ""


def test_a_padded_value_comes_back_stripped(monkeypatch):
    monkeypatch.setenv("X_SETTING", "  smtp.example.com \t")
    assert env_setting("X_SETTING") == "smtp.example.com"


@pytest.mark.parametrize("clean", ["smtp.example.com", "2525", "p4ss with space inside", "123456:ABC-DEF", "-1001"])
def test_a_clean_value_is_returned_byte_identical(monkeypatch, clean):
    """Strip is the identity on a correctly configured value - the guarantee
    that live channels are built from exactly what they were built from."""
    monkeypatch.setenv("X_SETTING", clean)
    assert env_setting("X_SETTING") == clean
    assert env_setting("X_SETTING") is not None


# --- the property: the router says so, once -------------------------------------

def _router_send(db, rules, caplog, sent):
    """Drive a real router with emails on file; `sent` records any send."""
    from sentinel.alerts import contact as contact_mod
    from tests.test_sms_notifications import _detection, _stored_alert

    class _NoSend:
        def __init__(self, *a, **k):
            sent.append(("built", a))

        def send(self, alert):
            sent.append(("sent", alert))
            return []

    router = ContactRouter(db=db, rules=rules)
    account_id = make_account(db)
    detection = _detection(severity="CRITICAL")
    alert_id = _stored_alert(db, account_id, detection)
    with caplog.at_level(logging.WARNING, logger="sentinel.alerts.contact"):
        original = contact_mod.EmailChannel
        contact_mod.EmailChannel = _NoSend
        try:
            router.send(detection, "acct-1", ["owner@example.com"], [],
                        account_id=account_id, alert_id=alert_id)
        finally:
            contact_mod.EmailChannel = original
    return router


def test_a_whitespace_smtp_host_leaves_the_not_configured_warning_not_a_silent_failure(
        db, rules, monkeypatch, caplog):
    _clear(monkeypatch)
    monkeypatch.setenv("SMTP_HOST", "   ")
    monkeypatch.setenv("SMTP_SENDER", "guardian@example.com")
    sent: list = []
    router = _router_send(db, rules, caplog, sent)
    assert router._smtp is None
    assert any("SMTP is not configured" in r.message for r in caplog.records)
    assert sent == [], "no channel may be built on a half-filled config"


def test_a_whitespace_smtp_sender_is_the_same_story(db, rules, monkeypatch, caplog):
    _clear(monkeypatch)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_SENDER", "\t")
    sent: list = []
    _router_send(db, rules, caplog, sent)
    assert any("SMTP is not configured" in r.message for r in caplog.records)
    assert sent == []


def test_a_clean_smtp_config_builds_the_same_dict_as_before(monkeypatch):
    _clear(monkeypatch)
    for k, v in CLEAN.items():
        monkeypatch.setenv(k, v)
    assert ContactRouter._smtp_config() == {
        "host": "smtp.example.com", "port": 2525, "username": "user@example.com",
        "password": "p4ss", "sender": "guardian@example.com"}


def test_padded_smtp_values_reach_the_router_stripped(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("SMTP_HOST", " smtp.example.com ")
    monkeypatch.setenv("SMTP_SENDER", "guardian@example.com\t")
    monkeypatch.setenv("SMTP_USERNAME", " user ")
    monkeypatch.setenv("SMTP_PASSWORD", "  ")  # blank -> the "" default, as absent
    cfg = ContactRouter._smtp_config()
    assert cfg == {"host": "smtp.example.com", "port": 587, "username": "user",
                   "password": "", "sender": "guardian@example.com"}


# --- the ops email channel in the cli builder -----------------------------------

def test_the_ops_email_channel_is_not_built_on_a_whitespace_host(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("SMTP_HOST", " ")
    monkeypatch.setenv("SMTP_SENDER", "guardian@example.com")
    monkeypatch.setenv("SMTP_RECIPIENTS", "ops@example.com")
    assert "email" not in _build_named_channels()


def test_the_ops_email_channel_is_built_from_clean_values_unchanged(monkeypatch):
    _clear(monkeypatch)
    for k, v in CLEAN.items():
        monkeypatch.setenv(k, v)
    email = _build_named_channels()["email"]
    assert (email._host, email._port, email._username, email._password, email._sender) == (
        "smtp.example.com", 2525, "user@example.com", "p4ss", "guardian@example.com")
    assert email._recipients == ["ops@example.com", "va@example.com"]


# --- the trap ------------------------------------------------------------------

def test_there_is_one_helper_that_strips_and_the_raw_one_says_so():
    """cli used to have `_env` (no strip) beside a helper that strips. Two
    names one letter apart with opposite contracts is a mistake waiting to
    be made; the raw one is renamed and documented, and nothing named
    `_env` is left to reach for."""
    import sentinel.cli as cli
    from sentinel.alerts import sms
    assert not hasattr(cli, "_env")
    assert not hasattr(sms, "_env")
    assert "NOT stripped" in cli._env_raw.__doc__
