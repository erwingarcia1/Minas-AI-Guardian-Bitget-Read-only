from __future__ import annotations

import logging
import os

from sentinel.alerts.base import AlertMessage
from sentinel.alerts.email import EmailChannel
from sentinel.alerts.sms import build_sms_channel

logger = logging.getLogger("sentinel.alerts.contact")


class ContactRouter:
    """Delivers an account's alerts to the per-account email/phone captured at
    onboarding, in addition to the global severity routing. Transport comes
    from the same SMTP_* / ClickSend / Twilio env used elsewhere; when a
    transport isn't configured, a contact is skipped with a one-time warning
    rather than an error - so per-account delivery lights up the moment
    credentials are added, with no code change."""

    def __init__(self) -> None:
        self._smtp = self._smtp_config()
        self._warned: set[str] = set()

    @staticmethod
    def _smtp_config() -> dict | None:
        host = os.environ.get("SMTP_HOST")
        sender = os.environ.get("SMTP_SENDER")
        if host and sender:
            return {
                "host": host,
                "port": int(os.environ.get("SMTP_PORT", "587")),
                "username": os.environ.get("SMTP_USERNAME", ""),
                "password": os.environ.get("SMTP_PASSWORD", ""),
                "sender": sender,
            }
        return None

    def _warn_once(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            logger.warning(msg)

    def send(self, detection, account_label: str, emails, phones) -> None:
        """Deliver to every configured per-account email + phone (primary and
        secondary). `emails`/`phones` are iterables; None/blank entries and
        duplicates are dropped. Telegram handles are intentionally NOT handled
        here - the Bot API can't DM an arbitrary @handle without the recipient
        first starting the bot (see docs), so those are stored for reference
        only until a customer-opt-in flow exists."""
        emails = list(dict.fromkeys(e for e in (emails or []) if e))
        phones = list(dict.fromkeys(p for p in (phones or []) if p))
        if not emails and not phones:
            return
        alert = AlertMessage(
            account_label=account_label,
            rule=detection.rule,
            severity=detection.severity,
            confidence=detection.confidence,
            message=detection.message,
            evidence=detection.evidence,
        )

        if emails:
            if self._smtp is None:
                self._warn_once(
                    "smtp",
                    "per-account email set but SMTP is not configured - set SMTP_* env to deliver "
                    "these alerts (Telegram still fires)",
                )
            else:
                try:
                    EmailChannel(
                        self._smtp["host"], self._smtp["port"], self._smtp["username"],
                        self._smtp["password"], self._smtp["sender"], emails,
                    ).send(alert)
                except Exception as exc:  # noqa: BLE001 - delivery failure must not stall detection
                    logger.warning("per-account email to %s failed: %s", emails, exc)

        if phones:
            channel = build_sms_channel(phones)
            if channel is None:
                self._warn_once(
                    "sms",
                    "per-account SMS set but no SMS provider is configured - set CLICKSEND_* or "
                    "TWILIO_* env to deliver these alerts (Telegram still fires)",
                )
            else:
                try:
                    channel.send(alert)
                except Exception as exc:  # noqa: BLE001 - delivery failure must not stall detection
                    logger.warning("per-account SMS to %s failed: %s", phones, exc)
