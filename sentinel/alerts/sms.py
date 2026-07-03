from __future__ import annotations

import requests

from sentinel.alerts.base import AlertChannel, AlertMessage


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
