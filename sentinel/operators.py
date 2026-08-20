"""Operator accounts: who we are to the product (G-50).

There were none. Not weak ones - none. `portal_members` held customers, the ops
dashboard had no login of its own by design with the network gate as the entire
access control, and `events` had no actor column - so the append-only log that
exists to reconstruct an incident could say *what* happened and *when*, and
never *who*.

Two things depend on this and neither could be built first: attribution needs an
identity to attribute to, and 2FA needs an account to protect.

**Separate from customers, deliberately.** Operators live in their own table,
sign in through their own form, and carry their own session secret. Customer
identity (`portal_members`) is a different system with different requirements,
and conflating them is how an operator ends up reachable through a customer
login. Only the password *primitives* are shared, from `sentinel.passwords`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone

from sentinel.db import Database
from sentinel.passwords import DUMMY_HASH, hash_password, verify_password

SESSION_COOKIE = "ops_session"
SESSION_TTL_SECONDS = 12 * 3600
_SECRET_KEY_ROW = "session_secret"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


# --- accounts -----------------------------------------------------------------

def create_operator(db: Database, username: str, password: str, display_name: str | None = None) -> int:
    username = normalize_username(username)
    if not username:
        raise ValueError("operator username is empty")
    if not password:
        raise ValueError("operator password is empty")
    now = _now()
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM operators WHERE username = ?", (username,))
        if cur.fetchone() is not None:
            raise ValueError(f"operator '{username}' already exists")
        cur.execute(
            "INSERT INTO operators (username, display_name, password_hash, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'active', ?, ?)",
            (username, display_name, hash_password(password), now, now),
        )
        return cur.lastrowid


def set_operator_password(db: Database, username: str, password: str) -> bool:
    if not password:
        raise ValueError("operator password is empty")
    with db.cursor() as cur:
        cur.execute(
            "UPDATE operators SET password_hash = ?, updated_at = ? WHERE username = ?",
            (hash_password(password), _now(), normalize_username(username)),
        )
        return cur.rowcount == 1


def _set_status(db: Database, username: str, status: str) -> bool:
    with db.cursor() as cur:
        cur.execute(
            "UPDATE operators SET status = ?, updated_at = ? WHERE username = ?",
            (status, _now(), normalize_username(username)),
        )
        return cur.rowcount == 1


def disable_operator(db: Database, username: str) -> bool:
    """Offboarding, as a routine act rather than a migration.

    Disabled rather than deleted: deleting would orphan every action the person
    ever took, and the whole point of having names is that the log can still
    point at one."""
    return _set_status(db, username, "disabled")


def enable_operator(db: Database, username: str) -> bool:
    return _set_status(db, username, "active")


def list_operators(db: Database) -> list:
    with db.cursor() as cur:
        cur.execute(
            "SELECT username, display_name, status, created_at, last_login_at "
            "FROM operators ORDER BY username"
        )
        return cur.fetchall()


def operator_count(db: Database) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) c FROM operators")
        return cur.fetchone()["c"]


def is_active_operator(db: Database, username: str) -> bool:
    with db.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM operators WHERE username = ? AND status = 'active'",
            (normalize_username(username),),
        )
        return cur.fetchone() is not None


def check_operator_login(db: Database, username: str, password: str) -> bool:
    username = normalize_username(username)
    with db.cursor() as cur:
        cur.execute(
            "SELECT password_hash FROM operators WHERE username = ? AND status = 'active'",
            (username,),
        )
        row = cur.fetchone()
    if row is None:
        # One scrypt op against a dummy either way, so a missing or disabled
        # account costs the same time as a real one and response timing never
        # reveals which usernames exist.
        verify_password(password, DUMMY_HASH)
        return False
    if not verify_password(password, row["password_hash"]):
        return False
    with db.cursor() as cur:
        cur.execute("UPDATE operators SET last_login_at = ? WHERE username = ?", (_now(), username))
    return True


def operator_login_requires_code(db: Database, username: str) -> bool:
    """Always True (G-28, #32). Operator two-factor is not optional.

    Both halves of that criterion live here. An *enrolled* operator cannot turn
    it off - `twofactor.disable` refuses for operators. An *unenrolled* one
    cannot sign in either, because this returns True regardless of enrolment,
    so the login route must send them to enrol before it will issue a session.

    Without the second half, "operators must have 2FA" would be satisfied by
    nobody ever enrolling - which is exactly how a mandatory control becomes
    an optional one."""
    return True


# --- sessions -----------------------------------------------------------------

def session_secret(db: Database) -> bytes:
    """Kept in `operator_meta`, not `portal_meta`: a leak of one secret must not
    mint sessions for the other identity system."""
    with db.cursor() as cur:
        cur.execute("SELECT value FROM operator_meta WHERE key = ?", (_SECRET_KEY_ROW,))
        row = cur.fetchone()
        if row is not None:
            return bytes.fromhex(row["value"])
        secret = secrets.token_bytes(32)
        cur.execute("INSERT INTO operator_meta (key, value) VALUES (?, ?)", (_SECRET_KEY_ROW, secret.hex()))
        return secret


def make_session_token(secret: bytes, username: str, now: float | None = None) -> str:
    name_b64 = base64.urlsafe_b64encode(normalize_username(username).encode()).decode().rstrip("=")
    expires = int((now if now is not None else time.time()) + SESSION_TTL_SECONDS)
    payload = f"{name_b64}.{expires}"
    sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(secret: bytes, token: str, now: float | None = None) -> str | None:
    """The operator username for a valid, unexpired token; None otherwise."""
    try:
        name_b64, expires_s, sig = token.split(".")
        payload = f"{name_b64}.{expires_s}"
        expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(expires_s) < (now if now is not None else time.time()):
            return None
        pad = "=" * (-len(name_b64) % 4)
        return base64.urlsafe_b64decode(name_b64 + pad).decode()
    except (ValueError, TypeError):
        return None


def session_operator(db: Database, token: str | None) -> str | None:
    """The signed-in operator for a cookie, or None.

    Re-checks that the account is still active on every request rather than
    trusting the token alone. A session minted before someone was offboarded
    would otherwise stay valid for its full 12 hours, which would make
    `disable_operator` a promise rather than a control."""
    if not token:
        return None
    username = verify_session_token(session_secret(db), token)
    if username is None or not is_active_operator(db, username):
        return None
    return username


# --- attribution ---------------------------------------------------------------

def acknowledge_alert(db: Database, alert_id: int, actor: str | None) -> bool:
    """Marks an alert acknowledged, by name. False if it was already
    acknowledged - the first ack keeps its name and its time, because an
    acknowledgement is a claim someone made at a moment and overwriting it
    would rewrite the record of who responded."""
    with db.cursor() as cur:
        cur.execute(
            "UPDATE alerts SET acknowledged_at = ?, acknowledged_by = ? "
            "WHERE id = ? AND acknowledged_at IS NULL",
            (_now(), actor, alert_id),
        )
        acknowledged = cur.rowcount == 1
    if acknowledged:
        # Decision 1 (#27): the emergency contact hears that a CRITICAL was
        # closed - acknowledgement must not silently silence them. Fires only
        # on the first ack (the IS NULL guard above), so exactly once.
        from sentinel.alerts.escalation import send_critical_ack_closing_note

        send_critical_ack_closing_note(db, alert_id)
    return acknowledged
