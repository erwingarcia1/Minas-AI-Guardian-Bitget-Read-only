from __future__ import annotations

from sentinel import net
from sentinel.alerts.base import AlertChannel, AlertMessage
from sentinel.config import env_setting

# Two SMS segments. Shared by every SMS transport: SMS is a pager, not a
# report - the full evidence goes out on Telegram/email in parallel.
SMS_MAX_LEN = 320


def format_sms_body(alert: AlertMessage, max_len: int = SMS_MAX_LEN,
                    audience: str = "customer") -> str:
    """Lock-screen-strict SMS bodies, by audience.

    An SMS shows on a lock screen and is the most spoofable channel, so it
    gets the most conservative copy of any surface - stricter than email:

      customer:  severity, account label, "sign in to the Minas portal".
                 Nothing else. No amounts, no addresses, no balances, no
                 internal rule names, and NO link - a real Minas SMS never
                 carries one, which is itself the anti-phishing tell.
      operator:  severity, rule name, account label. Rule names are ours
                 and mean something to the person paged - but the raw
                 detector message (which embeds amounts and addresses)
                 stays off an operator's lock screen too, and the full
                 evidence rides Telegram/email in parallel. No link either.

    The old body shipped `alert.message` raw plus a portal URL - the exact
    compare-side-only failure email fixed with render_customer(), and
    tests/test_customer_channel_guard.py is what now stops the next
    channel from repeating it."""
    if audience == "customer":
        body = (f"Minas AI Guardian {alert.severity} alert on {alert.account_label}. "
                "Sign in to the Minas portal to review. We never send links by SMS.")
    else:
        body = (f"Minas ops {alert.severity}: {alert.rule} on {alert.account_label}. "
                "Full evidence on Telegram/email.")
    return body[:max_len]


def sms_provider_name() -> str | None:
    """The SMS provider this process would send through - "ClickSend",
    "Twilio", or None - by the rule `build_sms_channel` applies: ClickSend
    when its credentials are present, Twilio otherwise. The privacy policy
    names it (decision 27), so the page and the sender cannot disagree;
    tests/test_clicksend_sms.py holds the two to the same answer."""
    if env_setting("CLICKSEND_USERNAME") and env_setting("CLICKSEND_API_KEY"):
        return "ClickSend"
    if env_setting("TWILIO_ACCOUNT_SID") and env_setting("TWILIO_AUTH_TOKEN") and env_setting("TWILIO_FROM_NUMBER"):
        return "Twilio"
    return None


def build_sms_channel(recipients: list[str], audience: str = "customer") -> AlertChannel | None:
    """Return an SMS channel for `recipients` based on configured env, or None
    if no SMS provider is set. ClickSend is preferred when its credentials are
    present; Twilio is the fallback. Callers no-op cleanly when this is None.
    "Present" means non-blank - see `sentinel.config.env_setting`."""
    recipients = [r for r in recipients if r]
    if not recipients:
        return None
    cs_user, cs_key = env_setting("CLICKSEND_USERNAME"), env_setting("CLICKSEND_API_KEY")
    if cs_user and cs_key:
        from sentinel.alerts.clicksend import ClickSendSmsChannel

        return ClickSendSmsChannel(cs_user, cs_key, env_setting("CLICKSEND_FROM"), recipients,
                                   audience=audience)
    sid, token, frm = env_setting("TWILIO_ACCOUNT_SID"), env_setting("TWILIO_AUTH_TOKEN"), env_setting("TWILIO_FROM_NUMBER")
    if sid and token and frm:
        return TwilioSmsChannel(sid, token, frm, recipients, audience=audience)
    return None


class TwilioSmsChannel(AlertChannel):
    """SMS via Twilio's REST API. SMS is CRITICAL-only in the default
    routing; the body is truncated hard because SMS is a pager, not a
    report - the full evidence goes out on Telegram/email in parallel."""

    MAX_LEN = SMS_MAX_LEN

    def __init__(self, account_sid: str, auth_token: str, from_number: str, to_numbers: list[str], timeout: float = 15.0, *, audience: str = "customer"):
        if audience not in ("operator", "customer"):
            raise ValueError(f"unknown audience {audience!r}")
        self._audience = audience
        self._sid = account_sid
        self._token = auth_token
        self._from = from_number
        self._to = to_numbers
        self._timeout = timeout

    @property
    def recipients(self) -> list[str]:
        return list(self._to)

    def send(self, alert: AlertMessage) -> list[dict]:
        """Sends to every recipient; returns one delivery receipt per number:
        {to, provider, provider_message_id, status}."""
        body = format_sms_body(alert, self.MAX_LEN, audience=self._audience)
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
        receipts = []
        for number in self._to:
            resp = net.post(
                url,
                auth=(self._sid, self._token),
                data={"From": self._from, "To": number, "Body": body},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            try:
                sid = resp.json().get("sid")
            except ValueError:
                sid = None
            receipts.append({"to": number, "provider": "twilio", "provider_message_id": sid, "status": "sent"})
        return receipts
