from __future__ import annotations

import smtplib
from email.message import EmailMessage

from sentinel.alerts.base import AlertChannel, AlertMessage


class EmailChannel(AlertChannel):
    def __init__(self, host: str, port: int, username: str, password: str, sender: str, recipients: list[str], use_tls: bool = True):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._recipients = recipients
        self._use_tls = use_tls

    def send(self, alert: AlertMessage) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[Minas AI Guardian {alert.severity}] {alert.rule} - {alert.account_label}"
        msg["From"] = self._sender
        msg["To"] = ", ".join(self._recipients)
        msg.set_content(alert.render())

        with smtplib.SMTP(self._host, self._port, timeout=15) as smtp:
            if self._use_tls:
                smtp.starttls()
            if self._username:
                smtp.login(self._username, self._password)
            smtp.send_message(msg)
