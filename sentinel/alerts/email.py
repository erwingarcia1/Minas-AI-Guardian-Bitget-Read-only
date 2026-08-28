from __future__ import annotations

from email.message import EmailMessage

from sentinel import net
from sentinel.alerts.base import AlertChannel, AlertMessage


class EmailChannel(AlertChannel):
    """SMTP delivery for one audience.

    `audience` picks the template, and the two are not interchangeable: the
    operator format carries rule names, confidence and raw evidence, which is
    exactly what must not reach a customer's mailbox. Default is operator so
    that any caller which forgets to say gets the internal format sent to an
    internal address, rather than the reverse."""

    def __init__(self, host: str, port: int, username: str, password: str, sender: str, recipients: list[str], use_tls: bool = True, *, audience: str = "operator"):
        if audience not in ("operator", "customer"):
            raise ValueError(f"unknown audience {audience!r}")
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._recipients = recipients
        self._use_tls = use_tls
        self._audience = audience

    @property
    def recipients(self) -> list[str]:
        return list(self._recipients)

    def send(self, alert: AlertMessage) -> None:
        customer = self._audience == "customer"
        msg = EmailMessage()
        msg["Subject"] = alert.customer_subject() if customer else alert.operator_subject()
        msg["From"] = self._sender
        msg["To"] = ", ".join(self._recipients)
        msg.set_content(alert.render_customer() if customer else alert.render())

        with net.smtp(self._host, self._port, timeout=15) as smtp:
            if self._use_tls:
                smtp.starttls()
            if self._username:
                smtp.login(self._username, self._password)
            smtp.send_message(msg)
