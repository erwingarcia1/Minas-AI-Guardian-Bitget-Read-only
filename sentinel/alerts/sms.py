from __future__ import annotations

import os

import requests

from sentinel.alerts.base import AlertChannel, AlertMessage


def build_sms_channel(recipients: list[str]) -> AlertChannel | None:
    """Return an SMS channel for `recipients` based on configured env, or None
    if no SMS provider is set. ClickSend is preferred when its credentials are
    present; Twilio is the fallback. Callers no-op cleanly when this is None."""
    recipients = [r for r in recipients if r]
    if not recipients:
        return None
    cs_user, cs_key = os.environ.get("CLICKSEND_USERNAME"), os.environ.get("CLICKSEND_API_KEY")
    if cs_user and cs_key:
        from sentinel.alerts.clicksend import ClickSendSmsChannel

        return ClickSendSmsChannel(cs_user, cs_key, os.environ.get("CLICKSEND_FROM"), recipients)
    sid, token, frm = (
        os.environ.get("TWILIO_ACCOUNT_SID"),
        os.environ.get("TWILIO_AUTH_TOKEN"),
        os.environ.get("TWILIO_FROM_NUMBER"),
    )
    if sid and token and frm:
        return TwilioSmsChannel(sid, token, frm, recipients)
    return None


class TwilioSmsChannel(AlertChannel):
    """SMS via Twilio's REST API. SMS is CRITICAL-only in the default
    routing; the body is truncated hard because SMS is a pager, not a
    report - the full evidence goes out on Telegram/email in parallel."""

    MAX_LEN = 320  # two SMS segments

    def __init__(self, account_sid: str, auth_token: str, from_number: str, to_numbers: list[str], timeout: float = 15.0):
        self._sid = account_sid
        self._token = auth_token
        self._from = from_number
        self._to = to_numbers
        self._timeout = timeout

    def send(self, alert: AlertMessage) -> None:
        body = f"[Minas AI Guardian {alert.severity}] {alert.rule} - {alert.account_label}: {alert.message}"
        if len(body) > self.MAX_LEN:
            body = body[: self.MAX_LEN - 3] + "..."
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
        for number in self._to:
            resp = requests.post(
                url,
                auth=(self._sid, self._token),
                data={"From": self._from, "To": number, "Body": body},
                timeout=self._timeout,
            )
            resp.raise_for_status()
