"""Self-serve Telegram connection for portal members (#18).

The Bot API cannot DM an arbitrary @handle - the recipient must start the
bot first - so the flow is built around that: the portal hands the member a
one-time code, the member opens the bot and sends `/start <code>`, and the
worker watching the bot's updates binds that chat to the member and sends
the test message the criterion asks for. The code is the secret; the
Telegram handle columns on accounts stay what they always were, reference
only.

Security shape, following the house patterns:
  - the code is 8 bytes of `secrets`, single-use, 15-minute expiry; an
    unknown or expired code gets a polite refusal in the chat and nothing
    else - no probing which codes exist;
  - connecting and disconnecting are member security events (they change
    where alert copies go), recorded on every account that login reaches;
  - disconnecting also sends the tripwire email to the member's address,
    because silencing a channel is what an attacker does before acting;
  - customer chats receive CUSTOMER copy (`render_customer`), never the
    operator format with rule names and evidence.

The worker is a daemon thread long-polling getUpdates. Its offset persists
in portal_meta so a restart neither replays old links nor drops queued
ones. Every failure is logged and the loop continues - a Telegram outage
must never take the poller down with it.
"""
from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone

from sentinel import net
from sentinel.db import Database

logger = logging.getLogger("sentinel.alerts.telegram_link")

LINK_CODE_TTL_MINUTES = 15
_OFFSET_KEY = "telegram_updates_offset"
_BOT_USERNAME_KEY = "telegram_bot_username"

TEST_MESSAGE = (
    "You're connected to Minas AI Guardian. This is your test message - "
    "alerts about your accounts will arrive in this chat from now on. "
    "You can disconnect at any time from the portal's Security page."
)

_REFUSAL = (
    "That link code isn't valid - it may have expired (codes last "
    f"{LINK_CODE_TTL_MINUTES} minutes) or already been used. Generate a "
    "fresh one from your Minas AI Guardian portal, under Security."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_link_code(db: Database, email: str) -> str:
    """A fresh one-time code for this member, invalidating any pending one -
    two live codes for one person is two phishable secrets."""
    from sentinel.portal import normalize_email

    email = normalize_email(email)
    code = secrets.token_urlsafe(8)
    now = _now()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE telegram_links SET status = 'revoked' "
            "WHERE member_email = ? AND status = 'pending'", (email,))
        cur.execute(
            "INSERT INTO telegram_links (member_email, link_code, code_expires_at, "
            "status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (email, code,
             (now + timedelta(minutes=LINK_CODE_TTL_MINUTES)).isoformat(),
             now.isoformat()),
        )
    return code


def connected_link(db: Database, email: str):
    from sentinel.portal import normalize_email

    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM telegram_links WHERE member_email = ? AND status = 'connected' "
            "ORDER BY id DESC LIMIT 1", (normalize_email(email),))
        return cur.fetchone()


def pending_link(db: Database, email: str):
    from sentinel.portal import normalize_email

    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM telegram_links WHERE member_email = ? AND status = 'pending' "
            "AND code_expires_at > ? ORDER BY id DESC LIMIT 1",
            (normalize_email(email), _now().isoformat()))
        return cur.fetchone()


def disconnect(db: Database, email: str) -> bool:
    """Revoke the member's connected chat. Returns whether anything was
    connected. The caller records the security event and sends the tripwire
    - this only flips the row."""
    from sentinel.portal import normalize_email

    with db.cursor() as cur:
        cur.execute(
            "UPDATE telegram_links SET status = 'revoked' "
            "WHERE member_email = ? AND status = 'connected'",
            (normalize_email(email),))
        return cur.rowcount > 0


def chat_ids_for_emails(db: Database, emails: list[str]) -> list[str]:
    """Connected chat ids for these contact addresses, for the delivery leg.
    Input addresses are canonicalized the same way the links were stored."""
    from sentinel.portal import normalize_email

    normalized = [normalize_email(e) for e in emails if e]
    normalized = [e for e in normalized if e]
    if not normalized:
        return []
    marks = ",".join("?" for _ in normalized)
    with db.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT chat_id FROM telegram_links "
            f"WHERE member_email IN ({marks}) AND status = 'connected' "
            f"AND chat_id IS NOT NULL", normalized)
        return [r["chat_id"] for r in cur.fetchall()]


