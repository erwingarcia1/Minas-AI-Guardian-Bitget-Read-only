from __future__ import annotations

import requests

from sentinel.alerts.base import AlertChannel, AlertMessage

# Verified against ClickSend's official docs + Python/PHP SDKs (2026-07-05):
# POST https://rest.clicksend.com/v3/sms/send, HTTP Basic auth (username + API
# key as password), body {"messages":[{"body","to","from?","source?"}]}, and
# success = response_code=="SUCCESS" AND each data.messages[].status=="SUCCESS".
_CLICKSEND_SMS_URL = "https://rest.clicksend.com/v3/sms/send"


class ClickSendSmsChannel(AlertChannel):
    """SMS via ClickSend's REST API - a Twilio alternative. SMS is a pager, not
    a report: the body is hard-truncated (full evidence goes out on Telegram/
    email in parallel)."""

    MAX_LEN = 320  # two SMS segments

    def __init__(self, username: str, api_key: str, from_id: str | None, to_numbers: list[str], timeout: float = 15.0):
        self._username = username
        self._api_key = api_key
        self._from = from_id
        self._to = to_numbers
        self._timeout = timeout

    def send(self, alert: AlertMessage) -> None:
        body = f"[Minas AI Guardian {alert.severity}] {alert.rule} - {alert.account_label}: {alert.message}"
        if len(body) > self.MAX_LEN:
            body = body[: self.MAX_LEN - 3] + "..."
        messages = []
        for number in self._to:
            msg = {"source": "minas", "body": body, "to": number}
            if self._from:
                msg["from"] = self._from
            messages.append(msg)

        resp = requests.post(
            _CLICKSEND_SMS_URL,
            auth=(self._username, self._api_key),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json={"messages": messages},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        # ClickSend docs: http_code/response_code alone don't reflect per-message
        # outcome. Require top-level SUCCESS AND every message queued.
        if data.get("response_code") != "SUCCESS":
            raise RuntimeError(f"ClickSend rejected the request: {data.get('response_code')} {data.get('response_msg')}")
        failed = [m for m in data.get("data", {}).get("messages", []) if m.get("status") != "SUCCESS"]
        if failed:
            raise RuntimeError(f"ClickSend failed to queue {len(failed)} message(s): {failed[0].get('status')}")
