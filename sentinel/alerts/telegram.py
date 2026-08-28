from __future__ import annotations

from sentinel import net
from sentinel.alerts.base import AlertChannel, AlertMessage


class TelegramChannel(AlertChannel):
    def __init__(self, bot_token: str, chat_id: str, timeout: float = 10.0):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._timeout = timeout

    @property
    def recipients(self) -> list[str]:
        return [str(self._chat_id)]

    def send(self, alert: AlertMessage) -> None:
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        resp = net.post(
            url,
            json={"chat_id": self._chat_id, "text": alert.render()},
            timeout=self._timeout,
        )
        resp.raise_for_status()