def confirm_code(db: Database, code: str, chat_id: str) -> str | None:
    """The worker's half of the handshake: bind `chat_id` to the pending link
    carrying `code`. Returns the member email on success, None otherwise.
    Single-use and expiring are enforced here, in one UPDATE, so two chats
    racing the same code cannot both win it."""
    if not code:
        return None
    now = _now().isoformat()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE telegram_links SET chat_id = ?, status = 'connected', "
            "link_code = NULL, connected_at = ? "
            "WHERE link_code = ? AND status = 'pending' AND code_expires_at > ?",
            (str(chat_id), now, code, now))
        if cur.rowcount == 0:
            return None
        cur.execute(
            "SELECT member_email FROM telegram_links WHERE chat_id = ? AND connected_at = ?",
            (str(chat_id), now))
        row = cur.fetchone()
    return row["member_email"] if row else None


def _meta_get(db: Database, key: str) -> str | None:
    with db.cursor() as cur:
        cur.execute("SELECT value FROM portal_meta WHERE key = ?", (key,))
        row = cur.fetchone()
    return row["value"] if row else None


def _meta_set(db: Database, key: str, value: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO portal_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def bot_username(db: Database) -> str | None:
    """The bot's @username, cached by the worker's getMe call - the portal
    renders the t.me deep link from this and says 'the Minas bot' until it
    is known."""
    return _meta_get(db, _BOT_USERNAME_KEY)


def process_update(db: Database, bot_token: str, update: dict) -> None:
    """One getUpdates entry. Factored out of the thread so tests can drive
    the handshake without a network or a thread."""
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if chat_id is None or not text.startswith("/start"):
        return
    code = text.split(maxsplit=1)[1].strip() if " " in text else ""
    email = confirm_code(db, code, str(chat_id))
    if email is None:
        _send_text(bot_token, chat_id, _REFUSAL)
        return
    # The test message IS the confirmation the criterion asks for: the
    # member sees delivery work before relying on it.
    _send_text(bot_token, chat_id, TEST_MESSAGE)
    try:
        from sentinel.events import record_member_security_event

        record_member_security_event(db, email, "member_telegram_connected",
                                     {"chat": str(chat_id)})
    except Exception as exc:  # noqa: BLE001 - the link stands; the record failing is its own log line
        logger.error("could not record the telegram connection for %s: %s", email, exc)


def _send_text(bot_token: str, chat_id, text: str) -> None:
    try:
        resp = net.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10.0,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - a chat we cannot reach must not stop the loop
        logger.error("telegram send to chat %s failed: %s", chat_id, exc)


class TelegramLinkWorker(threading.Thread):
    """Daemon long-poll over getUpdates. One instance per process."""

    def __init__(self, db: Database, bot_token: str, poll_timeout: int = 25):
        super().__init__(daemon=True, name="telegram-link-worker")
        self._db = db
        self._token = bot_token
        self._poll_timeout = poll_timeout
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self._learn_bot_username()
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:  # noqa: BLE001 - an outage must not kill the poller
                logger.error("telegram update poll failed: %s", exc)
                self._stop.wait(30)

    def _learn_bot_username(self) -> None:
        # POST like every Bot API method: net exposes only post(), on purpose
        # - one socket chokepoint, and this module has no reason to widen it.
        try:
            resp = net.post(f"https://api.telegram.org/bot{self._token}/getMe", timeout=10.0)
            resp.raise_for_status()
            username = (resp.json().get("result") or {}).get("username")
            if username:
                _meta_set(self._db, _BOT_USERNAME_KEY, str(username))
        except Exception as exc:  # noqa: BLE001 - the portal degrades to 'the Minas bot'
            logger.warning("could not learn the bot username: %s", exc)

    def _poll_once(self) -> None:
        offset = _meta_get(self._db, _OFFSET_KEY)
        params = {"timeout": self._poll_timeout}
        if offset is not None:
            params["offset"] = int(offset)
        resp = net.post(
            f"https://api.telegram.org/bot{self._token}/getUpdates",
            json=params, timeout=self._poll_timeout + 10.0,
        )
        resp.raise_for_status()
        for update in resp.json().get("result") or []:
            process_update(self._db, self._token, update)
            # Persisted per update, not per batch: a crash mid-batch must
            # not replay a /start that already bound a chat.
            _meta_set(self._db, _OFFSET_KEY, str(int(update["update_id"]) + 1))
