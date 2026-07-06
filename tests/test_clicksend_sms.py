from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sentinel.alerts.base import AlertMessage
from sentinel.alerts.clicksend import ClickSendSmsChannel
from sentinel.alerts.sms import TwilioSmsChannel, build_sms_channel


def _alert():
    return AlertMessage(account_label="acct", rule="withdrawal_initiated", severity="CRITICAL",
                        confidence="high", message="0.5 BTC withdrawal", evidence={})


def test_clicksend_posts_expected_payload():
    with patch("sentinel.alerts.clicksend.requests.post") as post:
        post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"response_code": "SUCCESS", "data": {"messages": [{"status": "SUCCESS"}]}},
        )
        ClickSendSmsChannel("user", "apikey", "MinasGuard", ["+16477216549"]).send(_alert())
        _, kwargs = post.call_args
        assert kwargs["auth"] == ("user", "apikey")
        assert kwargs["json"]["messages"][0]["to"] == "+16477216549"
        assert kwargs["json"]["messages"][0]["from"] == "MinasGuard"
        assert "Minas AI Guardian" in kwargs["json"]["messages"][0]["body"]
        assert kwargs["headers"]["Content-Type"] == "application/json"


def test_clicksend_raises_on_top_level_failure():
    with patch("sentinel.alerts.clicksend.requests.post") as post:
        post.return_value = MagicMock(
            status_code=200, json=lambda: {"response_code": "ERROR", "response_msg": "bad auth"}
        )
        with pytest.raises(RuntimeError):
            ClickSendSmsChannel("u", "k", None, ["+1555"]).send(_alert())


def test_clicksend_raises_on_per_message_failure():
    with patch("sentinel.alerts.clicksend.requests.post") as post:
        post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"response_code": "SUCCESS", "data": {"messages": [{"status": "INVALID_RECIPIENT"}]}},
        )
        with pytest.raises(RuntimeError):
            ClickSendSmsChannel("u", "k", None, ["+1555"]).send(_alert())


def test_factory_prefers_clicksend(monkeypatch):
    monkeypatch.setenv("CLICKSEND_USERNAME", "u")
    monkeypatch.setenv("CLICKSEND_API_KEY", "k")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "sid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+1")
    assert isinstance(build_sms_channel(["+1555"]), ClickSendSmsChannel)


def test_factory_falls_back_to_twilio(monkeypatch):
    monkeypatch.delenv("CLICKSEND_USERNAME", raising=False)
    monkeypatch.delenv("CLICKSEND_API_KEY", raising=False)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "sid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+1")
    assert isinstance(build_sms_channel(["+1555"]), TwilioSmsChannel)


def test_factory_none_when_unconfigured(monkeypatch):
    for k in ("CLICKSEND_USERNAME", "CLICKSEND_API_KEY", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    assert build_sms_channel(["+1555"]) is None
    assert build_sms_channel([]) is None
