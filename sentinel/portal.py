from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import html
import io
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

# Logic helpers only, deliberately: the portal owns its stylesheet, its pill
# and its brand usage (#82 — tests/test_surface_styles.py refuses a shared
# stylesheet, so the two surfaces cannot quietly converge again).
from sentinel.brand import MARK_SVG
from sentinel.dashboard import (
    _LOCAL_TIME_SCRIPT,
    _account_type_label,
    _ts,
    _watch_threshold,
)
from sentinel.db import Database
from sentinel.expected import mark_expected, marked_alert_ids, marks_for_alert
from sentinel.passwords import DUMMY_HASH as _DUMMY_HASH
from sentinel.passwords import LoginThrottle
from sentinel.rules_schema import default_for
from sentinel.snooze import active_snoozes_for_accounts, snooze_alert, snooze_choices
from sentinel.twofactor import MEMBER as TWOFACTOR_MEMBER
from sentinel.twofactor import check_second_factor

logger = logging.getLogger("sentinel.portal")

# The member portal runs as a SEPARATE HTTP server from the ops dashboard on
# purpose: the ops view is network-gated with no login of its own, so members
# must never share its surface. Everything here is read-only and every query
# is filtered by the authenticated member's email - there is no route that
# can return another customer's data.

SESSION_COOKIE = "minas_portal"
# Two clocks on every session (#31): an idle window that activity slides
# forward, and an absolute cap no amount of activity extends. Stateless -
# both deadlines ride inside the signed token.
SESSION_IDLE_SECONDS = 2 * 3600
SESSION_TTL_SECONDS = 12 * 3600
_SECRET_KEY_ROW = "session_signing_secret"

# Login throttling: after this many failures per email or per client IP in
# the window, further attempts are rejected without checking the password.


# --- password hashing (scrypt, stdlib) --------------------------------------

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False  # unparseable hash fails closed


# A single precomputed scrypt hash used to equalize login timing for unknown
# emails: verifying against it costs exactly ONE scrypt op, the same as the
# real-email path, so response time never reveals whether an email is
# registered. Shared with the operator login from sentinel.passwords - the
# primitives are common, the identities deliberately are not.


# --- member records ----------------------------------------------------------

def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def canonical_contact_email(email: str | None) -> str | None:
    """What a contact-email COLUMN stores: normalized, and None when absent.

    #90's two findings in one function. Reads behave today only because
    every comparison site remembered `lower()` - normalizing at the write
    boundary is what stops the next forgotten one - and '' as absent is the
    falsy-sentinel trap (`lower('') = ''` matches every blank), so the
    canonical absent is NULL. Every writer of alert_email /
    alert_email_secondary goes through this; a registration test enforces
    that."""
    return normalize_email(email) or None


def canonical_contact_phone(phone: str | None) -> str | None:
    """Phones share only the absence half: stripped, and None when blank -
    no case to fold."""
    return (phone or "").strip() or None


RESET_TOKEN_MAX_TTL_SECONDS = 3600

# A reset flow that accepts "a" turns a stolen link into a permanently weak
# account. Length only: this is a minimum, not a composition policy, because
# composition rules push people towards predictable substitutions and this
# product has no business teaching bad habits to somebody who is already
# locked out.
MIN_PASSWORD_LENGTH = 12

# Every /forgot response takes at least this long, whether or not the address
# exists. See `_forgot`.
_RESET_RESPONSE_FLOOR_SECONDS = 0.35

# How long a half-finished sign-in may sit in a browser. It carries a proof
# that a correct password was presented, so it is measured in minutes rather
# than in the session lifetime - a tab left open on a shared machine should not
# still be one code away from an account at the end of the day.
PENDING_TTL_SECONDS = 300


def _pending_now() -> float:
    """Indirected so a test can move time without touching the clock the rest
    of the process reads."""
    return time.time()


def make_pending_token(secret: bytes, email: str) -> str:
    """Proof that this browser presented a correct password moments ago, and
    nothing else (#32).

    Deliberately NOT a session token and deliberately not interchangeable with
    one: it is signed over a different context string, so a pending token
    presented as a cookie verifies as nothing. Otherwise the two-step flow
    would just rename the session and hand it over a step early."""
    email_b64 = base64.urlsafe_b64encode(normalize_email(email).encode()).decode().rstrip("=")
    expires = int(_pending_now() + PENDING_TTL_SECONDS)
    payload = f"pending.{email_b64}.{expires}"
    sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_pending_token(secret: bytes, token: str) -> str | None:
    try:
        context, email_b64, expires_s, sig = token.split(".")
        if context != "pending":
            return None
        payload = f"pending.{email_b64}.{expires_s}"
        if not hmac.compare_digest(sig, hmac.new(secret, payload.encode(),
                                                 hashlib.sha256).hexdigest()):
            return None
        if int(expires_s) < _pending_now():
            return None
        pad = "=" * (-len(email_b64) % 4)
        return base64.urlsafe_b64decode(email_b64 + pad).decode()
    except (ValueError, TypeError):
        return None


def set_member_password(db: Database, email: str, password: str, *,
                        invalidate_sessions: bool = True, notify=None,
                        grant_account_id: int | None = None, actor: str | None = None) -> None:
    """Create or reset a member login.

    `invalidate_sessions` bumps `session_epoch`, which retires every cookie
    ever issued to this member (#33). It defaults to True and callers should
    leave it there: the ops dashboard's "Reset portal password" is the path
    used when a customer phones in saying they have been compromised, and it
    would be a poor joke if that were the one route that left the attacker's
    session alive.

    `notify` is called with the address after a successful change. It is the
    tripwire, not a receipt - a change the owner did not make is the only
    signal they will get that somebody else has their mailbox. It is called
    LAST and guarded: a customer locked out with a dead SMTP server must not
    stay locked out because the notification failed."""
    email = normalize_email(email)
    if not email:
        raise ValueError("member email is empty")
    now = datetime.now(timezone.utc).isoformat()
    bump = "session_epoch = session_epoch + 1, " if invalidate_sessions else ""
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM portal_members WHERE email = ?", (email,))
        existed = cur.fetchone() is not None
        cur.execute(
            "INSERT INTO portal_members (email, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?) "
            f"ON CONFLICT(email) DO UPDATE SET password_hash = excluded.password_hash, {bump}"
            "updated_at = excluded.updated_at",
            (email, hash_password(password), now, now),
        )
    # #61: a credential issued from one account's page opens that account.
    # A NEW login is scoped to it and nothing else; an existing login keeps
    # what it had (materialised if it was legacy) and gains this one.
    if grant_account_id is not None:
        if not existed:
            with db.cursor() as cur:
                cur.execute("UPDATE portal_members SET scoped = 1 WHERE email = ?", (email,))
        grant_account(db, email, int(grant_account_id), actor)
    # Recorded here rather than at the call sites, so no route that sets a
    # password can forget to leave a trace (G-32, #36). Guarded for the same
    # reason `notify` is: a customer resetting in a panic must not be left
    # locked out because the audit write failed. A failure that swallows the
    # record is logged as an error, because a silently missing audit row is
    # the thing this project keeps finding.
    try:
        from sentinel.events import record_member_security_event

        record_member_security_event(db, email, "member_password_changed")
    except Exception as exc:  # noqa: BLE001 - the password change is the point
        logger.error("could not record the password change for %s: %s", email, exc)
    if notify is not None:
        try:
            notify(email)
        except Exception as exc:  # noqa: BLE001 - the change is the point
            logger.error("password-change notification to %s failed: %s", email, exc)


def member_exists(db: Database, email: str) -> bool:
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM portal_members WHERE email = ?", (normalize_email(email),))
        return cur.fetchone() is not None


def check_member_login(db: Database, email: str, password: str) -> bool:
    email = normalize_email(email)
    with db.cursor() as cur:
        cur.execute("SELECT password_hash FROM portal_members WHERE email = ?", (email,))
        row = cur.fetchone()
    if row is None:
        # Burn ONE scrypt op against a static dummy hash so a missing email is
        # not distinguishable from a wrong password by response timing. (Using
        # hash_password here instead would run scrypt twice and leak existence.)
        verify_password(password, _DUMMY_HASH)
        return False
    if not verify_password(password, row["password_hash"]):
        return False
    with db.cursor() as cur:
        cur.execute(
            "UPDATE portal_members SET last_login_at = ? WHERE email = ?",
            (datetime.now(timezone.utc).isoformat(), email),
        )
    return True


def member_login_requires_code(db: Database, email: str) -> bool:
    """True when this member has confirmed two-factor.

    A separate call from `check_member_login` on purpose. Folding the second
    factor into the password check would make it something a login route
    inherits without deciding; keeping it separate means any route that issues
    a session has to answer this question explicitly, and a route that forgets
    is a route that fails a test (#32)."""
    from sentinel.twofactor import MEMBER, is_enabled

    return is_enabled(db, MEMBER, normalize_email(email))


_ACCOUNT_COLUMNS = ("SELECT a.*, p.last_success_at, p.consecutive_failures, p.consecutive_auth_failures "
                    "FROM accounts a LEFT JOIN poll_state p ON p.account_id = a.id ")


def contact_accounts(db: Database, email: str) -> list:
    """Every account this address is a contact on (primary or secondary,
    case-insensitive). The pre-#61 scope rule, kept as the LEGACY rule for
    logins nobody has scoped yet, and the source a legacy login's grants are
    materialised from.

    An empty lookup returns nothing rather than falling through to the query.
    `alert_email_secondary` is '' on accounts saved through a form that left it
    blank, and `lower('') = ''` would otherwise hand a caller with no email
    every account in the fleet."""
    normalized = normalize_email(email)
    if not normalized:
        return []
    with db.cursor() as cur:
        cur.execute(
            _ACCOUNT_COLUMNS + "WHERE lower(a.alert_email) = ? OR lower(a.alert_email_secondary) = ? "
            "ORDER BY a.label",
            (normalized, normalized),
        )
        return cur.fetchall()


def is_scoped(db: Database, email: str) -> bool:
    with db.cursor() as cur:
        cur.execute("SELECT scoped FROM portal_members WHERE email = ?", (normalize_email(email),))
        row = cur.fetchone()
    return bool(row and row["scoped"])


def member_accounts(db: Database, email: str) -> list:
    """The accounts this login opens. THE isolation boundary: all portal
    queries go through the ids returned here.

    #61 (decision 10): a SCOPED login opens exactly its grants - a credential
    issued from one account's page opens that account, and revoking never
    widens. A login nobody has scoped yet keeps the legacy rule (every
    account the address is a contact on, G-21's per-row match for the
    secondary), so no existing member sees a change until an operator
    touches their scope - at which moment their contact rows are
    materialised into grants first."""
    normalized = normalize_email(email)
    if not normalized:
        return []
    if not is_scoped(db, normalized):
        return contact_accounts(db, normalized)
    with db.cursor() as cur:
        cur.execute(
            _ACCOUNT_COLUMNS + "JOIN member_account_grants g ON g.account_id = a.id "
            "WHERE g.email = ? ORDER BY a.label",
            (normalized,),
        )
        return cur.fetchall()


# --- #25: what a non-primary contact may change ------------------------------
#
# Decision 1 fixed the role: a secondary is an EMERGENCY contact, not a
# co-owner. Owner's ruling (2026-08-28): a non-primary may READ and may
# ACKNOWLEDGE. Nothing else. Every other change to an account is the primary
# contact's alone.
#
# The gate is default-deny and lives in one place (`do_POST`, between the
# route lookup and the handler). "Primary" is the account's `alert_email`;
# anyone else the login opens - the secondary contact, a #61 grant issued
# from an account page - is non-primary on that account. A login can be
# primary on one account and secondary on another, so account-scoped routes
# are judged against the account the request names, member-scoped routes
# against whether the login is primary anywhere at all.
#
# Every POST route must sit in exactly ONE of the three sets below. A route
# in none is a build failure (tests/test_member_roles.py), because the first
# version of this boundary was a test that POSTed to paths which had never
# existed and passed for six weeks while /key, /erase and /pause grew
# underneath it.

# Routes with NO session to judge: sign-in and its recovery. Not a role
# decision at all - there is nobody yet to be primary or secondary - so they
# are a bucket of their own, not a line in the allowlist.
PRE_AUTH = frozenset({"/login", "/login-code", "/forgot", "/reset"})

# What a signed-in NON-PRIMARY contact may reach: leave, harden their own
# login with a second factor, and acknowledge an alert - the one
# account-facing act an emergency contact exists to perform.
NON_PRIMARY_ALLOWED = frozenset({
    "/logout",
    "/security/2fa/start", "/security/2fa/confirm", "/security/2fa/disable",
    "/acknowledge",
})

# Everything that changes what is watched, how, or where alerts go.
# Account-scoped routes name the account they act on (see `_post_target`).
PRIMARY_ONLY = frozenset({
    "/key", "/erase", "/preferences", "/digest/frequency", "/drill",
    "/telegram/link", "/telegram/unlink",
    "/pause/request", "/pause/cancel", "/snooze", "/expected",
})

# Account-scoped primary-only routes: which form field names the account, and
# whether it is an account id or an alert id (resolved to its account inside
# the member's own scope - never outside it).
# Every POST route the portal serves. The dispatch table in do_POST must equal
# this set, and this set must be exactly the three disjoint buckets.
POST_ROUTES = PRE_AUTH | NON_PRIMARY_ALLOWED | PRIMARY_ONLY

ACCOUNT_SCOPED = {
    "/key": ("account_id", "account"),
    "/pause/request": ("account_id", "account"),
    "/pause/cancel": ("account_id", "account"),
    "/snooze": ("alert_id", "alert"),
    "/expected": ("alert_id", "alert"),
}


def member_role(db: Database, email: str, account_id: int) -> str | None:
    """'primary' if this login is the account's alert_email, 'non-primary'
    if the login opens the account any other way, None if it does not open
    it at all. Judged inside `member_accounts` so the answer never widens
    past the isolation boundary."""
    normalized = normalize_email(email)
    for row in member_accounts(db, normalized):
        if int(row["id"]) != int(account_id):
            continue
        return "primary" if normalize_email(row["alert_email"] or "") == normalized else "non-primary"
    return None


def primary_anywhere(db: Database, email: str) -> bool:
    """Whether this login is the primary contact of at least one account it
    opens. A login that is primary nowhere is an emergency contact
    everywhere, and member-scoped changes are refused to it outright."""
    normalized = normalize_email(email)
    return any(normalize_email(row["alert_email"] or "") == normalized
               for row in member_accounts(db, normalized))


def _render_refused(email: str | None) -> str:
    """The 403 for a non-primary contact. Says what happened and whom to
    ask, and says the same thing whether or not the account in the request
    exists - the refusal must not be a way to probe."""
    return _page(
        '<section class="panel"><h2>This change is for the primary contact</h2>'
        '<p>You are signed in as an emergency contact. You can see everything on these '
        'accounts and acknowledge alerts, but changes - keys, pauses, snoozes, alert '
        'preferences, deleting data - are reserved for the account\'s primary contact.</p>'
        '<p>If something needs changing, ask the primary contact to sign in and do it, or '
        'contact Minas directly. Nothing was changed by this request.</p>'
        '<p><a class="btn btn--secondary" href="/">Back to your accounts</a></p></section>',
        email, "", title="Reserved for the primary contact - Minas AI Guardian")


def materialise_scope(db: Database, email: str, actor: str | None = None) -> int:
    """Turn a legacy login's contact-row scope into explicit grants, once.
    Returns the number of grants written; 0 if already scoped. Called before
    any grant or revoke so the first explicit change starts from what the
    login already opened, never from nothing."""
    normalized = normalize_email(email)
    if not normalized or is_scoped(db, normalized):
        return 0
    now = datetime.now(timezone.utc).isoformat()
    written = 0
    if not member_exists(db, normalized):
        return 0
    # Read the contact rows BEFORE opening the writing cursor: a nested
    # cursor on the same connection deadlocks.
    rows = contact_accounts(db, normalized)
    with db.cursor() as cur:
        for row in rows:
            cur.execute(
                "INSERT OR IGNORE INTO member_account_grants (email, account_id, granted_at, granted_by) "
                "VALUES (?, ?, ?, ?)", (normalized, int(row["id"]), now, actor))
            written += cur.rowcount
        cur.execute("UPDATE portal_members SET scoped = 1 WHERE email = ?", (normalized,))
    return written


def grant_account(db: Database, email: str, account_id: int, actor: str | None = None) -> bool:
    """Let this login open one more account. Materialises a legacy scope
    first. False when the grant already existed. Recorded on the account."""
    normalized = normalize_email(email)
    if not normalized:
        raise ValueError("member email is empty")
    materialise_scope(db, normalized, actor)
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM portal_members WHERE email = ?", (normalized,))
        if cur.fetchone() is None:
            raise ValueError("no portal login for this address")
        cur.execute("SELECT 1 FROM accounts WHERE id = ?", (int(account_id),))
        if cur.fetchone() is None:
            raise ValueError("no such account")
        cur.execute(
            "INSERT OR IGNORE INTO member_account_grants (email, account_id, granted_at, granted_by) "
            "VALUES (?, ?, ?, ?)",
            (normalized, int(account_id), datetime.now(timezone.utc).isoformat(), actor))
        added = cur.rowcount > 0
        cur.execute("UPDATE portal_members SET scoped = 1 WHERE email = ?", (normalized,))
    if added:
        from sentinel.events import append_event

        append_event(db, int(account_id), "member_account_granted", "LOW",
                     {"login": normalized}, actor=actor)
    return added


def revoke_account(db: Database, email: str, account_id: int, actor: str | None = None) -> bool:
    """Stop this login opening one account. Materialises a legacy scope
    first, so removing one account never widens to none-or-all. False when
    there was nothing to revoke. Recorded on the account."""
    normalized = normalize_email(email)
    if not normalized:
        return False
    materialise_scope(db, normalized, actor)
    with db.cursor() as cur:
        cur.execute("DELETE FROM member_account_grants WHERE email = ? AND account_id = ?",
                    (normalized, int(account_id)))
        removed = cur.rowcount > 0
    if removed:
        from sentinel.events import append_event

        append_event(db, int(account_id), "member_account_revoked", "LOW",
                     {"login": normalized}, actor=actor)
    return removed


def _token_hash(token: str) -> str:
    """SHA-256, not scrypt. A reset token is 32 bytes of `secrets` output, so
    there is no dictionary to attack and no work factor worth paying - the
    hash exists to stop a database read yielding a working link, not to slow
    a guesser down. Using scrypt here would cost a second per request for no
    security this does not already have."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_reset_token(db: Database, email: str, ttl_seconds: int = RESET_TOKEN_MAX_TTL_SECONDS):
    """A single-use reset token, or None if that address has no login.

    Returns None rather than raising for an unknown address, and does the same
    work either way. "Which of these ten thousand addresses is a Minas
    customer" must not be answerable with a stopwatch - and a Minas customer
    is by definition somebody holding crypto worth monitoring. The login page
    already burns a dummy scrypt hash for the same reason; this burns a real
    token generation.

    Issuing a token retires any earlier one for that address. A customer who
    clicks "forgot password" three times otherwise has three standing bearer
    credentials sitting in their mailbox, and the older ones are exactly what
    somebody with brief mailbox access comes back for."""
    email = normalize_email(email)
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    # Capped, not trusted. "Expires within an hour" is a ceiling, and a caller
    # passing a longer TTL - a convenience feature, a test, a mistake - must
    # not be able to mint a link that outlives the promise.
    ttl = min(int(ttl_seconds or 0), RESET_TOKEN_MAX_TTL_SECONDS)
    expires_at = (now + timedelta(seconds=max(ttl, 0))).isoformat()

    if not email or not member_exists(db, email):
        return None

    with db.cursor() as cur:
        cur.execute(
            "UPDATE password_resets SET used_at = ? WHERE email = ? AND used_at IS NULL",
            (now.isoformat(), email),
        )
        cur.execute(
            "INSERT INTO password_resets (email, token_hash, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (email, _token_hash(token), now.isoformat(), expires_at),
        )
    return token


def consume_reset_token(db: Database, token: str) -> str | None:
    """The email this token belongs to, marking it used. None if it is unknown,
    expired or already redeemed.

    The address comes back from the *token record*, never from the form that
    submitted it. Trusting a form field here would let anyone with any valid
    token reset any account."""
    if not token:
        return None
    now = datetime.now(timezone.utc).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, email FROM password_resets WHERE token_hash = ? AND used_at IS NULL "
            "AND expires_at > ?",
            (_token_hash(token), now),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute("UPDATE password_resets SET used_at = ? WHERE id = ?", (now, row["id"]))
    return row["email"]


# --- sessions (HMAC-signed cookie) -------------------------------------------

def _session_secret(db: Database) -> bytes:
    with db.cursor() as cur:
        cur.execute("SELECT value FROM portal_meta WHERE key = ?", (_SECRET_KEY_ROW,))
        row = cur.fetchone()
        if row is not None:
            return bytes.fromhex(row["value"])
        secret = secrets.token_bytes(32)
        cur.execute("INSERT INTO portal_meta (key, value) VALUES (?, ?)", (_SECRET_KEY_ROW, secret.hex()))
        return secret


def make_session_token(secret: bytes, email: str, now: float | None = None,
                       epoch: int | None = None,
                       absolute_deadline: float | None = None) -> str:
    """`epoch` is the member's `session_epoch`, baked into the signed payload.

    Sessions are stateless - there is no server-side record to delete - so
    this is the only thing that can make an already-issued cookie stop
    working. Bumping the stored epoch on a password change retires every
    cookie ever issued to that member (#33).

    Two deadlines (#31): `idle` slides forward on activity (the refresh in
    `_session_email` re-issues the cookie), `hard` is set once at sign-in
    and COPIED, never extended, through every refresh - a session left open
    on a stolen laptop dies of idleness in hours, and no amount of activity
    keeps any session alive past the absolute cap."""
    now_v = now if now is not None else time.time()
    email_b64 = base64.urlsafe_b64encode(normalize_email(email).encode()).decode().rstrip("=")
    idle = int(now_v + SESSION_IDLE_SECONDS)
    hard = int(absolute_deadline if absolute_deadline is not None
               else now_v + SESSION_TTL_SECONDS)
    payload = f"{email_b64}.{idle}.{hard}.{int(epoch or 1)}"
    sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def session_hard_deadline(token: str) -> float | None:
    """The token's absolute deadline, for refreshes that must not extend it."""
    try:
        return float(token.split(".")[2])
    except (ValueError, IndexError):
        return None


def verify_session_token(secret: bytes, token: str, now: float | None = None,
                         epoch: int | None = None) -> str | None:
    """Returns the member email for a valid, unexpired, current-epoch token.

    A token whose epoch does not match the member's current one was issued
    before a password change and is refused - that is what "resetting signs
    out all other sessions" means when there is no session store. Expiry is
    the EARLIER of the idle and absolute deadlines. Pre-#31 four-field
    tokens fail the five-way split and are refused - everyone signs in
    once after the upgrade."""
    try:
        email_b64, idle_s, hard_s, epoch_s, sig = token.split(".")
        payload = f"{email_b64}.{idle_s}.{hard_s}.{epoch_s}"
        expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        now_v = now if now is not None else time.time()
        if int(idle_s) < now_v or int(hard_s) < now_v:
            return None
        if epoch is not None and int(epoch_s) != int(epoch):
            return None
        pad = "=" * (-len(email_b64) % 4)
        return base64.urlsafe_b64decode(email_b64 + pad).decode()
    except (ValueError, TypeError):
        return None




# --- pages -------------------------------------------------------------------

# The portal design system (#82), ported from the owner's external design
# pass: design/portal/ is the reference (DESIGN-SYSTEM.md + styles.css +
# five mockup screens). Ported as a SYSTEM - tokens, primitives, states -
# not pasted wholesale: mockup-only classes (state variations, remember-me)
# are dropped, and utilities the real pages need are defined here from the
# same tokens. The contract lives in docs/portal-design.md; the decisions
# (no framework, brand the portal not the dashboard, stacked key/value
# blocks instead of tables on phones, one gold primary per screen, >=48px
# touch targets) are recorded on #82.
_PORTAL_STYLE = """
  :root {
    --bg:#0d0f13; --surface:#151921; --surface-2:#1c212b;
    --border:#2a3140; --border-strong:#3a4256;
    --text:#f4f6fa; --text-muted:#aab2c1; --text-subtle:#768093;
    --text-on-gold:#16110a;
    --gold:#f0b429; --gold-press:#d99a14; --gold-line:#b8841a;
    --violet:#8b5cf6; --violet-press:#6d28d9; --violet-text:#c4b2ff; --violet-line:#7c68b8;
    --danger:#f87171; --danger-bg:rgba(248,113,113,0.10); --danger-line:rgba(248,113,113,0.45);
    --warn:#fbbf24; --warn-bg:rgba(251,191,36,0.10); --warn-line:rgba(251,191,36,0.45);
    --ok:#34d399; --ok-bg:rgba(52,211,153,0.10); --ok-line:rgba(52,211,153,0.45);
    --font-sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif,"Apple Color Emoji","Segoe UI Emoji";
    --font-serif:Georgia,"Times New Roman",Times,serif;
    --font-mono:"SF Mono","JetBrains Mono",ui-monospace,Menlo,monospace;
    --text-xs:.75rem; --text-sm:.875rem; --text-base:1rem; --text-lg:1.125rem;
    --text-xl:1.375rem; --text-2xl:1.75rem; --text-display:clamp(1.75rem,7vw,2.25rem);
    --lh-tight:1.2; --lh-snug:1.35; --lh-body:1.55; --tracking-wide:.08em;
    --space-1:.25rem; --space-2:.5rem; --space-3:.75rem; --space-4:1rem;
    --space-5:1.5rem; --space-6:2rem; --space-7:3rem; --space-8:4rem;
    --radius:6px; --radius-sm:4px;
    --container:44rem; --bar-h:8.5rem;
  }
  *, *::before, *::after { box-sizing:border-box; }
  html { -webkit-text-size-adjust:100%; text-size-adjust:100%; }
  body { margin:0; background:var(--bg); color:var(--text);
         font-family:var(--font-sans); font-size:var(--text-base);
         line-height:var(--lh-body); -webkit-font-smoothing:antialiased;
         text-rendering:optimizeLegibility; }
  body.has-actionbar { padding-bottom:var(--bar-h); }
  h1,h2,h3,h4 { margin:0; line-height:var(--lh-tight); font-weight:700; }
  h1 { font-size:var(--text-2xl); letter-spacing:-.01em; }
  h2 { font-size:var(--text-xl); line-height:var(--lh-snug); }
  h3 { font-size:var(--text-lg); line-height:var(--lh-snug); }
  p { margin:0; } p + p { margin-top:var(--space-3); }
  ul,ol { margin:0; padding:0; list-style:none; }
  a { color:var(--violet-text); text-decoration:underline; text-underline-offset:3px; }
  a:hover { text-decoration-thickness:2px; }
  strong { font-weight:700; } small { font-size:var(--text-sm); }
  :focus-visible { outline:3px solid var(--violet); outline-offset:2px; border-radius:var(--radius-sm); }
  .btn--primary:focus-visible { outline-color:var(--violet); }
  ::selection { background:var(--gold); color:var(--text-on-gold); }

  .container { width:100%; max-width:var(--container); margin-inline:auto; padding-inline:var(--space-4); }
  .main { padding:var(--space-5) 0 var(--space-7); }
  .stack > * + * { margin-top:var(--space-4); }
  .stack-lg > * + * { margin-top:var(--space-6); }
  .muted { color:var(--text-muted); } .subtle { color:var(--text-subtle); }
  .eyebrow { font-size:var(--text-xs); font-weight:700; letter-spacing:var(--tracking-wide);
             text-transform:uppercase; color:var(--text-subtle); }
  .lead { font-size:var(--text-lg); color:var(--text-muted); line-height:var(--lh-snug); }
  .page-head { margin-bottom:var(--space-5); }
  .page-head h1 { margin-bottom:var(--space-2); }

  .topbar { background:var(--surface); border-bottom:1px solid var(--border); }
  .topbar__inner { display:flex; align-items:center; justify-content:space-between;
                   gap:var(--space-4); min-height:56px; padding-block:var(--space-3); }
  .brand { display:inline-flex; align-items:center; gap:var(--space-3); color:var(--text);
           text-decoration:none; font-weight:700; font-size:var(--text-base); letter-spacing:-.005em; }
  .brand:hover { text-decoration:none; }
  .brand__mark { width:28px; height:28px; flex:none; color:var(--gold); }
  .brand__name span { color:var(--text-muted); font-weight:600; }
  .brand__stack { display:flex; flex-direction:column; line-height:1.25; }
  .brand__tag { font-size:var(--text-xs); font-weight:700; letter-spacing:var(--tracking-wide);
                text-transform:uppercase; color:var(--gold); }
  .auth-card__brand .brand__tag { margin-top:-6px; }
  .topbar__meta { font-size:var(--text-sm); color:var(--text-muted); text-align:right; }
  .topbar__meta a { white-space:nowrap; }
  .linkbtn { font:inherit; background:none; border:none; padding:0; cursor:pointer;
             color:var(--violet-text); text-decoration:underline; text-underline-offset:3px;
             min-height:44px; }
  .subnav { border-top:1px solid var(--border); background:var(--surface); }
  .subnav__inner { display:flex; gap:var(--space-5); overflow-x:auto;
                   scrollbar-width:thin; -webkit-overflow-scrolling:touch; }
  .subnav a { display:inline-flex; align-items:center; min-height:48px; color:var(--text-muted);
              text-decoration:none; font-size:var(--text-sm); font-weight:600;
              border-bottom:2px solid transparent; white-space:nowrap; padding-inline:2px; }
  .subnav a:hover { color:var(--text); }
  .subnav a[aria-current="page"] { color:var(--text); border-bottom-color:var(--gold); }

  .footer { border-top:1px solid var(--border); padding:var(--space-5) 0; margin-top:var(--space-7);
            color:var(--text-subtle); font-size:var(--text-sm); }
  .footer__row { display:flex; flex-wrap:wrap; gap:var(--space-4) var(--space-5); }
  .footer__row a { color:var(--text-muted); }
  .footer__tag { font-weight:600; color:var(--text-muted); }

  .btn { display:inline-flex; align-items:center; justify-content:center; gap:var(--space-2);
         min-height:48px; padding:var(--space-3) var(--space-5); font:inherit; font-weight:700;
         font-size:var(--text-base); line-height:1; border:1px solid transparent;
         border-radius:var(--radius); text-decoration:none; cursor:pointer;
         transition:none; white-space:nowrap; }
  .btn:disabled, .btn[aria-disabled="true"] { background:var(--surface-2); color:var(--text-subtle);
         border-color:var(--border); cursor:not-allowed; pointer-events:none; }
  .btn--primary { background:var(--gold); color:var(--text-on-gold); border-color:var(--gold-line); }
  .btn--primary:hover { background:var(--gold-press); }
  .btn--primary:active { background:var(--gold-press); }
  .btn--secondary { background:transparent; color:var(--violet-text); border-color:var(--violet-line); }
  .btn--secondary:hover { background:rgba(139,92,246,0.12); border-color:var(--violet); }
  .btn--tertiary { background:transparent; color:var(--text-muted); min-height:44px; padding-inline:var(--space-3); }
  .btn--tertiary:hover { color:var(--text); }
  .btn--block { width:100%; }
  .btn--sm { min-height:40px; padding-block:var(--space-2); font-size:var(--text-sm); }
  a.btn { text-align:center; }

  .skip-link { position:absolute; left:var(--space-3); top:-3rem; z-index:100; background:var(--gold);
               color:var(--text-on-gold); padding:var(--space-3) var(--space-4);
               border-radius:var(--radius); font-weight:700; text-decoration:none; }
  .skip-link:focus { top:var(--space-3); }

  .panel { background:var(--surface); border:1px solid var(--border);
           border-radius:var(--radius); padding:var(--space-5); }
  .panel + .panel { margin-top:var(--space-4); }
  .panel h2 { margin-bottom:var(--space-3); }
  .panel h3 { margin-bottom:var(--space-2); }

  .badge { display:inline-flex; align-items:center; gap:var(--space-2); font-size:var(--text-xs);
           font-weight:700; letter-spacing:var(--tracking-wide); text-transform:uppercase;
           padding:var(--space-1) var(--space-3); border-radius:999px;
           border:1px solid var(--border-strong); background:var(--surface-2);
           color:var(--text-muted); line-height:1.6; white-space:nowrap; }
  .badge .dot { width:8px; height:8px; border-radius:50%; background:currentColor; }
  .badge--ok { color:var(--ok); border-color:var(--ok-line); background:var(--ok-bg); }
  .badge--warn { color:var(--warn); border-color:var(--warn-line); background:var(--warn-bg); }
  .badge--danger { color:var(--danger); border-color:var(--danger-line); background:var(--danger-bg); }
  .badge--neutral { color:var(--text-muted); }
  .badge--lg { font-size:var(--text-sm); padding:var(--space-2) var(--space-4);
               letter-spacing:.04em; text-transform:none; }

  /* Key/value rows: THE answer to tables on a phone (stacked; two-column
     from 40rem). Every fact list uses this; there are no data tables. */
  .kv { margin:0; }
  .kv__row { display:grid; gap:2px; padding-block:var(--space-3); border-top:1px solid var(--border); }
  .kv__row:first-child { border-top:0; padding-top:var(--space-3); }
  .kv__row:last-child { padding-bottom:0; }
  .kv dt { font-size:var(--text-xs); font-weight:700; letter-spacing:var(--tracking-wide);
           text-transform:uppercase; color:var(--text-subtle); }
  .kv dd { margin:0; font-size:var(--text-base); color:var(--text); }
  .kv dd.muted { color:var(--text-muted); }
  @media (min-width:40rem) {
    .kv__row { grid-template-columns:12rem 1fr; align-items:baseline; gap:var(--space-4); }
  }

  .acct { background:var(--surface); border:1px solid var(--border);
          border-radius:var(--radius); padding:var(--space-5); }
  .acct + .acct { margin-top:var(--space-3); }
  .acct__head { display:flex; flex-wrap:wrap; align-items:flex-start; justify-content:space-between;
                gap:var(--space-3) var(--space-4); margin-bottom:var(--space-3); }
  .acct__label { font-size:var(--text-lg); font-weight:700; }
  .acct__exchange { color:var(--text-muted); font-size:var(--text-sm); margin-top:2px; }

  .problem { margin-top:var(--space-4); padding:var(--space-4); border:1px solid var(--danger-line);
             background:var(--danger-bg); border-radius:var(--radius-sm); }
  .problem--warn { border-color:var(--warn-line); background:var(--warn-bg); }
  .problem__title { font-weight:700; color:var(--text); margin-bottom:2px; }
  .problem--warn .problem__title { color:var(--warn); }
  .problem__body { color:var(--text-muted); font-size:var(--text-sm); }
  .problem__fix { margin-top:var(--space-3); display:flex; flex-wrap:wrap;
                  align-items:center; gap:var(--space-3); }
  .fix-tag { font-size:var(--text-xs); font-weight:700; letter-spacing:var(--tracking-wide);
             text-transform:uppercase; color:var(--ok); }
  .fix-tag--muted { color:var(--text-muted); }

  .state { border:1px solid var(--border); border-radius:var(--radius);
           padding:var(--space-5); margin-bottom:var(--space-5); }
  .state__eyebrow { display:inline-flex; align-items:center; gap:var(--space-2);
                    font-size:var(--text-xs); font-weight:700; letter-spacing:var(--tracking-wide);
                    text-transform:uppercase; margin-bottom:var(--space-3); }
  .state__answer { font-size:var(--text-display); font-weight:800; line-height:var(--lh-tight);
                   letter-spacing:-.015em; margin-bottom:var(--space-3); }
  .state__detail { color:var(--text-muted); font-size:var(--text-lg);
                   line-height:var(--lh-snug); max-width:38ch; }
  .state--ok { border-color:var(--ok-line); background:var(--ok-bg); }
  .state--ok .state__eyebrow { color:var(--ok); }
  .state--warn { border-color:var(--warn-line); background:var(--warn-bg); }
  .state--warn .state__eyebrow { color:var(--warn); }
  .state--danger { border-color:var(--danger-line); background:var(--danger-bg); }
  .state--danger .state__eyebrow { color:var(--danger); }
  .state--ok .state__answer, .state--warn .state__answer, .state--danger .state__answer { color:var(--text); }
  .state__eyebrow svg { width:16px; height:16px; }

  .alert-head { margin-bottom:var(--space-5); }
  .alert-head .badge { margin-bottom:var(--space-3); }
  .alert-head h1 { font-size:var(--text-2xl); margin-bottom:var(--space-2); }
  .alert-meta { display:grid; gap:var(--space-2); color:var(--text-muted); font-size:var(--text-sm); }
  .steps { counter-reset:step; margin-top:var(--space-3); }
  .steps li { position:relative; padding:var(--space-3) 0 var(--space-3) calc(var(--space-6) + var(--space-3));
              border-top:1px solid var(--border); color:var(--text); }
  .steps li:first-child { border-top:0; }
  .steps li::before { counter-increment:step; content:counter(step); position:absolute;
                      left:0; top:var(--space-3); width:var(--space-6); height:var(--space-6);
                      display:inline-flex; align-items:center; justify-content:center;
                      background:var(--surface-2); border:1px solid var(--border-strong);
                      border-radius:50%; font-size:var(--text-sm); font-weight:700; color:var(--gold); }
  .steps li strong { display:block; color:var(--text); }
  .steps li span { color:var(--text-muted); font-size:var(--text-sm); }

  .primary-row { margin-top:var(--space-5); display:flex; flex-direction:column; gap:var(--space-3); }
  .actionbar { position:fixed; left:0; right:0; bottom:0; background:var(--surface);
               border-top:1px solid var(--border-strong); z-index:20;
               padding:var(--space-3) var(--space-4) calc(var(--space-3) + env(safe-area-inset-bottom, 0px)); }
  .actionbar__note { font-size:var(--text-sm); color:var(--text-muted); margin:0 0 var(--space-2); }
  .actionbar__note strong { color:var(--text); }

  .form { display:grid; gap:var(--space-5); }
  .field { display:grid; gap:var(--space-2); }
  .field label { font-size:var(--text-sm); font-weight:600; color:var(--text); }
  .field .hint, .hint { font-size:var(--text-sm); color:var(--text-muted); }
  .input { width:100%; min-height:48px; padding:var(--space-3) var(--space-4); font:inherit;
           font-size:var(--text-base); color:var(--text); background:var(--bg);
           border:1px solid var(--border-strong); border-radius:var(--radius); appearance:none; }
  .input::placeholder { color:var(--text-subtle); }
  .input:focus-visible { outline:3px solid var(--violet); outline-offset:1px; border-color:var(--violet); }
  .input[aria-invalid="true"] { border-color:var(--danger); box-shadow:inset 0 0 0 1px var(--danger); }
  .field--row { display:flex; align-items:center; justify-content:space-between; gap:var(--space-3); }
  .field--row label { margin:0; }

  .notice { display:grid; grid-template-columns:auto 1fr; gap:var(--space-3);
            padding:var(--space-4); border-radius:var(--radius);
            border:1px solid var(--border); background:var(--surface-2); }
  .notice--danger { border-color:var(--danger-line); background:var(--danger-bg); }
  .notice--info { border-color:var(--violet-line); background:rgba(139,92,246,0.10); }
  .notice--ok { border-color:var(--ok-line); background:var(--ok-bg); }
  .notice__icon { width:20px; height:20px; flex:none; }
  .notice--danger .notice__icon { color:var(--danger); }
  .notice--info .notice__icon { color:var(--violet-text); }
  .notice--ok .notice__icon { color:var(--ok); }
  .notice__title { font-weight:700; margin:0; }
  .notice__body { margin-top:2px; color:var(--text-muted); font-size:var(--text-sm); }

  .phish { display:flex; gap:var(--space-3); font-size:var(--text-sm);
           color:var(--text-muted); line-height:var(--lh-snug); }
  .phish svg { width:18px; height:18px; flex:none; color:var(--gold); margin-top:1px; }

  .callout-danger { border:1px solid var(--danger-line); background:var(--danger-bg);
                    border-radius:var(--radius); padding:var(--space-5); margin-bottom:var(--space-5); }
  .callout-danger h2 { font-size:var(--text-xl); color:var(--text); margin-bottom:var(--space-2); }
  .callout-danger p { color:var(--text-muted); font-size:var(--text-lg); line-height:var(--lh-snug); }
  .feature-list { display:grid; gap:var(--space-3); }
  .feature-list li { display:grid; grid-template-columns:auto 1fr; gap:var(--space-3); align-items:start; }
  .feature-list .tick { width:20px; height:20px; flex:none; margin-top:2px; }
  .feature-list .tick--yes { color:var(--ok); }
  .feature-list .tick--no { color:var(--danger); }
  .feature-list strong { display:block; color:var(--text); }
  .feature-list span { color:var(--text-muted); font-size:var(--text-sm); }

  .shell { min-height:100dvh; display:flex; flex-direction:column; }
  .shell__main { flex:1; display:flex; align-items:center; padding:var(--space-6) 0; }
  .auth-card { width:100%; max-width:26rem; margin-inline:auto; background:var(--surface);
               border:1px solid var(--border); border-radius:var(--radius);
               padding:var(--space-6) var(--space-5); }
  .auth-card__brand { display:flex; flex-direction:column; align-items:center; gap:var(--space-3);
                      text-align:center; margin-bottom:var(--space-6); }
  .auth-card__brand .brand__mark { width:36px; height:36px; }
  .auth-card__brand .brand__name { font-size:var(--text-lg); font-weight:700; }
  .auth-card__brand .brand__name span { display:block; font-size:var(--text-sm); color:var(--text-muted); }

  /* Utilities the real pages need, from the same tokens */
  .mono { font-family:var(--font-mono); font-size:var(--text-sm);
          font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
  .meta { font-size:var(--text-sm); color:var(--text-subtle); }
  .empty { color:var(--text-subtle); }
  .count { font-size:var(--text-sm); color:var(--text-subtle); font-variant-numeric:tabular-nums; }
  .acctlink { font-weight:600; }
  .ack-yes { color:var(--ok); font-size:var(--text-sm); }
  .ack-no { color:var(--text-subtle); }
  .ctl-row { display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-2);
             margin-top:var(--space-2); }
  .input--inline { width:auto; min-width:0; }
  .alert-item { padding-block:var(--space-4); border-top:1px solid var(--border); }
  .alert-item:first-of-type { border-top:0; }
  .alert-item__head { display:flex; flex-wrap:wrap; align-items:center;
                      gap:var(--space-2) var(--space-3); margin-bottom:var(--space-1); }

  @media (min-width:40rem) {
    .primary-row { flex-direction:row; align-items:center; }
    .primary-row .btn { width:auto; }
  }
  @media (min-width:48rem) {
    body.has-actionbar { padding-bottom:0; }
    .container { padding-inline:var(--space-5); }
    .actionbar { position:static; background:none; border-top:0; padding:var(--space-6) 0 0; }
    .actionbar__inner { display:flex; align-items:center; justify-content:space-between; gap:var(--space-5); }
    .actionbar__note { margin:0; max-width:26rem; text-align:right; }
    .actionbar .btn--block { width:auto; min-width:14rem; }
  }

  /* Print: the status page is a document handed to an exchange, an insurer
     or the police. Serif, monochrome, masthead; interactive chrome gone. */
  @media print {
    .topbar, .subnav, .footer, .actionbar, .no-print, .noprint, .skip-link,
    .primary-row { display:none !important; }
    body { background:#fff !important; color:#000 !important;
           font-size:11pt; line-height:1.45; font-family:var(--font-serif); }
    body.has-actionbar { padding-bottom:0 !important; }
    .container { max-width:100% !important; padding:0 !important; margin:0 !important; }
    .main { padding:0 !important; }
    h1,h2,h3 { color:#000 !important; }
    a { color:#000 !important; text-decoration:none; }
    .panel, .acct, .state { background:#fff !important; border-color:#000 !important;
      border-width:1px !important; break-inside:avoid; page-break-inside:avoid;
      padding:10pt 12pt !important; }
    .print-masthead { display:block !important; }
    .badge { background:#fff !important; color:#000 !important; border:1px solid #000 !important; }
    .badge .dot { display:none; }
    .state { border:2px solid #000 !important; background:#fff !important; }
    .state--ok, .state--warn, .state--danger { background:#fff !important; }
    .state__answer { font-size:20pt !important; color:#000 !important; }
    .state__eyebrow { color:#000 !important; }
    .problem, .problem--warn { background:#fff !important; border:1px solid #000 !important; }
    .kv__row { border-top-color:#999 !important; }
    .kv dt, .kv dd { color:#000 !important; }
    @page { margin:18mm 16mm; }
  }
  .print-masthead { display:none; }
  .print-masthead__title { font-family:var(--font-serif); font-size:16pt; font-weight:700; margin:0 0 4pt; }
  .print-masthead__sub { font-size:10pt; color:#333; margin:0 0 2pt; }
  @media (prefers-reduced-motion: reduce) {
    * { animation:none !important; transition:none !important; }
  }
"""


# Badge helpers: status and severity as icon-dot + word, never colour alone
# (design/portal/DESIGN-SYSTEM.md). Labels are customer words, not our
# column values.
_STATUS_BADGE = {
    "active": ("badge--ok", "Active"),
    "disabled": ("badge--neutral", "Disabled"),
    "tamper_suspected": ("badge--danger", "Suspected tampering"),
}

# Severity tones: CRITICAL is the red that means act now; HIGH warns; the
# rest stay neutral so the loud tones keep their meaning.
_SEV_BADGE = {
    "CRITICAL": "badge--danger",
    "HIGH": "badge--warn",
    "MEDIUM": "badge--neutral",
    "LOW": "badge--neutral",
}


def _status_pill(status: str) -> str:
    cls, label = _STATUS_BADGE.get(status, ("badge--neutral", status.replace("_", " ")))
    return (f'<span class="badge {cls}"><span class="dot" aria-hidden="true"></span>'
            f"{html.escape(label)}</span>")


def _sev_badge(severity: str, large: bool = False) -> str:
    cls = _SEV_BADGE.get(severity, "badge--neutral")
    size = " badge--lg" if large else ""
    return (f'<span class="badge {cls}{size}"><span class="dot" aria-hidden="true"></span>'
            f"{html.escape(severity.title())}</span>")


# The subnav, in one place (the old _member_nav's reasoning survives): a
# page reachable from some screens and not others is how the reader who most
# needs "what are the limits" arrives on the one page that doesn't link it.
_NAV_ITEMS = (
    ("/", "My accounts"),
    ("/status", "Am I being watched?"),
    ("/alerts", "Alerts"),
    ("/activity", "Activity"),
    ("/limits", "Limits"),
    ("/security", "Security"),
)


def _page(body_html: str, email: str | None = None, active: str = "",
          *, title: str = "Minas AI Guardian", shell: bool = False,
          actionbar: bool = False, back: str | None = None) -> str:
    """The frame, per design/portal/: topbar (brand + signed-in meta),
    scrollable subnav with the current page underlined in gold, footer.
    `shell` centres an auth card vertically (sign-in family, no nav);
    `actionbar` reserves bottom space for a fixed action bar; `back`
    replaces the subnav with a single return link (alert detail)."""
    brand = (
        '<a class="brand" href="/" aria-label="Minas AI Guardian home">' + MARK_SVG
        + '<span class="brand__stack"><span class="brand__name">Minas AI <span>Guardian</span></span>'
        '<span class="brand__tag">Member portal</span></span></a>'
    )
    meta = ""
    if back:
        meta = f'<div class="topbar__meta"><a href="{back}">Back to accounts</a></div>'
    elif email:
        meta = (
            '<div class="topbar__meta"><span class="subtle">Signed in as</span> '
            + html.escape(email)
            + '<br><form method="post" action="/logout" style="margin:0;display:inline">'
              '<button class="linkbtn" type="submit">Sign out</button></form></div>'
        )
    subnav = ""
    if email and not back:
        current = " aria-current='page'"
        links = "".join(
            f"<a href='{href}'{current if href == active else ''}>{label}</a>"
            for href, label in _NAV_ITEMS
        )
        subnav = (f'<nav class="subnav" aria-label="Primary">'
                  f'<div class="container subnav__inner">{links}</div></nav>')
    topbar = (f'<header class="topbar"><div class="container topbar__inner">'
              f"{brand}{meta}</div>{subnav}</header>")
    footer = (
        '<footer class="footer"><div class="container footer__row">'
        '<span class="footer__tag">Minas AI Guardian</span>'
        '<a href="/limits">What this product can and cannot do</a>'
        '<span class="subtle">Read-only monitoring. We cannot move your funds.</span>'
        "</div></footer>"
    )
    if shell:
        main = f'<main id="main" class="shell__main"><div class="container">{body_html}</div></main>'
        footer = ""
    else:
        main = f'<main id="main" class="main"><div class="container stack-lg">{body_html}</div></main>'
    body_cls = "shell" if shell else ("has-actionbar" if actionbar else "")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        '<meta name="color-scheme" content="dark">'
        f"<title>{html.escape(title)} — Member portal</title><style>" + _PORTAL_STYLE + "</style></head>"
        f'<body class="{body_cls}">'
        '<a class="skip-link" href="#main">Skip to content</a>'
        + topbar + main + footer
        + _LOCAL_TIME_SCRIPT + "</body></html>"
    )


def _member_alert(db: Database, alert_id: int, account_ids: list[int]):
    """An alert row, but only if it is on one of this member's accounts.
    Returns None for someone else's alert and for one that doesn't exist — the
    callers render both as not-found, so a member cannot use acknowledgement or
    a snooze to discover that an alert id is in use."""
    if not account_ids:
        return None
    marks = ",".join("?" for _ in account_ids)
    with db.cursor() as cur:
        cur.execute(
            f"SELECT id, account_id, rule, dedup_key, severity FROM alerts "
            f"WHERE id = ? AND account_id IN ({marks})",
            [alert_id, *account_ids],
        )
        return cur.fetchone()


def acknowledge_alert_as_member(
    db: Database, alert_id: int, email: str, account_ids: list[int]
) -> bool:
    """Member-side acknowledgement (G-22). False if the alert isn't theirs or
    was already acknowledged.

    The account scope is in the UPDATE itself rather than in a check before it.
    A member acknowledging an alert makes the product quieter — it is what
    stops escalation — so "which alerts may I silence" is a boundary that has
    to hold inside the statement that does the silencing, not in a caller that
    a later route might forget to run.

    `acknowledged_at IS NULL` keeps the first acknowledgement. See
    `operators.acknowledge_alert`, which holds the same rule for the operator
    path: an acknowledgement is a claim someone made at a moment, and the
    second person to open the incident does not get to become the first."""
    normalized = normalize_email(email)
    if not normalized or not account_ids:
        return False
    marks = ",".join("?" for _ in account_ids)
    with db.cursor() as cur:
        cur.execute(
            f"UPDATE alerts SET acknowledged_at = ?, acknowledged_by = ? "
            f"WHERE id = ? AND acknowledged_at IS NULL AND account_id IN ({marks})",
            [datetime.now(timezone.utc).isoformat(), normalized, alert_id, *account_ids],
        )
        acknowledged = cur.rowcount == 1
    if acknowledged:
        # Decision 1 (#27): same closing note as the operator path - hooking
        # only one acknowledgement writer would leave the other silently mute.
        from sentinel.alerts.escalation import send_critical_ack_closing_note

        send_critical_ack_closing_note(db, alert_id)
        # Siblings from the same customer action (#99). Same-account by the
        # sibling definition, so still inside this member's scope by
        # construction - and re-checked anyway by recursing through this
        # same scoped writer, because a boundary held in one place is a
        # boundary a later definition change could silently move.
        from sentinel.alerts.siblings import sibling_alert_ids

        for sibling in sibling_alert_ids(db, alert_id):
            acknowledge_alert_as_member(db, sibling, email, account_ids)
    return acknowledged


def _time_to_acknowledge(sent_at: str | None, acknowledged_at: str | None) -> str:
    """How long the alert waited, as a short human string.

    This is the number that says whether the alerting works. Four minutes means
    someone read it; nine hours means someone eventually found it. Both are
    already recorded — `sent_at` and `acknowledged_at` — but a pair of
    timestamps is not something a customer subtracts in their head."""
    if not sent_at or not acknowledged_at:
        return ""
    try:
        seconds = int(
            (datetime.fromisoformat(acknowledged_at) - datetime.fromisoformat(sent_at))
            .total_seconds()
        )
    except (TypeError, ValueError):
        return ""
    if seconds < 0:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"


def _snooze_control(row, snoozed_until: str | None, rules=None) -> str:
    """A snooze offer, or the silence already in force.

    Shown only while the alert is unacknowledged: once someone has said they
    have seen it, "stop telling me about this" is what acknowledgement already
    means, and offering both invites silencing an alert nobody has looked at
    twice."""
    if snoozed_until:
        return f"<p class='meta'>Snoozed until {_ts(snoozed_until)}</p>"
    choices = snooze_choices(row["severity"], rules)
    if not choices:
        return ""  # a zero cap: this severity cannot be snoozed
    options = "".join(f"<option value='{h}'>{h}h</option>" for h in choices)
    return (
        "<form method='post' action='/snooze' class='ctl-row'>"
        f"<input type='hidden' name='alert_id' value=\"{int(row['id'])}\">"
        f"<select class='input input--inline' name='hours' aria-label='Snooze for'>{options}</select>"
        "<button class='btn btn--sm btn--tertiary' type='submit'>Snooze</button></form>"
    )


def _expected_control(row, marked: bool) -> str:
    """"That was me" (G-24, #28).

    Offered next to acknowledgement rather than instead of it, because they say
    different things: acknowledging is "I have seen this", marking is "this was
    my own activity and the detector was wrong". Conflating them would lose the
    only signal a customer can give us about whether a rule is any good.

    Shown once. A second click tells us nothing new, and the row saying
    "you marked this" is the answer to the question the button asks."""
    if marked:
        return "<p class='meta'>You marked this as your own activity</p>"
    return (
        "<form method='post' action='/expected' class='ctl-row'>"
        f"<input type='hidden' name='alert_id' value=\"{int(row['id'])}\">"
        "<input class='input input--inline' type='text' name='note' maxlength='500' "
        "placeholder='Optional note' aria-label='Optional note'>"
        "<button class='btn btn--sm btn--tertiary' type='submit'>That was me</button></form>"
    )


def _ack_cell(row, snoozed_until: str | None = None, rules=None,
              marked: bool = False) -> str:
    """The acknowledgement column: who and how long if it's done, a button if
    it isn't. One tap, from the row the alert is on.

    "That was me" stays available after acknowledgement. The order people
    actually do this in is acknowledge first, in a hurry, and recognise their
    own activity a minute later - and the recognition is the half we most want
    recorded."""
    if row["acknowledged_at"]:
        who = html.escape(row["acknowledged_by"] or "—")
        elapsed = _time_to_acknowledge(row["sent_at"], row["acknowledged_at"])
        suffix = f" · {elapsed}" if elapsed else ""
        return (f"<div class='ack-yes'>✓ Acknowledged by {who}{suffix}</div>"
                f"{_expected_control(row, marked)}")
    return (
        "<div class='ctl-row'><form method='post' action='/acknowledge' style='margin:0'>"
        f"<input type='hidden' name='alert_id' value=\"{int(row['id'])}\">"
        "<button class='btn btn--secondary' type='submit'>Acknowledge</button></form></div>"
        f"{_snooze_control(row, snoozed_until, rules)}"
        f"{_expected_control(row, marked)}"
    )


_ICON_DANGER = (
    '<svg class="notice__icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">'
    '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>'
    '<path d="M12 7v6M12 16.5v.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>'
)
_ICON_INFO = (
    '<svg class="notice__icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">'
    '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>'
    '<path d="M12 11v5M12 8v.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>'
)
_ICON_SHIELD = (
    '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
    '<path d="M12 3l8 3v5c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-3z" '
    'stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>'
    '<path d="M9 12l2 2 4-4" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


def _auth_brand() -> str:
    return (
        '<div class="auth-card__brand">' + MARK_SVG
        + '<div class="brand__name">Minas AI <span>Guardian</span></div>'
        '<div class="brand__tag">Member portal</div></div>'
    )


def _render_login(error: str | None = None) -> str:
    invalid = ' aria-invalid="true"' if error else ""
    notice = ""
    if error:
        notice = (
            '<div class="notice notice--danger" role="alert">' + _ICON_DANGER
            + f'<div><p class="notice__title">{html.escape(error)}</p>'
            '<p class="notice__body">Please try again. Caps Lock may be on. If you have '
            'forgotten your password, reset it rather than retrying.</p></div></div>'
        )
    body = (
        '<section class="auth-card" aria-labelledby="signin-title">' + _auth_brand()
        + '<h1 id="signin-title">Sign in</h1>'
        '<p class="muted" style="margin:.5rem 0 1.5rem">Monitor your exchange accounts. '
        "Read-only access only.</p>" + notice
        + '<form class="form" method="post" action="/login" style="margin-top:1.5rem">'
        '<div class="field"><label for="email">Email address</label>'
        f'<input class="input" type="email" id="email" name="email" required '
        f'autocomplete="username" inputmode="email"{invalid}></div>'
        '<div class="field"><div class="field--row"><label for="password">Password</label>'
        '<a href="/forgot" class="btn btn--tertiary" style="padding-inline:var(--space-2)">'
        "Forgot password?</a></div>"
        f'<input class="input" type="password" id="password" name="password" required '
        f'autocomplete="current-password"{invalid}>'
        '<span class="hint">Never enter your password after following a link from an '
        "email. Open the portal address you were given directly.</span></div>"
        '<button type="submit" class="btn btn--primary btn--block">Sign in</button>'
        "</form>"
        '<div class="phish" style="margin-top:1.5rem;padding-top:1.25rem;border-top:1px solid var(--border)">'
        + _ICON_SHIELD
        + '<p><strong style="color:var(--text)">Minas will never email you a link asking '
        "for your API key.</strong> Always open this site and sign in to check your "
        "accounts — don't click through from email or messages.</p></div>"
        "</section>"
    )
    return _page(body, title="Sign in — Minas AI Guardian", shell=True)


def _repeat_note(r) -> str:
    """One ongoing problem is one alert - and the alert says so (#22). A
    repeat suppressed by the cooldown is counted on the row it repeated, and
    this renders that count wherever the alert is listed, so suppression
    never becomes concealment."""
    n = r["repeat_count"] if "repeat_count" in r.keys() else 0
    if not n:
        return ""
    return (f"<span class='meta'>still firing — repeated {int(n)}× since, "
            f"last {_ts(r['last_repeat_at'])}</span>")


def _pause_section(db: Database, account_id: int, status: str) -> str:
    """Request or cancel the 24-hour pause countdown (#98, decision 15).
    The explainer IS the security design said out loud: the wait, the
    linkless emails, and the phone-Minas fast path are why a stolen
    session cannot quietly turn monitoring off."""
    from sentinel.pause import PAUSE_COUNTDOWN_HOURS, pending_pause

    pending = pending_pause(db, account_id)
    if pending is not None:
        return (
            '<section class="panel" aria-labelledby="pause-title">'
            '<div class="state state--warn" role="alert">'
            '<span class="state__eyebrow">' + _EYE_WARN + "Pause pending</span>"
            f'<p class="state__answer">Monitoring stops {_ts(pending["execute_at"])} '
            "unless cancelled.</p>"
            '<p class="state__detail">Every contact on this account was emailed when this '
            "was requested. If you did not request it, contact Minas directly — we never "
            "email links, and a phone call works even if you cannot sign in.</p></div>"
            '<form class="form" method="post" action="/pause/cancel" style="margin-top:var(--space-4)">'
            f'<input type="hidden" name="account_id" value="{account_id}">'
            '<button class="btn btn--primary" type="submit">Cancel the pause</button>'
            "</form></section>"
        )
    if status != "active":
        return ""
    return (
        '<section class="panel" aria-labelledby="pause-title"><h2 id="pause-title">Pause monitoring</h2>'
        f'<p class="muted">Requesting a pause starts a {PAUSE_COUNTDOWN_HOURS}-hour '
        "countdown: monitoring stops automatically when it ends, unless you (or Minas) "
        "cancel first. Every contact on the account is emailed at the request and again "
        "near the end. The wait is deliberate — an instant off-switch is what someone "
        "with a stolen session would want. Need it paused right now? Call Minas; a "
        "person can do it immediately.</p>"
        '<form class="form" method="post" action="/pause/request" style="margin-top:var(--space-4)">'
        f'<input type="hidden" name="account_id" value="{account_id}">'
        '<button class="btn btn--secondary" type="submit">Request a pause</button>'
        "</form></section>"
    )


def _watch_state(watch: dict | None) -> tuple[str, str]:
    """The tri-state a customer reads on an account card (#34, #10):
    Watching (checked recently, nothing failing), Paused (deliberately
    disabled - stillness by choice, not a fault), Trouble (everything else:
    stale, rejected key, tamper-flagged). Derived from member_status's
    judgement, never recomputed here - the card and the /status page must
    not be able to disagree."""
    if watch is None:
        return ("badge--danger", "Trouble")
    if watch["watched"]:
        return ("badge--ok", "Watching")
    if watch["status"] == "disabled":
        return ("badge--neutral", "Paused")
    return ("badge--danger", "Trouble")


def _render_home(db: Database, email: str, rules=None) -> str:
    accounts = member_accounts(db, email)
    ids = [r["id"] for r in accounts]
    watch = member_status(db, email, rules)
    watch_by_id = {a["account_id"]: a for a in watch["accounts"]}
    alerts = []
    if ids:
        marks = ",".join("?" for _ in ids)
        with db.cursor() as cur:
            cur.execute(
                f"SELECT al.id, al.sent_at, al.severity, al.rule, al.acknowledged_at, al.acknowledged_by, al.dedup_key, al.repeat_count, al.last_repeat_at, ac.label "
                f"FROM alerts al JOIN accounts ac ON ac.id = al.account_id "
                f"WHERE al.account_id IN ({marks}) ORDER BY al.sent_at DESC LIMIT 30",
                ids,
            )
            alerts = cur.fetchall()
    snoozed = active_snoozes_for_accounts(db, ids)
    marked = marked_alert_ids(db, ids)

    def _card(r) -> str:
        w = watch_by_id.get(int(r["id"]))
        cls, word = _watch_state(w)
        badge = (f'<span class="badge {cls}"><span class="dot" aria-hidden="true"></span>'
                 f"{word}</span>")
        # Trouble says what is wrong in the card itself; the customer must
        # not need to guess which of the two pages carries the reason.
        reason = ""
        if w and not w["watched"] and w["reason"] and word == "Trouble":
            reason = ('<div class="kv__row"><dt>What\'s wrong</dt>'
                      f"<dd>{html.escape(w['reason'])}</dd></div>")
        return (
            '<article class="acct"><div class="acct__head"><div>'
            f"<p class=\"acct__label\"><a class='acctlink' href='/account?id={r['id']}'>"
            f"{html.escape(r['label'])} ›</a></p>"
            f"<p class=\"acct__exchange\">{html.escape((r['exchange'] or 'bitget').title())}"
            f" · {_account_type_label(r['topology'])}</p>"
            f"</div>{badge}</div>"
            '<dl class="kv"><div class="kv__row"><dt>Last successful check</dt>'
            f"<dd>{_ts(r['last_success_at'], 'Never')}</dd></div>"
            f"{reason}"
            '<div class="kv__row"><dt>Key permissions</dt><dd>Read-only</dd></div></dl>'
            "</article>"
        )

    account_cards = "".join(
        _card(r) for r in accounts
    ) or '<p class="empty">No accounts yet — contact the Minas team.</p>'

    # The prominent banner (#34): if ANY account is not being watched, the
    # home screen says so before anything else - a customer who stops at the
    # first screen must still learn it. Paused-by-choice accounts still
    # count: paused IS not watched, and the banner is where that stays
    # visible.
    # A pending pause is "about to not be watched" (#98): its own banner,
    # shown even when everything is currently green - the whole point is
    # warning BEFORE the stillness starts, on the one screen everyone sees.
    from sentinel.pause import pending_pauses_for

    pending = pending_pauses_for(db, [int(i) for i in ids])
    pause_banner = ""
    if pending:
        by_id = {int(r["id"]): r for r in accounts}
        lines = "; ".join(
            f"{html.escape(str(by_id[aid]['label']))} at {_ts(p['execute_at'])}"
            for aid, p in sorted(pending.items()))
        pause_banner = (
            '<div class="state state--warn" role="alert" style="margin-bottom:var(--space-5)">'
            f'<span class="state__eyebrow">{_EYE_WARN}Pause pending</span>'
            f'<p class="state__answer">Monitoring is about to stop: {lines}.</p>'
            '<p class="state__detail">Cancel from the account page — and if you did not '
            "request this, contact Minas directly. We never email links.</p></div>"
        )

    banner = ""
    if accounts and watch["overall"] != "watching":
        dark = [a for a in watch["accounts"] if not a["watched"]]
        if watch["overall"] == "not_watching":
            cls, icon = "state--danger", _EYE_DANGER
            answer = "Your accounts are NOT being watched."
        else:
            cls, icon = "state--warn", _EYE_WARN
            answer = (f"{len(dark)} of your {len(accounts)} accounts "
                      f"{'is' if len(dark) == 1 else 'are'} not being watched.")
        banner = (
            f'<div class="state {cls}" role="alert" style="margin-bottom:var(--space-5)">'
            f'<span class="state__eyebrow">{icon}Attention</span>'
            f'<p class="state__answer">{answer}</p>'
            '<p class="state__detail"><a class="acctlink" href="/status">'
            "See what each account needs ›</a></p></div>"
        )

    alert_items = "".join(
        '<div class="alert-item"><div class="alert-item__head">'
        f"{_sev_badge(r['severity'])}"
        f"<a class='acctlink' href='/incident?id={int(r['id'])}'>{html.escape(r['rule'])} ›</a>"
        f"<span class='meta'>{html.escape(r['label'])} · {_ts(r['sent_at'])}</span>"
        f"{_repeat_note(r)}</div>"
        f"{_ack_cell(r, snoozed.get(r['dedup_key']), rules, int(r['id']) in marked)}"
        "</div>"
        for r in alerts
    ) or '<p class="empty">No alerts — all quiet.</p>'

    # Decision 16's landing half: no links in email, so the portal itself
    # must land the customer on what needs them. The newest unacknowledged
    # incident sits above the fold - opening the portal directly reaches it
    # in one step, and the email's "incident #N" reference matches this
    # block's number.
    incident_hero = ""
    if ids:
        marks2 = ",".join("?" for _ in ids)
        with db.cursor() as cur:
            cur.execute(
                f"SELECT al.id, al.rule, al.severity, al.sent_at, ac.label "
                f"FROM alerts al JOIN accounts ac ON ac.id = al.account_id "
                f"WHERE al.account_id IN ({marks2}) AND al.acknowledged_at IS NULL "
                f"ORDER BY al.sent_at DESC, al.id DESC LIMIT 1", ids)
            hero = cur.fetchone()
        if hero is not None:
            cls = "state--danger" if hero["severity"] in ("CRITICAL", "HIGH") else "state--warn"
            icon = _EYE_DANGER if cls == "state--danger" else _EYE_WARN
            incident_hero = (
                f'<div class="state {cls}" role="alert" style="margin-bottom:var(--space-5)">'
                f'<span class="state__eyebrow">{icon}Needs your attention — incident #{int(hero["id"])}</span>'
                f'<p class="state__answer">{_sev_badge(hero["severity"])} {html.escape(hero["rule"])} '
                f"on {html.escape(hero['label'])}, {_ts(hero['sent_at'])} — not yet acknowledged.</p>"
                f'<p class="state__detail"><a class="acctlink" href="/incident?id={int(hero["id"])}">'
                "Open the incident and acknowledge it ›</a></p></div>"
            )

    body = (
        incident_hero + pause_banner + banner
        + '<section aria-labelledby="accounts-title"><div class="page-head">'
        '<h1 id="accounts-title">My accounts</h1>'
        f'<p class="muted">{len(accounts)} exchange account'
        f'{"s" if len(accounts) != 1 else ""} connected. Read-only keys only.</p></div>'
        '<div class="primary-row no-print">'
        '<a href="/status" class="btn btn--primary">Am I being watched?</a></div>'
        '<div style="height:var(--space-5)"></div>'
        f'<div>{account_cards}</div></section>'
        '<section class="panel" aria-labelledby="alerts-title">'
        '<h2 id="alerts-title">Your recent alerts</h2>'
        '<p class="count">latest 30 · <a href="/alerts">full history ›</a></p>'
        f"{alert_items}</section>"
    )
    return _page(body, email, "/")


def _render_member_account(db: Database, email: str, account_id: int, rules=None) -> str | None:
    """Account detail, only if this account belongs to the member. Returns
    None (rendered as not-found) otherwise - existence is never revealed."""
    rows = {r["id"]: r for r in member_accounts(db, email)}
    acct = rows.get(account_id)
    if acct is None:
        return None

    with db.cursor() as cur:
        cur.execute(
            "SELECT event_type, severity, created_at FROM events "
            "WHERE account_id = ? ORDER BY created_at DESC LIMIT 20",
            (account_id,),
        )
        events = cur.fetchall()
        cur.execute(
            "SELECT id, sent_at, severity, rule, acknowledged_at, acknowledged_by, dedup_key "
            "FROM alerts "
            "WHERE account_id = ? ORDER BY sent_at DESC LIMIT 20",
            (account_id,),
        )
        alerts = cur.fetchall()
    snoozed = active_snoozes_for_accounts(db, [account_id])
    marked = marked_alert_ids(db, [account_id])

    last_ok = acct["last_success_at"]
    now = datetime.now(timezone.utc)
    fresh = False
    if last_ok:
        try:
            fresh = (now - datetime.fromisoformat(last_ok)).total_seconds() < 300
        except ValueError:
            fresh = False
    def _tick(kind: str) -> str:
        path = ('M8 12.5l2.5 2.5L16 9' if kind == 'yes' else 'M9 9l6 6M15 9l-6 6')
        return (f'<svg class="tick tick--{kind}" viewBox="0 0 24 24" fill="none" aria-hidden="true">'
                '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>'
                f'<path d="{path}" stroke="currentColor" stroke-width="2" '
                'stroke-linecap="round" stroke-linejoin="round"/></svg>')

    if fresh and acct["status"] == "active":
        live_check = (
            "<li>" + _tick("yes") + "<div><strong>Monitoring live.</strong>"
            f"<span>Polled continuously for fund movement and config drift — last check {_ts(last_ok)}.</span></div></li>"
        )
    else:
        live_check = (
            "<li>" + _tick("no") + "<div><strong>This account is NOT being watched.</strong>"
            "<span>Movements on it are not currently being detected. Minas has been alerted and is "
            "restoring access; if we asked you for a new API key, that is what this is.</span></div></li>"
        )
    # The plain summary #9 asks for, stated as the outcome of verification
    # rather than a promise: the key was proven read-only at connection (a
    # key that could do more is refused, never accepted with a warning), and
    # the permission re-check timestamp comes from the snapshots the slow
    # loop actually takes - "not re-checked yet" is said plainly when true.
    with db.cursor() as cur:
        cur.execute(
            "SELECT snapshot_at FROM permission_snapshots WHERE account_id = ? "
            "ORDER BY snapshot_at DESC LIMIT 1", (account_id,))
        perm_row = cur.fetchone()
    perm_checked = (
        f"last re-checked {_ts(perm_row['snapshot_at'])}" if perm_row
        else "not re-checked since connection yet"
    )
    posture = (
        '<section class="panel" aria-labelledby="posture-title"><h2 id="posture-title">Security posture</h2>'
        '<ul class="feature-list">'
        "<li>" + _tick("yes") + "<div><strong>This key can read balances and history. "
        "It cannot trade or withdraw.</strong>"
        "<span>Verified when the key was connected — a key that could trade or withdraw "
        f"would have been refused, not accepted with a warning. Its permissions are "
        f"re-checked as we poll ({perm_checked}), and any change raises an alert.</span></div></li>"
        + live_check +
        "<li>" + _tick("yes") + "<div><strong>Credentials encrypted.</strong>"
        "<span>Your API key is encrypted at rest and never displayed. "
        f"It was last used {_ts(last_ok, 'never — no check has completed yet')}; "
        "every account check uses it, so this moves whenever monitoring "
        "does.</span></div></li>"
        # The revocation that matters happens at the EXCHANGE (#45): a
        # read-only key cannot delete itself, and Minas offering a "revoke"
        # button here would be theatre in front of the real kill switch the
        # customer already holds. Saying where the switch is IS the feature.
        "<li>" + _tick("yes") + "<div><strong>You hold the kill switch.</strong>"
        f"<span>Revoke this key in your {html.escape((acct['exchange'] or 'exchange').title())} "
        "app at any moment — Minas cannot stop you, slow you down, or use the key for "
        "anything but reading. Revoking ends monitoring until a new key is "
        "connected.</span></div></li>"
        # Offered on every account rather than only on unwatched ones. The
        # customer who needs it most has just revoked every key in a panic, and
        # asking them to first prove the account looks broken - on a page whose
        # freshness check may not have caught up yet - is a hurdle at the worst
        # possible moment (#69).
        "<li>" + _tick("yes") + "<div><strong>Revoked our key?</strong>"
        f'<span><a class="acctlink" href="/key?id={int(account_id)}">Replace the monitoring key ›</a> '
        "and monitoring restarts within a minute.</span></div></li>"
        "</ul></section>"
    )

    event_items = "".join(
        '<div class="alert-item"><div class="alert-item__head">'
        f"{_sev_badge(r['severity'])}"
        f"<span>{html.escape(r['event_type'])}</span>"
        f"<span class='meta'>{_ts(r['created_at'])}</span></div></div>"
        for r in events
    ) or '<p class="empty">No events — all quiet.</p>'
    alert_items = "".join(
        '<div class="alert-item"><div class="alert-item__head">'
        f"{_sev_badge(r['severity'])}"
        f"<a class='acctlink' href='/incident?id={int(r['id'])}'>{html.escape(r['rule'])} ›</a>"
        f"<span class='meta'>{_ts(r['sent_at'])}</span></div>"
        f"{_ack_cell(r, snoozed.get(r['dedup_key']), rules, int(r['id']) in marked)}"
        "</div>"
        for r in alerts
    ) or '<p class="empty">No alerts — all quiet.</p>'

    body = (
        '<article class="acct"><div class="acct__head"><div>'
        f'<p class="acct__label">{html.escape(acct["label"])}</p>'
        f'<p class="acct__exchange">{html.escape((acct["exchange"] or "bitget").title())}'
        f" · {_account_type_label(acct['topology'])}</p>"
        f"</div>{_status_pill(acct['status'])}</div>"
        '<dl class="kv">'
        f'<div class="kv__row"><dt>Monitored since</dt><dd>{_ts(acct["onboarded_at"])}</dd></div>'
        f'<div class="kv__row"><dt>Last successful check</dt><dd>{_ts(last_ok, "Never")}</dd></div>'
        "</dl></article>"
        + posture
        + _pause_section(db, int(account_id), acct["status"]) +
        '<section class="panel" aria-labelledby="events-title"><h2 id="events-title">Events</h2>'
        f'<p class="count">latest 20</p>{event_items}</section>'
        '<section class="panel" aria-labelledby="acct-alerts-title"><h2 id="acct-alerts-title">Alerts</h2>'
        f'<p class="count">latest 20</p>{alert_items}</section>'
    )
    return _page(body, email, "/")


# --- account activity (G-32, #36) --------------------------------------------

def account_activity(db: Database, account_ids: list[int], limit: int = 200):
    """Administrative changes on these accounts, newest first.

    Filtered to `ADMIN_EVENT_TYPES` rather than showing the whole event log:
    this page answers "has anybody changed how my account is watched or
    reached", and a customer scrolling past ten thousand detector rows to find
    one contact change is a customer who will not find it.

    Read-only, like everything in this module. The record's integrity is a
    property of the writer - `events` is append-only and the application
    issues no UPDATE or DELETE against it - not something a viewer enforces."""
    if not account_ids:
        return []
    from sentinel.events import ADMIN_EVENT_TYPES

    account_marks = ",".join("?" for _ in account_ids)
    type_marks = ",".join("?" for _ in ADMIN_EVENT_TYPES)
    with db.cursor() as cur:
        cur.execute(
            f"SELECT e.id, e.account_id, e.event_type, e.payload_json, e.actor, e.created_at, "
            f"ac.label FROM events e JOIN accounts ac ON ac.id = e.account_id "
            f"WHERE e.account_id IN ({account_marks}) AND e.event_type IN ({type_marks}) "
            f"ORDER BY e.created_at DESC, e.id DESC LIMIT ?",
            [*account_ids, *ADMIN_EVENT_TYPES, limit],
        )
        return cur.fetchall()


def _activity_detail(event_type: str, payload: dict) -> str:
    """What actually changed, in words, from the recorded payload.

    Every branch reads a value the row carries. Nothing here infers or
    reconstructs: this page is the one a customer reads when deciding whether
    somebody else has their account, and a plausible sentence the record does
    not support is worse than a blank cell."""
    def shown(value) -> str:
        return html.escape(str(value)) if value not in (None, "") else "none"

    if event_type == "alert_contact_updated":
        parts = []
        if payload.get("email_from") != payload.get("email_to"):
            parts.append(f"email {shown(payload.get('email_from'))} → "
                         f"{shown(payload.get('email_to'))}")
        if payload.get("phone_from") != payload.get("phone_to"):
            parts.append(f"phone {shown(payload.get('phone_from'))} → "
                         f"{shown(payload.get('phone_to'))}")
        return "; ".join(parts)
    if event_type == "sms_alerts_changed":
        return f"SMS alerts {'on' if payload.get('to') else 'off'}"
    if event_type == "member_portal_password_generated":
        return f"issued for {shown(payload.get('email'))}"
    if event_type in ("member_password_changed", "member_two_factor_enabled",
                      "member_two_factor_disabled"):
        return f"login {shown(payload.get('login'))}"
    if event_type == "customer_added":
        return f"{shown(payload.get('exchange'))} · {shown(payload.get('topology'))}"
    return ""


def _stale_after_seconds(rules=None) -> int:
    """How long a silent account may go before this page stops calling it
    watched: the same window the heartbeat detector alarms on.

    Derived, never a literal. The number here has to mean the same thing as
    the number `detect_unreachable` fires on, or the page and the alert
    disagree about whether an account is being watched — and the page is the
    one the customer believes. `_watch_threshold` exists in the dashboard for
    exactly this reason; this is the portal's side of it.
    """
    threshold = _watch_threshold(rules)
    interval = (
        rules.get_int("polling", "fast_loop_seconds") if rules is not None
        else int(default_for("polling", "fast_loop_seconds") or 45)
    )
    return threshold * interval


_STATUS_REASON = {
    "tamper_suspected": (
        "flagged for suspected interference with our monitoring key, and checks are "
        "paused while Minas investigates — Minas has been alerted"
    ),
    "disabled": "monitoring is switched off for this account",
}


def member_status(db: Database, email: str, rules=None, now: datetime | None = None) -> dict:
    """What this member's monitoring is actually doing, as data.

    Split from the rendering so the judgement can be tested without a server,
    and so the page cannot quietly say something the data does not support.

    Returns `{overall, accounts, problems, checked_at}` where `overall` is one
    of `watching` / `degraded` / `not_watching`:

      `watching`      every account polled inside the heartbeat window
      `degraded`      some accounts are watched and some are not
      `not_watching`  none of this member's accounts are being watched

    A member with no accounts is `not_watching`: nothing is being watched,
    which is the truthful answer and not an error state.
    """
    now = now or datetime.now(timezone.utc)
    window = _stale_after_seconds(rules)
    rows = member_accounts(db, email)

    accounts, problems = [], []
    watched = 0
    for r in rows:
        last_ok = r["last_success_at"]
        age = None
        if last_ok:
            try:
                age = (now - datetime.fromisoformat(last_ok)).total_seconds()
            except ValueError:
                age = None

        label = str(r["label"])
        fresh = age is not None and age < window and r["status"] == "active"
        auth_failing = (r["consecutive_auth_failures"] or 0) > 0

        # Ordered by what the customer can do about it, with one rule above
        # that (#83): CURRENT STATE BEATS STALE COUNTERS. Only `active`
        # accounts are polled, so any non-active status freezes the
        # poll_state counters at whatever they held when polling stopped —
        # `status` is a fact about now, `consecutive_auth_failures` is a
        # fact about whenever we last looked, and they disagree exactly when
        # an account is disabled or flagged mid-key-failure. The status
        # check therefore comes first: "replace it and monitoring restarts"
        # is FALSE while non-active (only a status change restarts
        # anything), and for tamper_suspected the frozen counter is the very
        # one that raised the flag, so the key advice would win every time.
        # The next signal that freezes when polling stops belongs below the
        # status check too.
        if r["status"] != "active":
            # This account is not being watched - including
            # `tamper_suspected`, which is the alarming one: the account we
            # most suspect is being interfered with is the account we stop
            # looking at. Said in words rather than by printing the column
            # value, because "tamper_suspected" is our jargon and this page
            # is read by the customer.
            reason = _STATUS_REASON.get(
                r["status"], f"this account is {r['status']}, so it is not being checked"
            )
        elif auth_failing:
            # A rejected key is the customer's to replace; an unreachable
            # exchange is not; and telling somebody to re-issue a working
            # key is wrong advice (G-6).
            reason = "our monitoring key is being rejected — replace it and monitoring restarts"
        elif last_ok is None:
            reason = "this account has never completed a check"
        elif not fresh:
            reason = "no successful check recently — the exchange may be unreachable"
        else:
            reason = None

        if fresh and not auth_failing:
            watched += 1
        else:
            problems.append({"account_id": int(r["id"]), "label": label, "reason": reason})

        accounts.append({
            "account_id": int(r["id"]),
            "label": label,
            "last_success_at": last_ok,
            "watched": bool(fresh and not auth_failing),
            "reason": reason,
            # The raw column, so a consumer can tell a deliberate pause
            # (disabled) from trouble - the home screen's tri-state (#34/#10)
            # needs the distinction and must not re-derive it.
            "status": str(r["status"]),
        })

    if rows and watched == len(rows):
        overall = "watching"
    elif watched:
        overall = "degraded"
    else:
        overall = "not_watching"

    return {
        "overall": overall,
        "accounts": accounts,
        "problems": problems,
        "checked_at": now.isoformat(),
        "stale_after_seconds": window,
    }


# The three states of the one question this page answers, in the mockup's
# structure (design/portal/status.html): an eyebrow with an icon, a
# one-sentence answer in display type, one clarifying line.
_EYE_OK = ('<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
           '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>'
           '<path d="M8 12.5l2.5 2.5L16 9" stroke="currentColor" stroke-width="2" '
           'stroke-linecap="round" stroke-linejoin="round"/></svg>')
_EYE_WARN = ('<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
             '<path d="M12 3l9 16H3L12 3z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>'
             '<path d="M12 10v5M12 17.5v.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>')
_EYE_DANGER = ('<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
               '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>'
               '<path d="M12 7v6M12 16.5v.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>')

_OVERALL_COPY = {
    "watching": ("state--ok", _EYE_OK, "Monitoring normal",
                 "You're being watched.",
                 "Every connected account was checked within the last few minutes."),
    "degraded": ("state--warn", _EYE_WARN, "Partially watched",
                 "Some of your accounts are not being watched.",
                 "Monitoring is running, but not every account is being checked. "
                 "The ones affected are marked below."),
    "not_watching": ("state--danger", _EYE_DANGER, "Not being watched",
                     "Your accounts are NOT being watched.",
                     "Movements on them are not currently being detected. "
                     "Minas has been alerted; what each account needs is marked below."),
}


def _fmt_drill(d: dict | None, *, none: str) -> str:
    if d is None:
        return f'<p class="muted">{none}</p>'
    when = _ts(d["started_at"])
    if d["result"] == "passed":
        latency = f" · detection to delivery {d['latency_ms']} ms" if d.get("latency_ms") is not None else ""
        return (f'<p><span class="badge badge--ok">Passed</span> {when}{latency} · '
                f"reached {int(d['delivered_to'])} destination(s)</p>")
    return (f'<p><span class="badge badge--danger">Failed</span> {when} · '
            f"{html.escape(str(d.get('detail') or ''))} — Minas has been paged.</p>")


def _drill_section(db: Database, email: str, status: dict, notice: str | None = None) -> str:
    """Decision 2 (#37): the fleet's scheduled drill proves the machinery -
    shown here as the last drill, its result and its latency - and the
    member can ask for the proof on their own phone with one button.
    Customers never receive the scheduled drill; only the one they asked
    for."""
    from sentinel.drill import last_drill

    fleet = last_drill(db, kind="scheduled")
    mine = last_drill(db, kind="on_demand", account_ids=[a["account_id"] for a in status["accounts"]])
    banner = ""
    if notice:
        banner = ('<div class="notice notice--info" role="status">' + _ICON_INFO
                  + f'<div><p class="notice__title">{html.escape(notice)}</p></div></div>')
    button = ""
    if status["accounts"]:
        button = ('<form class="no-print" method="post" action="/drill" style="margin-top:var(--space-3)">'
                  '<button class="btn btn--secondary" type="submit">Test my alerts now</button>'
                  '<p class="hint">Sends a message labelled DRILL to your contacts through every channel '
                  "you have on - nothing else happens. Once an hour.</p></form>")
    return (
        '<section class="panel" aria-labelledby="drill-title"><h2 id="drill-title">Alert drills</h2>'
        + banner +
        '<p class="muted">Every week the Guardian injects a simulated theft on its own test account and '
        "watches it travel the whole path to a phone. That proves the machinery; the message you can "
        "send yourself proves the path to <em>you</em>.</p>"
        '<dl class="kv"><div class="kv__row"><dt>Last scheduled drill</dt><dd>'
        + _fmt_drill(fleet, none="No scheduled drill has run yet.")
        + '</dd></div><div class="kv__row"><dt>Your last test</dt><dd>'
        + _fmt_drill(mine, none="You have not run one yet.")
        + "</dd></div></dl>" + button + "</section>"
    )


def _delivery_section(db: Database, email: str) -> str:
    """The addition to decision 2: the drill proves the pipeline, not that
    THIS address still accepts mail. Last successful delivery per channel,
    from real traffic, with staleness said plainly."""
    from sentinel.drill import STALE_DELIVERY_DAYS, delivery_recency

    recency = delivery_recency(db, email)
    rows = ""
    for channel, label in (("email", "Email"), ("telegram", "Telegram"), ("sms", "SMS")):
        info = recency[channel]
        if info["last"]:
            value = _ts(info["last"])
            if info["stale"]:
                value += (f' <span class="badge badge--warn">over {STALE_DELIVERY_DAYS} days ago</span>')
        else:
            value = '<span class="badge badge--neutral">Never</span>'
        rows += f'<div class="kv__row"><dt>{label}</dt><dd>{value}</dd></div>'
    return (
        '<section class="panel" aria-labelledby="deliv-title"><h2 id="deliv-title">Last message that reached you</h2>'
        '<p class="muted">Derived from what was actually delivered to you - alerts, digests and tests - '
        "never from a drill on our test account. A channel that has not reached you in a while is "
        "worth testing with the button above.</p>"
        f'<dl class="kv">{rows}</dl></section>'
    )


def _render_status(db: Database, email: str, rules=None, drill_notice: str | None = None) -> str:
    """G-34 (#38): so the customer can trust the silence.

    The product's whole claim is that no news is good news. That claim is only
    worth anything if "no news" can be told apart from "nobody is watching",
    and until this page the customer had no way to tell — which is precisely
    the failure this codebase keeps closing on its own behalf, pointed at the
    person paying for it.

    What this page deliberately does NOT claim: that the Guardian is up. It is
    *served by* the Guardian, so if it renders, the process is alive — but a
    page that cannot load also cannot tell you why, and a dead host looks
    identical to a closed laptop. Saying so plainly is the honest version;
    external dead-man's-switch monitoring is #63, and until that exists this
    page is evidence and not an alarm.
    """
    status = member_status(db, email, rules)
    state_cls, eye_icon, eyebrow, answer, detail = _OVERALL_COPY[status["overall"]]
    generated = datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC")

    masthead = (
        '<div class="print-masthead">'
        '<p class="print-masthead__title">Minas AI Guardian member portal — Monitoring status report</p>'
        f'<p class="print-masthead__sub">Account: {html.escape(email)}</p>'
        f'<p class="print-masthead__sub">Generated: {generated}</p>'
        '<p class="print-masthead__sub">This report reflects the monitoring status at the '
        "time shown. It does not prove funds are safe and does not record transactions.</p></div>"
    )

    def _watch_badge(a) -> str:
        if a["watched"]:
            return ('<span class="badge badge--ok"><span class="dot" aria-hidden="true"></span>'
                    "Watched</span>")
        return ('<span class="badge badge--danger"><span class="dot" aria-hidden="true"></span>'
                "Not watched</span>")

    def _problem(a) -> str:
        if not a["reason"]:
            return ""
        fixable = "key is being rejected" in a["reason"]
        if fixable:
            fix = ('<p class="problem__fix"><span class="fix-tag">You can fix this</span>'
                   f'<a href="/key?id={int(a["account_id"])}" class="btn btn--secondary btn--sm">'
                   "Replace API key</a></p>")
        else:
            fix = ('<p class="problem__fix"><span class="fix-tag fix-tag--muted">'
                   "We're on it — contact the Minas team if it persists</span></p>")
        return ('<div class="problem"><p class="problem__title">'
                + html.escape(a["reason"]) + "</p>" + fix + "</div>")

    cards = "".join(
        '<article class="acct"><div class="acct__head"><div>'
        f'<p class="acct__label">{html.escape(a["label"])}</p></div>{_watch_badge(a)}</div>'
        '<dl class="kv"><div class="kv__row"><dt>Last successful check</dt>'
        f"<dd{'' if a['watched'] else ' class=muted'}>{_ts(a['last_success_at'], 'Never')}</dd>"
        "</div></dl>"
        f"{_problem(a)}</article>"
        for a in status["accounts"]
    ) or '<p class="empty">No accounts yet — contact the Minas team.</p>'

    body = (
        masthead
        + f'<div class="state {state_cls}" role="status">'
        f'<span class="state__eyebrow">{eye_icon}{eyebrow}</span>'
        f'<p class="state__answer">{answer}</p>'
        f'<p class="state__detail">{detail}</p></div>'
        '<div class="primary-row no-print">'
        '<a href="/status" class="btn btn--primary">Check again now</a>'
        '<a href="/" class="btn btn--tertiary">Manage accounts</a></div>'
        '<div style="height:var(--space-5)"></div>'
        + cards
        + _drill_section(db, email, status, notice=drill_notice)
        + _delivery_section(db, email) +
        '<div class="notice notice--info no-print" style="margin-top:var(--space-6)">' + _ICON_INFO
        + '<div><p class="notice__title">What this page cannot tell you.</p>'
        '<p class="notice__body">This page is served by the Guardian itself, so if it loaded, '
        "the Guardian is running. It cannot tell you the opposite: if the Guardian were "
        "completely down this page would not load at all, and that looks the same as any other "
        "connection problem. Treat it as evidence that monitoring is working, not as the alarm "
        "that would tell you it stopped.</p></div></div>"
    )
    return _page(body, email, "/status", title="Am I being watched? — Minas AI Guardian")




def retention_summary(rules=None) -> list[tuple[str, str]]:
    """What we keep and for how long, in customer terms, from the live config.

    Derived from the same settings `prune_all` enforces (G-43, #47) - via
    `RulesConfig`, which falls back to the declared defaults, so the page and
    the pruner cannot disagree. The grouping is by what the customer would
    call the data, not by table name; the mapping is pinned by a test against
    `sentinel.retention.PRUNABLE` so a new table cannot be silently absent
    from the page.
    """
    def days(setting: str) -> int:
        if rules is not None:
            return rules.get_int("retention", setting)
        return int(default_for("retention", setting) or 0)

    def phrase(n: int) -> str:
        if n <= 0:
            return "kept indefinitely"
        if n % 365 == 0:
            years = n // 365
            return f"{years} year{'s' if years != 1 else ''}"
        return f"{n} days"

    return [
        ("Activity records and alert history",
         phrase(max(days("events_days"), days("alerts_days")))
         + " — the forensic log an incident review is built from"),
        ("Balance snapshots",
         phrase(days("balance_snapshots_days"))
         + " — only \"what changed since the last check\" needs these; the latest is always kept"),
        ("Duplicate-detection records",
         phrase(days("dedup_days"))
         + " — bounded below by correctness: shorter, and an old movement could alert twice"),
        ("Fire-drill results",
         phrase(days("drills_days"))
         + " — the record that the alert path was tested, and whether it reached anyone"),
        ("Your API key (encrypted) and contact details",
         "until your account is deleted"),
        ("When you ask for your data to be deleted",
         "deleted immediately, from the security page or by asking Minas — what remains is one entry "
         "stating that a deletion happened, with the date and the number of records removed"),
        ("Baselines of your normal activity (addresses, pairs, routes)",
         "kept while your account is monitored — deleting one would make your own history look like an attack"),
    ]


def _render_limits(db: Database, email: str, rules=None) -> str:
    """G-44 (#48): what the Guardian can and cannot do, said plainly.

    The product detects and tells you. It cannot stop anything. A customer who
    believes otherwise will not act on an alert with the urgency it needs,
    because they will assume something is already being done - and the whole
    value of the alert is that they act on it.

    So this page exists to be *unflattering*, and the honesty is load-bearing
    rather than decorative. The read-only key is the clearest case: it is a
    real safety property worth stating, and its cost is precisely the
    limitation on this page. Stating only the first half would be marketing.

    Numbers come from the running config, never typed in. A page that promises
    a response time the poller does not keep is worse than a page that promises
    nothing.
    """
    fast = (
        rules.get_int("polling", "fast_loop_seconds") if rules is not None
        else int(default_for("polling", "fast_loop_seconds") or 45)
    )
    slow = (
        rules.get_int("polling", "slow_loop_seconds") if rules is not None
        else int(default_for("polling", "slow_loop_seconds") or 600)
    )

    def _feat(kind, title, text):
        path = ("M8 12.5l2.5 2.5L16 9" if kind == "yes" else "M9 9l6 6M15 9l-6 6")
        return (f'<li><svg class="tick tick--{kind}" viewBox="0 0 24 24" fill="none" aria-hidden="true">'
                '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>'
                f'<path d="{path}" stroke="currentColor" stroke-width="2" '
                'stroke-linecap="round" stroke-linejoin="round"/></svg>'
                f"<div><strong>{title}</strong><span>{text}</span></div></li>")

    does = "".join([
        _feat("yes", "It watches for money leaving.",
              "Withdrawals, transfers, and balance drops that no trade explains."),
        _feat("yes", "It watches your account's security settings.",
              "API permissions and IP allowlists changing are how a takeover starts."),
        _feat("yes", "It tells you, on the channels you have configured.",
              "And tells a second contact, and escalates if nobody acknowledges."),
        _feat("yes", "It keeps the record.",
              "Every alert and every check is stored, so you can reconstruct what "
              "happened and when afterwards."),
    ])

    cannot = "".join([
        _feat("no", "It cannot see what the exchange does not report.",
              "Every check reads the exchange's own API. If the exchange is slow "
              "to show a withdrawal, omits a reference, or is unreachable, the "
              "Guardian is that much later or that much blinder. It says so "
              'rather than guessing — see <a class="acctlink" href="/status">Am I being watched?</a>.'),
        _feat("no", "It cannot recover money that has gone.",
              "There is no reversal. The Guardian shortens the time between a "
              "theft starting and you knowing about it. That is the whole product."),
    ])

    keeps = "".join(
        f'<div class="kv__row"><dt>{html.escape(what)}</dt>'
        f"<dd class=\"muted\">{html.escape(how_long)}</dd></div>"
        for what, how_long in retention_summary(rules)
    )

    body = (
        '<div class="page-head"><span class="eyebrow">Read this first</span>'
        "<h1>What the Guardian can and cannot do</h1>"
        '<p class="lead">A monitoring product, not a brake. Knowing the difference changes '
        "how fast you act when an alert arrives.</p></div>"
        '<div class="callout-danger" role="note">'
        "<h2>Minas AI Guardian cannot stop an attack.</h2>"
        "<p>It cannot freeze your account, cancel a transaction, or move funds. When you get "
        "an alert, you and the exchange are the ones who can act — and seconds decide the "
        "outcome.</p></div>"
        '<section class="panel" aria-labelledby="does-title"><h2 id="does-title">What the Guardian does</h2>'
        f'<ul class="feature-list">{does}</ul></section>'
        '<section class="panel" aria-labelledby="cannot-title"><h2 id="cannot-title">What it cannot do</h2>'
        f'<ul class="feature-list">{cannot}</ul></section>'
        '<section class="panel" aria-labelledby="why-title"><h2 id="why-title">Why it is built this way</h2>'
        '<div class="stack">'
        "<p><strong>The key we hold is read-only, on purpose.</strong> It cannot trade, "
        "withdraw, or move anything. That means a compromise of Minas itself still cannot "
        "cost you money — we are not a way in to your funds. <strong>The cost of that "
        "guarantee is this page:</strong> a key that cannot move your money also cannot be "
        "used to rescue it. We chose the side that cannot hurt you.</p></div></section>"
        '<section class="panel" aria-labelledby="timing-title"><h2 id="timing-title">Honest timing</h2>'
        '<div class="stack">'
        f"<p>Your balances and recent movements are checked about every <strong>{fast} "
        f"seconds</strong>; permissions and allowlists about every <strong>{slow // 60} "
        "minutes</strong>. An alert is sent as soon as a check finds something, so in normal "
        "conditions you hear within a minute or so of the exchange showing it. That is "
        "measured from when the <em>exchange</em> reports it, not from when it happened — we "
        "cannot be faster than the source we read.</p></div></section>"
        '<section class="panel" aria-labelledby="keeps-title"><h2 id="keeps-title">What we keep, and for how long</h2>'
        '<p class="count">read from the running configuration</p>'
        f'<dl class="kv">{keeps}</dl>'
        '<div class="notice notice--info" style="margin-top:var(--space-4)">' + _ICON_INFO
        + '<div><p class="notice__title">These periods are our current practice, not yet '
        "reviewed policy.</p>"
        '<p class="notice__body">They are enforced automatically on the schedule shown, but '
        "a legal review of retention obligations is still pending — if it changes these "
        "numbers, this page changes with them, because it reads the same configuration the "
        "deletion job runs on.</p></div></div></section>"
        + _sensitivity_section(db, email, rules)
    )
    return _page(body, email, "/limits", title="Limits — Minas AI Guardian")


def _sensitivity_section(db: Database, email: str, rules=None) -> str:
    """#17's third criterion: the member sees their EFFECTIVE sensitivity in
    plain terms — the same numbers detection actually runs on, read through
    the same override layer, so this page and the alerts cannot disagree.
    A value set specifically for the account says so; everything else says
    'standard'."""
    from sentinel.overrides import overrides_for

    accounts = member_accounts(db, email)
    if not accounts:
        return ""

    def _effective(overrides: dict, setting: str):
        raw = overrides.get(setting)
        if raw is not None:
            return raw, True
        segments = setting.split(".")
        if rules is not None:
            return rules.get(*segments, default=default_for(*segments)), False
        return default_for(*segments), False

    ROWS = [
        ("Several withdrawals in a short time",
         "detectors.withdrawal_burst.min_count",
         lambda v, o: f"{v} or more within {_effective(o, 'detectors.withdrawal_burst.window_minutes')[0]} minutes"),
        ("A trade far from the market price",
         "detectors.off_market.max_deviation_pct",
         lambda v, o: f"more than {v}% away"),
        ("Unusual trading-fee spend",
         "detectors.fee_anomaly.multiplier",
         lambda v, o: f"{v}× your usual level"),
        ("A balance drop no trade explains",
         "detectors.quantity_drop.tolerance_pct",
         lambda v, o: f"beyond {v}% tolerance"),
        ("Contact with the account lost",
         "detectors.heartbeat.consecutive_failures_threshold",
         lambda v, o: f"after {v} failed checks in a row"),
    ]

    cards = ""
    for acct in accounts:
        overrides = overrides_for(db, int(acct["id"]))
        rows_html = ""
        for title, setting, phrase in ROWS:
            value, custom = _effective(overrides, setting)
            chip = ('<span class="badge badge--neutral"><span class="dot" aria-hidden="true"></span>'
                    "set for this account</span>") if custom else (
                    '<span class="meta">standard</span>')
            rows_html += (f'<div class="kv__row"><dt>{html.escape(title)}</dt>'
                          f"<dd>{html.escape(phrase(value, overrides))} {chip}</dd></div>")
        cards += (
            f'<article class="acct"><div class="acct__head"><div>'
            f'<p class="acct__label">{html.escape(acct["label"])}</p></div></div>'
            f'<dl class="kv">{rows_html}</dl></article>'
        )
    return (
        '<section class="panel" aria-labelledby="sens-title"><h2 id="sens-title">Your alert sensitivity</h2>'
        '<p class="muted">The thresholds your accounts are actually judged '
        "against — read from the running configuration, including anything set "
        "specifically for you. Ask the Minas team to tune these if your trading "
        "style makes an alert too eager or too quiet.</p>"
        + cards + "</section>"
    )




def _render_activity(db: Database, email: str) -> str:
    """Who has touched this member's accounts, and when (G-32).

    An empty page says the record is empty, not that nothing happened -
    those are the same sentence to a reader and very different facts, and this
    product has already shipped one bug where an absence was rendered as
    reassurance."""
    from sentinel.events import ADMIN_EVENT_LABELS

    accounts = member_accounts(db, email)
    rows = account_activity(db, [r["id"] for r in accounts])

    def _actor(actor) -> str:
        if actor:
            return html.escape(actor)
        # An unattributed row is a finding, not a blank. The same reasoning as
        # the `actor` column on config_changes: a change nobody can be named
        # for is exactly what somebody reading this page is looking for.
        return '<span class="ack-no">not attributed</span>'

    items = "".join(
        '<div class="alert-item"><div class="alert-item__head">'
        f"<strong>{html.escape(ADMIN_EVENT_LABELS.get(r['event_type'], r['event_type']))}</strong>"
        f"<span class='meta'>{html.escape(r['label'])} · {_ts(r['created_at'])}</span></div>"
        '<dl class="kv">'
        f'<div class="kv__row"><dt>Detail</dt><dd class="muted">'
        f"{_activity_detail(r['event_type'], _payload(r['payload_json']))}</dd></div>"
        f'<div class="kv__row"><dt>Who</dt><dd>{_actor(r["actor"])}</dd></div>'
        "</dl></div>"
        for r in rows
    ) or ('<p class="empty">Nothing recorded — no changes to your accounts or your '
          "login have been made since monitoring began.</p>")

    body = (
        '<div class="page-head"><h1>Who has touched your accounts</h1>'
        f'<p class="muted">{len(rows)} change(s). Every change to how your accounts are '
        "watched or reached, including the ones Minas makes. Detections are not listed here "
        '— they are on <a href="/alerts">your alert history</a>. This record is append-only: '
        "entries are never edited or removed, by us or by anybody else.</p></div>"
        f'<section class="panel">{items}</section>'
    )
    return _page(body, email, "/activity", title="Activity — Minas AI Guardian")


def _payload(payload_json: str | None) -> dict:
    try:
        loaded = json.loads(payload_json or "{}")
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


# --- alert history (G-31, #35) -----------------------------------------------

_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
_HISTORY_PAGE_SIZE = 200


def alert_history_rows(db: Database, account_ids: list[int], *,
                       account: int | None = None, severity: str | None = None,
                       date_from: str | None = None, date_to: str | None = None,
                       limit: int | None = None, offset: int = 0):
    """Alerts on this member's accounts, newest first, optionally narrowed.
    Returns (rows, total): one page and how many matched in all.

    The member scope is the IN(...) list, and every filter narrows WITHIN it:
    an `account` value that is not on the list intersects to nothing rather
    than being ignored. Ignoring an unrecognised filter would show everything
    under a heading that claims a selection - the same quiet inversion this
    project keeps finding elsewhere, done to a forensic record.

    Dates arrive as YYYY-MM-DD. `date(?, '+1 day')` makes the "to" day
    inclusive; on garbage it yields NULL and the comparison matches nothing.
    Same rule throughout: a filter that cannot be understood selects nothing,
    never everything."""
    if not account_ids:
        return [], 0
    marks = ",".join("?" for _ in account_ids)
    where = [f"al.account_id IN ({marks})"]
    params: list = list(account_ids)
    if account is not None:
        where.append("al.account_id = ?")
        params.append(account)
    if severity:
        where.append("al.severity = ?")
        params.append(severity)
    if date_from:
        where.append("al.sent_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("al.sent_at < date(?, '+1 day')")
        params.append(date_to)
    clause = " AND ".join(where)
    with db.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM alerts al WHERE {clause}", params)
        total = cur.fetchone()["n"]
        sql = (
            "SELECT al.id, al.sent_at, al.severity, al.rule, al.message, al.confidence, "
            "al.delivered_to, al.acknowledged_at, al.acknowledged_by, "
            "al.repeat_count, al.last_repeat_at, ac.label "
            "FROM alerts al JOIN accounts ac ON ac.id = al.account_id "
            # id breaks ties: two alerts in the same second must not swap
            # places between the page and the export.
            f"WHERE {clause} ORDER BY al.sent_at DESC, al.id DESC"
        )
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params = [*params, limit, offset]
        cur.execute(sql, params)
        rows = cur.fetchall()
    return rows, total


def _history_filters(qs) -> dict:
    """The submitted filter values, cleaned but not second-guessed."""
    def one(key: str) -> str:
        return (qs.get(key) or [""])[0].strip()

    account: int | None = None
    if one("account"):
        try:
            account = int(one("account"))
        except ValueError:
            # No account has id -1, so an unparseable id selects nothing -
            # the dates rule, applied here by hand.
            account = -1
    return {
        "account": account,
        "severity": one("severity"),
        "from": one("from"),
        "to": one("to"),
    }


def _history_query_string(f: dict) -> str:
    """The current filters as a query string, for the CSV link and paging."""
    pairs = [(k, v) for k, v in (
        ("account", f["account"] if f["account"] is not None else ""),
        ("severity", f["severity"]), ("from", f["from"]), ("to", f["to"]),
    ) if v not in ("", None)]
    return urlencode(pairs)


def _render_alert_history(db: Database, email: str, qs) -> str:
    """The searchable history (G-31): every alert, filters, incident links.

    A record, not a control surface: acknowledgement is shown as fact but the
    buttons live on the pages where the alert is being dealt with. This page
    answers "what has happened on my accounts", and the incident link is one
    click away for anything that needs acting on."""
    accounts = member_accounts(db, email)
    ids = [r["id"] for r in accounts]
    f = _history_filters(qs)
    try:
        page = max(1, int((qs.get("page") or ["1"])[0]))
    except ValueError:
        page = 1
    rows, total = alert_history_rows(
        db, ids, account=f["account"], severity=f["severity"],
        date_from=f["from"], date_to=f["to"],
        limit=_HISTORY_PAGE_SIZE, offset=(page - 1) * _HISTORY_PAGE_SIZE,
    )

    account_options = '<option value="">All accounts</option>' + "".join(
        f'<option value="{r["id"]}"{" selected" if f["account"] == r["id"] else ""}>'
        f'{html.escape(r["label"])}</option>'
        for r in accounts
    )
    severity_options = '<option value="">All severities</option>' + "".join(
        f'<option value="{s}"{" selected" if f["severity"] == s else ""}>{s}</option>'
        for s in _SEVERITIES
    )
    query = _history_query_string(f)
    filter_form = (
        '<form method="get" action="/alerts" class="ctl-row" style="align-items:flex-end">'
        '<div class="field"><label for="f-account">Account</label>'
        f'<select class="input input--inline" id="f-account" name="account">{account_options}</select></div>'
        '<div class="field"><label for="f-severity">Severity</label>'
        f'<select class="input input--inline" id="f-severity" name="severity">{severity_options}</select></div>'
        '<div class="field"><label for="f-from">From</label>'
        f'<input class="input input--inline" type="date" id="f-from" name="from" value="{html.escape(f["from"])}"></div>'
        '<div class="field"><label for="f-to">To</label>'
        f'<input class="input input--inline" type="date" id="f-to" name="to" value="{html.escape(f["to"])}"></div>'
        '<button class="btn btn--secondary" type="submit">Filter</button>'
        f'<a href="/alerts.csv{"?" + query if query else ""}">Download CSV ↓</a>'
        "</form>"
    )

    def _ack_text(r) -> str:
        if r["acknowledged_at"]:
            who = html.escape(r["acknowledged_by"] or "—")
            return f"<span class='ack-yes'>✓ {who} · {_ts(r['acknowledged_at'])}</span>"
        return "<span class='ack-no'>not acknowledged</span>"

    alert_rows = "".join(
        '<div class="alert-item"><div class="alert-item__head">'
        f"{_sev_badge(r['severity'])}"
        f"<a class='acctlink' href='/incident?id={int(r['id'])}'>{html.escape(r['rule'])} ›</a>"
        f"<span class='meta'>{html.escape(r['label'])} · {_ts(r['sent_at'])}</span>"
        f"{_repeat_note(r)}"
        f"{_ack_text(r)}</div></div>"
        for r in rows
    ) or '<p class="empty">No alerts match.</p>' 

    def _page_link(p: int, label: str) -> str:
        amp = "&" if query else ""
        return f"<a class='acctlink' href='/alerts?{query}{amp}page={p}'>{label}</a>"

    pager = ""
    if total > _HISTORY_PAGE_SIZE:
        parts = []
        if page > 1:
            parts.append(_page_link(page - 1, "‹ Newer"))
        if page * _HISTORY_PAGE_SIZE < total:
            parts.append(_page_link(page + 1, "Older ›"))
        pager = "<p class='meta' style='margin-top:var(--space-3)'>" + " · ".join(parts) + "</p>"

    body = (
        '<div class="page-head"><h1>Alert history</h1>'
        f'<p class="muted">{total} alert(s). Acknowledgement is shown here as fact; the '
        "buttons live on each alert's own page.</p></div>"
        f'<section class="panel">{filter_form}</section>'
        f'<section class="panel">{alert_rows}{pager}</section>'
    )
    return _page(body, email, "/alerts", title="Alert history — Minas AI Guardian")


def _csv_defang(value) -> str:
    """A cell that cannot become a formula.

    A spreadsheet treats a leading =, +, -, @ or tab as code, and this export
    exists to be opened in one - by exactly the person an =HYPERLINK() in an
    attacker-influenced field would be aimed at. The quote prefix costs one
    character of cosmetics on those cells; leaving it off costs whatever the
    formula does on the customer's machine."""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t"):
        return "'" + text
    return text


def _alert_history_csv(db: Database, email: str, qs) -> bytes:
    """The same history as /alerts, honouring the same filters, every matching
    row - the export is not paginated, because half a spreadsheet is worse
    than none."""
    ids = [r["id"] for r in member_accounts(db, email)]
    f = _history_filters(qs)
    rows, _total = alert_history_rows(
        db, ids, account=f["account"], severity=f["severity"],
        date_from=f["from"], date_to=f["to"],
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "alert_id", "time_utc", "account", "severity", "rule", "what_we_saw",
        "confidence", "channels_delivered", "acknowledged_at", "acknowledged_by",
    ])
    for r in rows:
        writer.writerow([
            r["id"], r["sent_at"], _csv_defang(r["label"]), r["severity"],
            _csv_defang(r["rule"]), _csv_defang(r["message"]),
            _csv_defang(r["confidence"]), r["delivered_to"],
            r["acknowledged_at"] or "", _csv_defang(r["acknowledged_by"]),
        ])
    # utf-8-sig: the BOM is what makes Excel read UTF-8 as UTF-8 instead of
    # mojibake-ing every non-ASCII account label.
    return buf.getvalue().encode("utf-8-sig")


def _incident_balances(db: Database, account_id: int, sent_at: str) -> tuple[list, list]:
    """The two balance snapshots either side of the alert.

    Deliberately not "the current balance". The reader of this page is
    reconstructing a past state, usually for somebody else - an exchange, an
    insurer, the police - and today's number in that document is actively
    misleading."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT coin, quantity, snapshot_at FROM balance_snapshots "
            "WHERE account_id = ? AND snapshot_at <= ? "
            "AND snapshot_at = (SELECT MAX(snapshot_at) FROM balance_snapshots "
            "                   WHERE account_id = ? AND snapshot_at <= ?) ORDER BY coin",
            (account_id, sent_at, account_id, sent_at),
        )
        before = cur.fetchall()
        cur.execute(
            "SELECT coin, quantity, snapshot_at FROM balance_snapshots "
            "WHERE account_id = ? AND snapshot_at > ? "
            "AND snapshot_at = (SELECT MIN(snapshot_at) FROM balance_snapshots "
            "                   WHERE account_id = ? AND snapshot_at > ?) ORDER BY coin",
            (account_id, sent_at, account_id, sent_at),
        )
        after = cur.fetchall()
    return list(before), list(after)


def _render_incident(db: Database, email: str, alert_id: int) -> str | None:
    """One incident, as a document (G-25, #29).

    This is the most detailed view of an account in the product - balances,
    destination addresses, exact timings - and it exists to be handed to
    somebody else. So it is scoped through `member_accounts` like everything
    else here, and it returns None for another member's incident and for one
    that does not exist alike.

    What it must never do is claim more than the record supports. In
    particular an alert with `delivered_to = 0` was detected and reached
    nobody (#57), and rendering that as "notified" because a row exists would
    undo that distinction in the one document where it matters most."""
    from sentinel.alerts.base import advice_steps, monitoring_note

    account_ids = [r["id"] for r in member_accounts(db, email)]
    if not account_ids:
        return None
    marks = ",".join("?" for _ in account_ids)
    with db.cursor() as cur:
        cur.execute(
            f"SELECT a.*, ac.label, ac.exchange FROM alerts a "
            f"JOIN accounts ac ON ac.id = a.account_id "
            f"WHERE a.id = ? AND a.account_id IN ({marks})",
            [alert_id, *account_ids],
        )
        alert = cur.fetchone()
    if alert is None:
        return None

    try:
        evidence = json.loads(alert["evidence_json"] or "{}")
    except ValueError:
        evidence = {}
    before, after = _incident_balances(db, alert["account_id"], alert["sent_at"])

    delivered = alert["delivered_to"] or 0
    if delivered > 0:
        notified = (
            f"<b>Notified</b> — sent to {delivered} channel(s)"
        )
        notified_cls = "ok"
    else:
        # The #57 fact, stated rather than softened.
        notified = (
            "<b>Not delivered</b> — this alert was detected and recorded but "
            "<b>reached nobody</b>. No notification was successfully sent."
        )
        notified_cls = "warn"

    if alert["acknowledged_at"]:
        acknowledged = (
            f"<b>Acknowledged</b> — by {html.escape(alert['acknowledged_by'] or 'unknown')} "
            f"at {_ts(alert['acknowledged_at'])}"
        )
        ack_cls = "ok"
    else:
        acknowledged = "<b>Not acknowledged</b> — nobody has confirmed seeing this alert."
        ack_cls = "warn"

    timeline = [
        ("ok", f"<b>Detected</b> — {html.escape(alert['message'])}", _ts(alert["sent_at"])),
        (notified_cls, notified, _ts(alert["sent_at"])),
        (ack_cls, acknowledged, _ts(alert["acknowledged_at"]) if alert["acknowledged_at"] else "—"),
    ]
    # A customer saying "that was me" is part of this incident's record, and
    # this page is the document handed to an exchange or an insurer. Leaving it
    # off would let a document describe a theft the account's owner had already
    # said was their own trading (G-24, #28).
    for mark in marks_for_alert(db, alert["id"]):
        note = f" — “{html.escape(mark['note'])}”" if mark["note"] else ""
        timeline.append((
            "ok",
            f"<b>Marked as expected activity</b> by {html.escape(mark['marked_by'])}"
            f"{note}",
            _ts(mark["created_at"]),
        ))

    timeline_html = "".join(
        '<div class="alert-item"><div class="alert-item__head">'
        + ('<span class="badge badge--ok"><span class="dot" aria-hidden="true"></span>Done</span>'
           if cls == "ok" else
           '<span class="badge badge--warn"><span class="dot" aria-hidden="true"></span>Attention</span>')
        + f"<span class='meta'>{when}</span></div>"
        f"<p class='muted'>{text}</p></div>"
        for cls, text, when in timeline
    )

    def _balance_kv(rows, when_label):
        if not rows:
            return f'<p class="empty">No snapshot {when_label} this alert.</p>'
        return '<dl class="kv">' + "".join(
            f'<div class="kv__row"><dt>{html.escape(r["coin"])}</dt>'
            f"<dd><span class='mono'>{html.escape(str(r['quantity']))}</span> "
            f"<span class='meta'>{_ts(r['snapshot_at'])}</span></dd></div>"
            for r in rows
        ) + "</dl>"

    evidence_kv = '<dl class="kv">' + ("".join(
        f'<div class="kv__row"><dt>{html.escape(str(k))}</dt>'
        f"<dd class=\"mono\">{html.escape(str(v))}</dd></div>"
        for k, v in sorted(evidence.items())
    ) or '<div class="kv__row"><dt>Evidence</dt><dd class="muted">No structured evidence recorded</dd></div>') + "</dl>"

    unack = not alert["acknowledged_at"]
    if unack:
        actionbar = (
            '<div class="actionbar no-print"><div class="container actionbar__inner">'
            '<p class="actionbar__note"><strong>Acknowledge doesn\'t stop the attack.</strong> '
            "It only marks this alert as seen.</p>"
            '<form method="post" action="/acknowledge">'
            f'<input type="hidden" name="alert_id" value="{int(alert["id"])}">'
            '<button type="submit" class="btn btn--primary btn--block">Acknowledge alert</button>'
            "</form></div></div>"
        )
    else:
        actionbar = (
            '<div class="actionbar no-print"><div class="container actionbar__inner">'
            f'<p class="actionbar__note">Acknowledged by '
            f"{html.escape(alert['acknowledged_by'] or 'unknown')} at {_ts(alert['acknowledged_at'])}.</p>"
            "</div></div>"
        )

    body = (
        '<section class="alert-head">'
        f"{_sev_badge(alert['severity'], large=True)}"
        f"<h1>{html.escape(alert['message'])}</h1>"
        '<div class="alert-meta">'
        f'<p><strong style="color:var(--text)">Detected:</strong> {_ts(alert["sent_at"])}</p>'
        f'<p><strong style="color:var(--text)">Account:</strong> '
        f"{html.escape((alert['exchange'] or 'bitget').title())} — {html.escape(alert['label'])}</p>"
        f'<p><strong style="color:var(--text)">Incident:</strong> #{int(alert["id"])} · '
        f"{html.escape(alert['rule'])} · confidence {html.escape(alert['confidence'] or '—')}</p>"
        "</div></section>"
        '<section class="panel" aria-labelledby="what-title"><h2 id="what-title">What was detected</h2>'
        f"{evidence_kv}</section>"
        '<section class="panel" aria-labelledby="tl-title"><h2 id="tl-title">Timeline</h2>'
        '<p class="count">detection → notification → acknowledgement</p>'
        f"{timeline_html}</section>"
        '<section class="panel" aria-labelledby="bal-title"><h2 id="bal-title">Balances before</h2>'
        f"{_balance_kv(before, 'before')}</section>"
        '<section class="panel" aria-labelledby="bal2-title"><h2 id="bal2-title">Balances after</h2>'
        f"{_balance_kv(after, 'after')}</section>"
        # Decisions 3 and 9: the SAME steps the alert email carries, from the
        # same function, so the page and the email cannot drift apart.
        '<section class="panel" aria-labelledby="next-title"><h2 id="next-title">What to do next</h2>'
        '<ol class="steps">' + "".join(f"<li>{html.escape(step)}</li>" for step in advice_steps(alert["exchange"]))
        + "</ol>"
        f'<p class="muted">{html.escape(monitoring_note())}</p></section>'
        '<div class="notice notice--info">' + _ICON_INFO
        + '<div><p class="notice__title">Minas cannot do this for you.</p>'
        '<p class="notice__body">Guardian is read-only. It can\'t revoke keys, cancel '
        "withdrawals, or freeze accounts. Only you and the exchange can.</p></div></div>"
        '<p class="no-print"><button class="linkbtn" type="button" onclick="window.print()">'
        "Print / Save as PDF</button></p>"
        + actionbar
    )
    return _page(body, email, actionbar=unack,
                 back=f"/account?id={int(alert['account_id'])}",
                 title=f"Incident #{int(alert['id'])} — Minas AI Guardian")


_RESET_ACK = (
    "If that address has a Minas AI Guardian login, a password-reset link is on "
    "its way. The link works once and expires within an hour."
)


def _reset_url(token: str) -> str:
    base = (os.environ.get("SENTINEL_PORTAL_URL") or "").strip().rstrip("/")
    return f"{base}/reset?token={token}" if base else f"/reset?token={token}"


def _send_reset_email(db: Database, address: str, token: str) -> None:
    """The one place the plaintext token exists outside the customer's mailbox.

    Guarded: a mail failure is logged, not raised. The alternative is a 500
    that tells the sender their address is real, which is the enumeration leak
    the whole flow is built to avoid."""
    from sentinel.alerts.base import AlertMessage
    from sentinel.alerts.contact import ContactRouter

    url = _reset_url(token)
    message = AlertMessage(
        account_label="your Minas AI Guardian login",
        rule="password_reset_requested",
        severity="LOW",
        confidence="n/a",
        message=(
            f"Someone asked to reset the password for {address}. Open this link to choose a "
            f"new one: {url}\n\nIt works once and expires within an hour. If this was not you, "
            f"you can ignore this email - your password has not changed."
        ),
        evidence={},
    )
    try:
        ContactRouter(db=db).send(
            _PlainNotice(message), "your Minas AI Guardian login", [address], [],
        )
    except Exception as exc:  # noqa: BLE001 - never leak existence through an error
        logger.error("could not send the password-reset email: %s", exc)


def _send_two_factor_disabled_email(db: Database, address: str) -> None:
    """Sent whenever two-factor is removed.

    The same reasoning as the password-change notice: a change the owner did
    not make is the only signal they will get. Removing a second factor is if
    anything the more important of the two, because it is what an attacker
    does *after* getting in and *before* doing anything else."""
    from sentinel.alerts.base import AlertMessage
    from sentinel.alerts.contact import ContactRouter

    message = AlertMessage(
        account_label="your Minas AI Guardian login",
        rule="two_factor_disabled",
        severity="LOW",
        confidence="n/a",
        message=(
            f"Two-factor authentication was just turned off for {address}. If this was not "
            f"you, somebody else has access to this login - sign in, turn two-factor back on, "
            f"and change your password immediately."
        ),
        evidence={},
    )
    ContactRouter(db=db).send(
        _PlainNotice(message), "your Minas AI Guardian login", [address], [],
    )


def _send_password_changed_email(db: Database, address: str) -> None:
    """Sent on EVERY password change, including one the owner just made.

    Not a receipt - a tripwire. A change the owner did not make is the only
    signal they will get that somebody else has their mailbox, and the cost of
    one extra email to the person who expected it is nothing against that."""
    from sentinel.alerts.base import AlertMessage
    from sentinel.alerts.contact import ContactRouter

    message = AlertMessage(
        account_label="your Minas AI Guardian login",
        rule="password_changed",
        severity="LOW",
        confidence="n/a",
        message=(
            f"The password for {address} was just changed, and every other signed-in device "
            f"has been signed out. If this was not you, contact Minas immediately - somebody "
            f"else has access to this mailbox."
        ),
        evidence={},
    )
    ContactRouter(db=db).send(
        _PlainNotice(message), "your Minas AI Guardian login", [address], [],
    )


class _PlainNotice:
    """Adapts an AlertMessage to the `detection` shape ContactRouter expects.
    These are account notices, not detections, so they carry no account id and
    leave no `sms_deliveries` row.

    `is_notice` says that in code rather than only in this docstring. A notice
    carries its whole meaning in `message` - a reset link, the address the
    alerts were moved to, the fact that a second factor was removed - so
    anything that reformats a detection into a summary must render these
    verbatim instead. Every notice in this module goes through this class, so
    declaring it here is what stops the next one being added without it."""

    is_notice = True

    def __init__(self, alert):
        self.rule = alert.rule
        self.severity = alert.severity
        self.confidence = alert.confidence
        self.message = alert.message
        self.evidence = alert.evidence


def _render_forgot(notice: str | None = None) -> str:
    """The request form, and the acknowledgement.

    The acknowledgement is deliberately the same sentence whether or not the
    address is a customer. A different wording, a different length or a
    different status code all answer the question an attacker asked - and the
    answer identifies somebody holding crypto worth monitoring (#33)."""
    if notice:
        inner = ('<div class="notice notice--ok" role="status">' + _ICON_INFO
                 + f'<div><p class="notice__title">{html.escape(notice)}</p></div></div>')
    else:
        inner = (
            '<form class="form" method="post" action="/forgot">'
            '<div class="field"><label for="fp-email">Email</label>'
            '<input class="input" type="email" id="fp-email" name="email" required autocomplete="email">'
            '<span class="hint">A single-use link that expires within an hour.</span></div>'
            '<button class="btn btn--primary btn--block" type="submit">Send reset link</button>'
            "</form>"
        )
    return _page(
        '<section class="auth-card">' + _auth_brand()
        + "<h1>Reset your password</h1>"
        '<div style="height:var(--space-4)"></div>' + inner
        + '<p class="hint" style="margin-top:var(--space-4)"><a href="/">‹ Back to sign in</a></p>'
        "</section>",
        title="Reset your password — Minas AI Guardian", shell=True,
    )


def _render_reset_form(token: str, error: str | None = None) -> str:
    notice = ""
    if error:
        notice = ('<div class="notice notice--danger" role="alert">' + _ICON_DANGER
                  + f'<div><p class="notice__title">{html.escape(error)}</p></div></div>'
                  '<div style="height:var(--space-4)"></div>')
    return _page(
        '<section class="auth-card">' + _auth_brand()
        + "<h1>Choose a new password</h1>"
        '<div style="height:var(--space-4)"></div>' + notice +
        '<form class="form" method="post" action="/reset">'
        f'<input type="hidden" name="token" value="{html.escape(token)}">'
        '<div class="field"><label for="np">New password</label>'
        '<input class="input" type="password" id="np" name="password" required '
        f'autocomplete="new-password" minlength="{MIN_PASSWORD_LENGTH}">'
        f'<span class="hint">At least {MIN_PASSWORD_LENGTH} characters. '
        "Setting a new password signs out every other device.</span></div>"
        '<button class="btn btn--primary btn--block" type="submit">Set password</button>'
        "</form></section>",
        title="Choose a new password — Minas AI Guardian", shell=True,
    )


def _render_code_prompt(pending: str, error: str | None = None) -> str:
    """Step two. The password has been accepted; the session has not been
    issued."""
    notice = ""
    if error:
        notice = ('<div class="notice notice--danger" role="alert">' + _ICON_DANGER
                  + f'<div><p class="notice__title">{html.escape(error)}</p></div></div>'
                  '<div style="height:var(--space-4)"></div>')
    return _page(
        '<section class="auth-card">' + _auth_brand()
        + "<h1>Two-factor code</h1>"
        '<div style="height:var(--space-4)"></div>' + notice +
        '<form class="form" method="post" action="/login-code">'
        f'<input type="hidden" name="pending" value="{html.escape(pending)}">'
        '<div class="field"><label for="otp">Code from your authenticator app</label>'
        '<input class="input" type="text" id="otp" name="code" required '
        'autocomplete="one-time-code" inputmode="numeric" autofocus>'
        '<span class="hint">Or enter one of your recovery codes.</span></div>'
        '<button class="btn btn--primary btn--block" type="submit">Sign in</button>'
        "</form></section>",
        title="Two-factor code — Minas AI Guardian", shell=True,
    )


def _render_replace_key(db: Database, email: str, account_id: int, *,
                        error: str | None = None, notice: str | None = None) -> str | None:
    """The form a customer uses to put a new read-only key in place (#69).

    Returns None - rendered as not-found - unless this account belongs to this
    member, exactly like every other account-scoped page. Existence is never
    revealed.

    **This page never arrives by link.** It is reachable only from a signed-in
    session, and the alert emails point at the portal's front door rather than
    here. "Minas emails you a link and asks you to paste an API key" is a
    phishing template we would be teaching customers to trust, and the cost of
    that lands on people we will never hear from. The copy says so on the page,
    because a customer who has learned the rule can spot the imitation.

    Nothing submitted is ever echoed back into the form. A wrong key is
    retyped, not corrected in place: rendering a secret back into HTML puts it
    in the browser's history, in any proxy's buffer, and on the shoulder of
    whoever is standing behind a frightened person."""
    rows = {r["id"]: r for r in member_accounts(db, email)}
    acct = rows.get(account_id)
    if acct is None:
        return None

    exchange = (acct["exchange"] or "bitget")
    banner = ""
    if error:
        banner = ('<div class="notice notice--danger" role="alert">' + _ICON_DANGER
                  + f'<div><p class="notice__title">{html.escape(error)}</p></div></div>')
    elif notice:
        banner = ('<div class="notice notice--ok" role="status">' + _ICON_INFO
                  + f'<div><p class="notice__title">{html.escape(notice)}</p></div></div>')

    # Only Bitget and OKX take a passphrase. Showing the field to everyone and
    # explaining it is better than hiding it: a Bybit customer who sees no
    # passphrase box and expects one has no way to tell whether the page is
    # broken or their key is different.
    passphrase_hint = (
        "Required for Bitget and OKX keys; leave empty for Bybit and Binance."
    )

    def _tick_li(kind, title, text):
        path = ("M8 12.5l2.5 2.5L16 9" if kind == "yes" else "M9 9l6 6M15 9l-6 6")
        return (f'<li><svg class="tick tick--{kind}" viewBox="0 0 24 24" fill="none" aria-hidden="true">'
                '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>'
                f'<path d="{path}" stroke="currentColor" stroke-width="2" '
                'stroke-linecap="round" stroke-linejoin="round"/></svg>'
                f"<div><strong>{title}</strong><span>{text}</span></div></li>")

    body = (
        '<div class="page-head"><h1>Replace the monitoring key</h1>'
        f'<p class="muted">{html.escape(acct["label"])}</p></div>'
        + banner +
        '<section class="panel"><ul class="feature-list">'
        + _tick_li("yes", "Read-only keys only.",
                   "A key that can trade or withdraw is refused, not accepted with a "
                   "warning. Create the new key with view or read permission only.")
        + _tick_li("yes", "It must be for this same exchange account.",
                   "We check the account the key belongs to before we store it, so a key "
                   "from a different account is refused.")
        + _tick_li("no", "Minas will never email you a link to this page.",
                   "You reached it by signing in, and that is the only way to reach it. If "
                   "a message ever asks you to paste an API key, it is not from us.")
        + "</ul></section>"
        + _key_instructions_panel(exchange)
        + '<section class="panel"><form class="form" method="post" action="/key" autocomplete="off">'
        f'<input type="hidden" name="account_id" value="{int(account_id)}">'
        f'<div class="field"><label for="k-key">New API key ({html.escape(exchange)})</label>'
        '<input class="input" type="text" id="k-key" name="api_key" required '
        'autocomplete="off" spellcheck="false"></div>'
        '<div class="field"><label for="k-secret">API secret</label>'
        '<input class="input" type="password" id="k-secret" name="secret" required autocomplete="off"></div>'
        '<div class="field"><label for="k-pass">API passphrase</label>'
        '<input class="input" type="password" id="k-pass" name="passphrase" autocomplete="off">'
        f'<span class="hint">{passphrase_hint}</span></div>'
        '<button class="btn btn--primary" type="submit">Check and replace</button>'
        "</form></section>"
    )
    return _page(body, email, "/",
                 title="Replace the monitoring key — Minas AI Guardian")


def _key_instructions_panel(exchange: str) -> str:
    """The plain-English, text-only key-creation steps for THIS account's
    exchange (#8, decision 14), with the dated interface line that makes
    text honest: staleness is disclosed on the page, not discovered by the
    person following stale steps."""
    from sentinel.keyinstructions import instructions_for, provenance_line

    entry = instructions_for(exchange)
    steps = "".join(f"<li>{html.escape(s)}</li>" for s in entry["steps"])
    provenance = provenance_line(entry)
    provenance_html = (
        f'<p class="muted" style="margin-top:var(--space-3)">{html.escape(provenance)}</p>'
        if provenance else ""
    )
    return (
        '<section class="panel" aria-labelledby="keysteps-title">'
        f'<h2 id="keysteps-title">Creating a read-only key on {html.escape((exchange or "your exchange").title())}</h2>'
        f'<ol class="muted" style="margin-top:var(--space-3);padding-left:var(--space-5)">{steps}</ol>'
        f"{provenance_html}</section>"
    )


def _render_security(db: Database, email: str, *, secret: str | None = None,
                     codes: list | None = None, error: str | None = None,
                     notice: str | None = None) -> str:
    """The member's own security page: turn two-factor on, or off (#32).

    Recovery codes are rendered exactly once, at the moment they are created.
    They are stored hashed, so this is genuinely the only time they can be
    shown - which is why the page says so rather than offering a "show again"
    link that could not work."""
    from sentinel.twofactor import MEMBER as _MEMBER
    from sentinel.twofactor import is_enabled, remaining_recovery_codes

    banner = ""
    if error:
        banner = ('<div class="notice notice--danger" role="alert">' + _ICON_DANGER
                  + f'<div><p class="notice__title">{html.escape(error)}</p></div></div>')
    elif notice:
        banner = ('<div class="notice notice--ok" role="status">' + _ICON_INFO
                  + f'<div><p class="notice__title">{html.escape(notice)}</p></div></div>')

    if codes:
        body = (
            '<section class="panel"><h2>Save your recovery codes</h2>'
            '<p class="muted">Each code works <strong>once</strong> and lets you sign '
            "in if you lose your phone. They are stored hashed, so this is the only time they "
            "can be shown — write them down or put them in a password manager now.</p>"
            '<p class="mono" style="margin-top:var(--space-4)">'
            + "<br>".join(html.escape(c) for c in codes)
            + "</p></section>"
        )
    elif secret:
        uri = _twofactor_uri(secret, email)
        body = (
            '<section class="panel"><h2>Set up two-factor</h2>'
            '<p class="muted">Add this to your authenticator app, then enter '
            "a code to switch it on. Two-factor is not active until you do — if the code never "
            "works, nothing has changed and you can still sign in.</p>"
            f'<p class="mono" id="totp-secret" style="margin-top:var(--space-3)">{html.escape(secret)}</p>'
            f'<p class="mono">{html.escape(uri)}</p>'
            '<form class="form" method="post" action="/security/2fa/confirm" style="margin-top:var(--space-4)">'
            '<div class="field"><label for="c2fa">Code from your app</label>'
            '<input class="input" type="text" id="c2fa" name="code" required inputmode="numeric" autofocus></div>'
            '<button class="btn btn--primary" type="submit">Turn on two-factor</button>'
            "</form></section>"
        )
    elif is_enabled(db, _MEMBER, email):
        left = remaining_recovery_codes(db, _MEMBER, email)
        body = (
            '<section class="panel"><h2>Two-factor is on</h2>'
            f'<p class="count">{left} recovery code(s) left</p>'
            '<p class="muted">Turning it off needs a current code from your '
            "app or a recovery code. We will email you when it happens — if you get that email "
            "and it was not you, somebody else is in your account.</p>"
            '<form class="form" method="post" action="/security/2fa/disable" style="margin-top:var(--space-4)">'
            '<div class="field"><label for="d2fa">Current code</label>'
            '<input class="input" type="text" id="d2fa" name="code" required inputmode="numeric"></div>'
            '<button class="btn btn--secondary" type="submit">Turn off two-factor</button>'
            "</form></section>"
        )
    else:
        body = (
            '<section class="panel"><h2>Two-factor is off</h2>'
            '<p class="muted">Your Minas login is what protects the alerts '
            "about your exchange accounts. Adding a second factor means a stolen password is "
            "not enough on its own.</p>"
            '<form class="form" method="post" action="/security/2fa/start" style="margin-top:var(--space-4)">'
            '<button class="btn btn--primary" type="submit">Set up two-factor</button>'
            "</form></section>"
        )
    # The 2FA interstitials (fresh secret, one-time recovery codes) stay
    # focused; the other settings join the page in its settled states.
    if not codes and not secret:
        body += _telegram_section(db, email)
        body += _preferences_section(db, email)
        body += _digest_section(db, email)
        body += _erasure_section(db, email)
    return _page(banner + body, email, "/security",
                 title="Security — Minas AI Guardian")


def _erasure_section(db: Database, email: str) -> str:
    """Delete my data (#46, decision 6 interim). Download first, then delete
    with the password (and the second factor when on). Says plainly what
    goes, what stays, and that it is immediate."""
    from sentinel.erasure import erasure_scope
    from sentinel.twofactor import MEMBER as _MEMBER
    from sentinel.twofactor import is_enabled

    scope = erasure_scope(db, email)
    primary = ", ".join(html.escape(a["label"]) for a in scope["primary"]) or "none"
    secondary = ", ".join(html.escape(a["label"]) for a in scope["secondary"])
    code_field = ""
    if is_enabled(db, _MEMBER, email):
        code_field = ('<div class="field"><label for="er-code">Code from your authenticator</label>'
                      '<input class="input" type="text" id="er-code" name="code" inputmode="numeric" required></div>')
    return (
        '<section class="panel" aria-labelledby="erase-title"><h2 id="erase-title">Delete my data</h2>'
        '<p class="muted">Deleting is immediate and cannot be undone. It removes the monitoring '
        "credentials and every record for the accounts you are the main contact of "
        f"(<strong>{primary}</strong>), your login and its settings, and your contact details. "
        "What remains is one entry stating that a deletion happened at your request, with the date "
        "and the number of records removed - nothing that identifies you. Monitoring of those accounts "
        "stops.</p>"
        + (f'<p class="muted">On <strong>{secondary}</strong> you are the second contact: you will be '
           "removed as a contact; that account belongs to someone else.</p>" if secondary else "")
        + '<p style="margin-top:var(--space-3)"><a class="btn btn--secondary btn--sm" href="/export.json">'
        "Download my data first</a></p>"
        '<form class="form" method="post" action="/erase" style="margin-top:var(--space-4)">'
        '<div class="field"><label for="er-pw">Your password</label>'
        '<input class="input" type="password" id="er-pw" name="password" autocomplete="current-password" required></div>'
        + code_field +
        '<div class="field"><label><input type="checkbox" name="confirm" value="yes" required> '
        "I understand this deletes my data now and cannot be undone.</label></div>"
        '<button class="btn btn--secondary" type="submit">Delete my data</button></form></section>'
    )


def _render_erased(email: str) -> str:
    return _page(
        '<section class="panel"><h2>Your data has been deleted</h2>'
        f'<p>Everything Minas AI Guardian held for <strong>{html.escape(email)}</strong> has been removed, '
        "and a confirmation has been sent to that address. One entry remains in our records stating that "
        "a deletion happened at your request. Monitoring of your accounts has stopped.</p>"
        "<p>Thank you for using Minas AI Guardian.</p></section>",
        None, title="Data deleted — Member portal", shell=True)


def _preferences_section(db: Database, email: str) -> str:
    """Alert preferences (#24, decision 4): timezone, quiet hours, and the
    channel grid. The CRITICAL/email cell is rendered locked - it is the
    floor - and the server refuses it off however the form is edited."""
    from sentinel.alerts.digest import member_exists_for_digest
    from sentinel.alerts.preferences import CHANNELS, FLOOR, SEVERITIES, member_prefs

    if not member_exists_for_digest(db, email):
        return ""
    prefs = member_prefs(db, email)
    import zoneinfo

    zones = sorted(zoneinfo.available_timezones())
    tz_options = '<option value="">— choose your timezone —</option>' + "".join(
        f'<option value="{html.escape(z)}"{" selected" if z == prefs["timezone"] else ""}>{html.escape(z)}</option>'
        for z in zones)
    rows = ""
    for sev in SEVERITIES:
        cells = ""
        for ch in CHANNELS:
            name = f"grid_{sev}_{ch}"
            checked = " checked" if prefs["grid"][sev][ch] else ""
            if (sev, ch) == FLOOR:
                cells += (f'<label style="margin-right:var(--space-4)"><input type="checkbox" checked disabled> '
                          f'{ch.capitalize()} (always on)</label><input type="hidden" name="{name}" value="on">')
            else:
                cells += (f'<label style="margin-right:var(--space-4)"><input type="checkbox" name="{name}" '
                          f'value="on"{checked}> {ch.capitalize()}</label>')
        rows += f'<div class="kv__row"><dt>{sev.capitalize()}</dt><dd>{cells}</dd></div>'
    quiet_checked = " checked" if prefs["quiet_hours_enabled"] else ""
    return (
        '<section class="panel" aria-labelledby="prefs-title"><h2 id="prefs-title">Alert preferences</h2>'
        '<p class="muted">Which channels reach you for each level, and quiet hours for '
        "everything except critical alerts. Critical alerts always reach you by email - that "
        "cannot be switched off. Anything held during quiet hours arrives in your next digest.</p>"
        '<form class="form" method="post" action="/preferences" style="margin-top:var(--space-3)">'
        '<div class="field"><label for="tz">Your timezone</label>'
        f'<select class="input" id="tz" name="timezone">{tz_options}</select>'
        '<p class="hint">Quiet hours need this. Without it they cannot be enabled.</p></div>'
        '<div class="field"><label><input type="checkbox" name="quiet_hours_enabled" value="on"'
        f'{quiet_checked}> Quiet hours for non-critical alerts</label></div>'
        '<div class="field"><label for="qs">From</label>'
        f'<input class="input" type="time" id="qs" name="quiet_start" value="{html.escape(prefs["quiet_start"])}"></div>'
        '<div class="field"><label for="qe">Until</label>'
        f'<input class="input" type="time" id="qe" name="quiet_end" value="{html.escape(prefs["quiet_end"])}"></div>'
        f'<dl class="kv">{rows}</dl>'
        '<button class="btn btn--secondary" type="submit" style="margin-top:var(--space-4)">Save preferences</button>'
        "</form></section>"
    )


def _digest_section(db: Database, email: str) -> str:
    """The proof-of-life digest choice (#21): daily, weekly, or off. Only a
    member with a portal login can change it - the same bar as every other
    preference here."""
    from sentinel.alerts.digest import digest_frequency, member_exists_for_digest

    current = digest_frequency(db, email)
    if not member_exists_for_digest(db, email):
        return (
            '<section class="panel" aria-labelledby="digest-title"><h2 id="digest-title">Proof-of-life digest</h2>'
            '<p class="muted">A periodic summary — accounts watched, checks '
            "performed, issues found — so silence is provably the good kind. Arrives daily; "
            "ask the Minas team to change it until your portal login is set up.</p></section>"
        )
    options = "".join(
        f'<label style="display:block;margin-top:var(--space-2)">'
        f'<input type="radio" name="frequency" value="{value}"'
        f'{" checked" if value == current else ""}> {label}</label>'
        for value, label in (("daily", "Daily — one summary every day"),
                             ("weekly", "Weekly — Mondays"),
                             ("off", "Off — alerts only, no summaries"))
    )
    return (
        '<section class="panel" aria-labelledby="digest-title"><h2 id="digest-title">Proof-of-life digest</h2>'
        '<p class="muted">A periodic summary — accounts watched, checks '
        "performed, issues found — so silence is provably the good kind. It sends even when "
        "all is quiet; that is the point.</p>"
        '<form class="form" method="post" action="/digest/frequency" style="margin-top:var(--space-3)">'
        f"{options}"
        '<button class="btn btn--secondary" type="submit" style="margin-top:var(--space-4)">Save</button>'
        "</form></section>"
    )


def _telegram_section(db: Database, email: str) -> str:
    """Self-serve Telegram connection (#18): guided, test-messaged, and
    disconnectable from the same place. The deep link is rendered from the
    bot username the worker learned; until it is known the steps name 'the
    Minas bot' and the code still works."""
    from sentinel.alerts.telegram_link import (
        LINK_CODE_TTL_MINUTES,
        bot_username,
        connected_link,
        pending_link,
    )

    link = connected_link(db, email)
    if link is not None:
        return (
            '<section class="panel" aria-labelledby="tg-title"><h2 id="tg-title">Telegram alerts are on</h2>'
            f'<p class="count">connected {_ts(link["connected_at"])}</p>'
            '<p class="muted">Copies of your alerts go to your Telegram chat, in the '
            "same customer wording as your email. Disconnecting stops them — we email "
            "you when that happens, in case it wasn't you.</p>"
            '<form class="form" method="post" action="/telegram/unlink" style="margin-top:var(--space-4)">'
            '<button class="btn btn--secondary" type="submit">Disconnect Telegram</button>'
            "</form></section>"
        )

    pending = pending_link(db, email)
    if pending is not None:
        username = bot_username(db)
        code = html.escape(pending["link_code"])
        if username:
            opener = (f'<a class="acctlink" href="https://t.me/{html.escape(username)}?start={code}" '
                      f'rel="noopener">Open @{html.escape(username)} in Telegram ›</a>')
        else:
            opener = "Open the Minas AI Guardian bot in Telegram"
        return (
            '<section class="panel" aria-labelledby="tg-title"><h2 id="tg-title">Finish connecting Telegram</h2>'
            '<p class="muted">Three steps, and your first message is a test '
            "message — so you see it working before you rely on it.</p>"
            '<ol class="muted" style="margin-top:var(--space-3);padding-left:var(--space-5)">'
            f"<li>{opener}</li>"
            f"<li>Press <strong>Start</strong> — or send <span class='mono'>/start {code}</span></li>"
            "<li>You'll get the test message in that chat, and this page will show you as connected.</li>"
            "</ol>"
            f'<p class="muted" style="margin-top:var(--space-3)">This code works once and expires in '
            f"{LINK_CODE_TTL_MINUTES} minutes.</p>"
            '<form class="form" method="post" action="/telegram/link" style="margin-top:var(--space-4)">'
            '<button class="btn btn--tertiary" type="submit">Generate a new code</button>'
            "</form></section>"
        )

    return (
        '<section class="panel" aria-labelledby="tg-title"><h2 id="tg-title">Telegram alerts are off</h2>'
        '<p class="muted">Get copies of your alerts in Telegram, instantly on '
        "your phone. Email keeps arriving either way — Telegram is a second copy, "
        "never a replacement, so a Telegram outage cannot cost you an alert.</p>"
        '<form class="form" method="post" action="/telegram/link" style="margin-top:var(--space-4)">'
        '<button class="btn btn--primary" type="submit">Connect Telegram</button>'
        "</form></section>"
    )


def _send_telegram_disconnected_email(db: Database, address: str) -> None:
    """The tripwire for the disconnect: silencing a channel is what an
    attacker does before acting, so the owner hears about it on the channel
    that still works."""
    from sentinel.alerts.base import AlertMessage
    from sentinel.alerts.contact import ContactRouter

    message = AlertMessage(
        account_label="your Minas AI Guardian login",
        rule="telegram_disconnected",
        severity="LOW",
        confidence="n/a",
        message=(
            f"Telegram alerts were just disconnected for {address}. Email alerts "
            f"continue unaffected. If this was not you, somebody else has access to "
            f"this login - sign in and change your password immediately."
        ),
        evidence={},
    )
    ContactRouter(db=db).send(
        _PlainNotice(message), "your Minas AI Guardian login", [address], [],
    )


def _twofactor_uri(secret: str, email: str) -> str:
    from sentinel.totp import provisioning_uri

    return provisioning_uri(secret, email)


def _render_not_found() -> str:
    return _page(
        '<section class="panel"><p class="empty">Not found.</p>'
        '<p style="margin-top:var(--space-3)"><a href="/">‹ Back to your accounts</a></p></section>',
        title="Not found — Minas AI Guardian",
    )


# --- server ------------------------------------------------------------------

def serve_portal(db: Database, port: int, host: str = "127.0.0.1",
                 rules=None, cipher=None, metrics=None,
                 operator_alerter=None, drill_channels=None, drill_router=None, drill_alerter=None) -> ThreadingHTTPServer:
    """Customer-facing member portal.

    Unlike the ops dashboard this one has its own login and is *meant* to be
    reachable by customers — but it still defaults to loopback, because it
    serves plain HTTP and its session cookie would cross the network in the
    clear. Reaching customers is the job of a TLS-terminating proxy in front
    of it, not of this socket. Set `SENTINEL_PORTAL_HOST` to widen it
    deliberately once that proxy exists (G-36)."""
    secret = _session_secret(db)
    throttle = LoginThrottle()

    def _throttle_pair(ip: str, identity: str) -> str:
        """Identity-scoped throttling is keyed to (ip, identity), never the
        bare identity (#31): a stranger who knows a victim's email must not
        be able to lock the victim out of the one page they need most
        mid-incident. The per-IP key still caps any single source across
        every identity; the deliberate residue is that a DISTRIBUTED
        guesser is throttled per-IP only - the alternative hands every
        stranger a lockout button."""
        return f"{ip}|{identity}"

    def _same_origin(handler) -> bool:
        """Is this POST from our own pages? (CSRF gate on every state change.)

        Sec-Fetch-Site is the PRIMARY signal: the browser sets it and a page
        cannot forge it (a forbidden header). `same-origin` is our own form;
        `none` is user-initiated (address bar / bookmark); anything else is
        another site posting at us. The Origin comparison is only the
        fallback for clients that do not send Sec-Fetch-Site.

        `Origin: null` alone is NEVER accepted: sandboxed iframes and some
        cross-site navigations serialise null too, and under a no-referrer
        policy even our OWN form posts did - which is how this check rejected
        every real browser sign-in while curl (no referrer policy) and the
        test client (no Origin at all) sailed through. Two correct hardening
        measures colliding; the referrer policy is now `same-origin` so
        Origin stays populated, and this check no longer depends on it alone.
        """
        fetch_site = (handler.headers.get("Sec-Fetch-Site") or "").lower()
        if fetch_site:
            return fetch_site in ("same-origin", "none")
        origin = handler.headers.get("Origin")
        if not origin:
            return True  # non-browser clients send neither header
        return urlparse(origin).netloc == (handler.headers.get("Host") or "")

    class Handler(BaseHTTPRequestHandler):
        def finish(self):
            # Same leak as the dashboard's (#79): the portal serves from the
            # same process and the same fd budget, so its requests leaked into
            # the ceiling too even while it still answered 200.
            try:
                super().finish()
            finally:
                db.release_thread_connection()

        def _send(self, body: bytes, status: int = 200, extra_headers: list[tuple[str, str]] | None = None,
                  content_type: str = "text/html; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            # same-origin, not no-referrer: no-referrer makes the browser
            # serialise Origin as null on our OWN form posts, which the CSRF
            # gate must refuse (null is also what sandboxed iframes send) -
            # so every browser sign-in was rejected. same-origin keeps URLs
            # (reset tokens included) out of every cross-site Referer, which
            # is the protection that mattered, while our own posts stay
            # attributable to us.
            self.send_header("Referrer-Policy", "same-origin")
            refresh = getattr(self, "_refresh_cookie", None)
            if refresh is not None:
                self.send_header("Set-Cookie", refresh)
                self._refresh_cookie = None
            for k, v in extra_headers or []:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, location: str, extra_headers: list[tuple[str, str]] | None = None):
            self.send_response(303)
            self.send_header("Location", location)
            for k, v in extra_headers or []:
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _session_email(self) -> str | None:
            """The signed-in member, or None.

            Checks the token's epoch against the member's current one on every
            request. That lookup is the price of being able to sign somebody
            out at all: without it a cookie issued before a password change
            keeps working until it expires on its own, which is precisely the
            window an attacker needs after a customer resets in a panic
            (#33)."""
            cookie = self.headers.get("Cookie") or ""
            for part in cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name != SESSION_COOKIE or not value:
                    continue
                email = verify_session_token(secret, value)
                if email is None:
                    return None
                with db.cursor() as cur:
                    cur.execute("SELECT session_epoch FROM portal_members WHERE email = ?",
                                (normalize_email(email),))
                    row = cur.fetchone()
                if row is None:
                    return None  # the member was removed; the cookie is stale
                current = verify_session_token(secret, value, epoch=row["session_epoch"])
                if current is not None:
                    # Activity slides the idle window (#31): re-issue with a
                    # fresh idle deadline and the ORIGINAL absolute one -
                    # copied, never extended. _send attaches it.
                    hard = session_hard_deadline(value)
                    if hard is not None and hard > time.time():
                        refreshed = make_session_token(
                            secret, current, epoch=row["session_epoch"],
                            absolute_deadline=hard)
                        max_age = max(1, int(min(hard - time.time(), SESSION_IDLE_SECONDS)))
                        self._refresh_cookie = (
                            f"{SESSION_COOKIE}={refreshed}; Max-Age={max_age}; "
                            f"Path=/; HttpOnly; Secure; SameSite=Lax")
                return current
            return None

        def _client_ip(self) -> str:
            return self.client_address[0] if self.client_address else "?"

        def do_GET(self):
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            email = self._session_email()
            if email is None:
                # The two recovery routes are reachable without a session -
                # that is the entire point of a recovery route.
                if path == "/forgot":
                    self._send(_render_forgot().encode())
                    return
                if path == "/reset":
                    qs = parse_qs(urlparse(self.path).query)
                    token = (qs.get("token") or [""])[0]
                    # Validated on submit, not here: checking it now would burn
                    # a single-use token just for rendering a form, and a
                    # customer who reloads the page would be locked out by
                    # their own browser.
                    if token:
                        self._send(_render_reset_form(token).encode())
                    else:
                        self._send(_render_login().encode())
                    return
                # Every other unauthenticated GET lands on the login page.
                self._send(_render_login().encode())
                return
            if path == "/":
                self._send(_render_home(db, email, rules).encode())
            elif path == "/alerts":
                qs = parse_qs(urlparse(self.path).query)
                self._send(_render_alert_history(db, email, qs).encode())
            elif path == "/limits":
                self._send(_render_limits(db, email, rules).encode())
            elif path == "/status":
                body = _render_status(db, email, rules)
                self._send(body.encode())
            elif path == "/activity":
                self._send(_render_activity(db, email).encode())
            elif path == "/export.json":
                self._export()
                return
            elif path == "/alerts.csv":
                qs = parse_qs(urlparse(self.path).query)
                self._send(
                    _alert_history_csv(db, email, qs),
                    content_type="text/csv; charset=utf-8",
                    extra_headers=[("Content-Disposition",
                                    'attachment; filename="minas-alert-history.csv"')],
                )
            elif path == "/incident":
                qs = parse_qs(urlparse(self.path).query)
                try:
                    alert_id = int(qs.get("id", ["0"])[0])
                except ValueError:
                    alert_id = 0
                page = _render_incident(db, email, alert_id)
                if page is None:
                    self._send(_render_not_found().encode(), 404)
                else:
                    self._send(page.encode())
            elif path == "/security":
                self._send(_render_security(db, email).encode())
            elif path == "/key":
                qs = parse_qs(urlparse(self.path).query)
                try:
                    aid = int(qs.get("id", ["0"])[0])
                except ValueError:
                    aid = 0
                page = _render_replace_key(db, email, aid)
                if page is None:
                    self._send(_render_not_found().encode(), 404)
                else:
                    self._send(page.encode())
            elif path == "/account":
                qs = parse_qs(urlparse(self.path).query)
                try:
                    aid = int(qs.get("id", ["0"])[0])
                except ValueError:
                    aid = 0
                page = _render_member_account(db, email, aid, rules)
                if page is None:
                    self._send(_render_not_found().encode(), 404)
                else:
                    self._send(page.encode())
            else:
                self._send(_render_not_found().encode(), 404)

        @staticmethod
        def _pad_to(deadline: float) -> None:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)

        def _pause(self, path, form):
            """Start or cancel the pause countdown (#98). Session-scoped and
            account-scoped: the account must belong to the signed-in member,
            and one the member cannot see is indistinguishable from one that
            does not exist."""
            member = self._session_email()
            if member is None:
                self._send(_render_login().encode())
                return
            try:
                account_id = int((form.get("account_id") or [""])[0])
            except (TypeError, ValueError):
                self._send(_render_not_found().encode(), 404)
                return
            if account_id not in {int(r["id"]) for r in member_accounts(db, member)}:
                self._send(_render_not_found().encode(), 404)
                return
            from sentinel.pause import cancel_pause, request_pause

            if path == "/pause/request":
                request_pause(db, account_id, member)
                logger.info("portal: pause requested on account %d by %s", account_id, member)
            else:
                cancelled = cancel_pause(db, account_id, member)
                logger.info("portal: pause cancel on account %d by %s (pending=%s)",
                            account_id, member, cancelled)
            page = _render_member_account(db, member, account_id, rules)
            self._send((page or _render_not_found()).encode())

        def _export(self):
            """Download my data (#46 criterion 3): the member's own accounts'
            events and alerts, as JSON, before erasure."""
            email = self._session_email()
            if email is None:
                self._send(_render_login().encode())
                return
            import json as _json

            from sentinel.erasure import export_member_data

            payload = _json.dumps(export_member_data(db, email), indent=1, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="minas-guardian-data.json"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _erase(self, form):
            """#46, decision 6 interim: proof of possession (password, and the
            second factor when on), then hard-delete, gap recorded, written
            confirmation sent, session ended."""
            email = self._session_email()
            if email is None:
                self._send(_render_login().encode())
                return
            from sentinel.erasure import confirmation_text, erase_member
            from sentinel.twofactor import MEMBER as _MEMBER
            from sentinel.twofactor import is_enabled

            password = form.get("password", [""])[0] or ""
            if form.get("confirm", [""])[0] != "yes":
                self._send(_render_security(db, email, error="Tick the confirmation to delete your data.").encode(), 400)
                return
            if not check_member_login(db, email, password):
                self._send(_render_security(db, email, error="That password was not accepted.").encode(), 401)
                return
            if is_enabled(db, _MEMBER, email):
                code = (form.get("code", [""])[0] or "").strip()
                if cipher is None or not check_second_factor(db, cipher, TWOFACTOR_MEMBER, email, code):
                    self._send(_render_security(db, email, error="That code was not accepted.").encode(), 401)
                    return

            def _notify(address, gap):
                from sentinel.alerts.base import AlertMessage
                from sentinel.alerts.contact import ContactRouter

                message = AlertMessage(account_label="your Minas AI Guardian data", rule="data_erased",
                                       severity="LOW", confidence="n/a", message=confirmation_text(gap),
                                       evidence={})
                ContactRouter(db=db).send(_PlainNotice(message), "your Minas AI Guardian data", [address], [])

            try:
                erase_member(db, email, actor=email, notify=_notify)
            except ValueError as exc:
                self._send(_render_security(db, email, error=str(exc)).encode(), 400)
                return
            logger.warning("member %s erased their data from %s", email, self._client_ip())
            body = _render_erased(email).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _drill(self):
            """"Test my alerts" (#37, decision 2): an on-demand drill against
            one of the member's own accounts. Session-scoped; once an hour per
            member, because a drill is a real message to a real phone."""
            email = self._session_email()
            if email is None:
                self._send(_render_login().encode())
                return
            from sentinel.drill import last_drill, run_drill

            accounts = member_accounts(db, email)
            if not accounts:
                self._send(_render_status(db, email, rules, drill_notice="No account to test yet.").encode())
                return
            ids = [int(a["id"]) for a in accounts]
            recent = last_drill(db, kind="on_demand", account_ids=ids)
            if recent is not None:
                try:
                    age = datetime.now(timezone.utc) - datetime.fromisoformat(recent["started_at"])
                except ValueError:
                    age = timedelta(hours=2)
                if age < timedelta(hours=1):
                    self._send(_render_status(
                        db, email, rules,
                        drill_notice="A test already ran in the last hour - its result is below.").encode())
                    return
            outcome = run_drill(db, drill_channels or {}, drill_router, drill_alerter, rules,
                                account_id=ids[0], kind="on_demand", requested_by=email)
            if outcome["result"] == "passed":
                note = (f"Drill sent - it reached {outcome['delivered_to']} destination(s) in "
                        f"{outcome['latency_ms']} ms. Check your phone.")
            else:
                note = "The drill did not reach anybody. Minas has been paged; contact us if you were expecting it."
            self._send(_render_status(db, email, rules, drill_notice=note).encode())

        def _preferences(self, form):
            """#24: session-scoped like every preference here. Refusals come
            back on the page with the reason; a save confirms on screen and
            takes effect on the next alert."""
            email = self._session_email()
            if email is None:
                self._send(_render_login().encode())
                return
            from sentinel.alerts.preferences import CHANNELS, SEVERITIES, set_member_prefs

            grid = {sev: {ch: (form.get(f"grid_{sev}_{ch}", [""])[0] == "on") for ch in CHANNELS}
                    for sev in SEVERITIES}
            try:
                changed = set_member_prefs(
                    db, email,
                    timezone_name=(form.get("timezone", [""])[0] or "").strip() or None,
                    quiet_hours_enabled=(form.get("quiet_hours_enabled", [""])[0] == "on"),
                    quiet_start=(form.get("quiet_start", ["22:00"])[0] or "22:00"),
                    quiet_end=(form.get("quiet_end", ["07:00"])[0] or "07:00"),
                    grid=grid)
            except ValueError as exc:
                self._send(_render_security(db, email, error=str(exc)).encode(), 400)
                return
            if changed:
                from sentinel.events import record_member_security_event

                record_member_security_event(db, email, "alert_preferences_changed")
            self._send(_render_security(db, email, notice="Alert preferences saved - they apply from the next alert.").encode())

        def _digest_frequency(self, form):
            """The member's own digest choice (#21). Session-scoped; an
            unrecognized value changes nothing rather than guessing."""
            email = self._session_email()
            if email is None:
                self._send(_render_login().encode())
                return
            from sentinel.alerts.digest import DIGEST_FREQUENCIES, set_digest_frequency

            value = (form.get("frequency", [""])[0] or "").strip()
            if value not in DIGEST_FREQUENCIES:
                self._send(_render_security(db, email, error="Choose daily, weekly, or off.").encode())
                return
            if set_digest_frequency(db, email, value):
                from sentinel.events import record_member_security_event

                record_member_security_event(db, email, "digest_frequency_changed",
                                             {"to": value})
            self._send(_render_security(
                db, email, notice=f"Digest set to {value}.").encode())

        def _telegram_link(self, path):
            """Start or end a Telegram connection (#18). Session-scoped like
            _twofactor: the subject is the signed-in member, never a form
            field - anyone with a session may connect or disconnect only
            their own chat."""
            email = self._session_email()
            if email is None:
                self._send(_render_login().encode())
                return
            from sentinel.alerts.telegram_link import create_link_code, disconnect

            if path == "/telegram/link":
                create_link_code(db, email)
                self._send(_render_security(db, email).encode())
                return
            if disconnect(db, email):
                from sentinel.events import record_member_security_event

                record_member_security_event(db, email, "member_telegram_disconnected", {})
                try:
                    _send_telegram_disconnected_email(db, email)
                except Exception as exc:  # noqa: BLE001 - the disconnect stands regardless
                    logger.error("telegram-disconnect notice to %s failed: %s", email, exc)
                self._send(_render_security(
                    db, email, notice="Telegram disconnected. Email alerts continue unaffected.").encode())
            else:
                self._send(_render_security(db, email).encode())

        def _twofactor(self, path, form):
            """Enrol in, confirm or remove two-factor (#32).

            Session-scoped: the subject is the signed-in member, never a form
            field. A form-supplied identity here would let anyone with a
            session enrol - or disable - two-factor on somebody else's
            login."""
            email = self._session_email()
            if email is None:
                self._send(_render_login().encode())
                return
            if cipher is None:
                self._send(_render_security(
                    db, email, error="Two-factor is unavailable on this deployment.").encode())
                return

            from sentinel.twofactor import MEMBER as _MEMBER
            from sentinel.twofactor import begin_enrolment, confirm_enrolment
            from sentinel.twofactor import disable as disable_2fa

            code = form.get("code", [""])[0] or ""
            if path.endswith("/start"):
                try:
                    secret, _uri = begin_enrolment(db, cipher, _MEMBER, email)
                except ValueError as exc:
                    self._send(_render_security(db, email, error=str(exc)).encode())
                    return
                self._send(_render_security(db, email, secret=secret).encode())
                return

            if path.endswith("/confirm"):
                codes = confirm_enrolment(db, cipher, _MEMBER, email, code)
                if codes is None:
                    # Nothing changed. Re-showing the setup page would need the
                    # secret again, and re-generating one would silently swap
                    # what the customer just scanned - so send them back to
                    # start deliberately.
                    self._send(_render_security(
                        db, email,
                        error="That code was not accepted. Start again and re-scan.").encode())
                    return
                logger.info("two-factor enabled for member %s", email)
                self._send(_render_security(db, email, codes=codes).encode())
                return

            if path.endswith("/disable"):
                ok = disable_2fa(db, cipher, _MEMBER, email, code,
                                 notify=lambda addr: _send_two_factor_disabled_email(db, addr))
                if not ok:
                    self._send(_render_security(
                        db, email, error="That code was not accepted. Two-factor is still on.").encode())
                    return
                logger.warning("two-factor disabled for member %s", email)
                self._send(_render_security(
                    db, email, notice="Two-factor is off. We have emailed you about it.").encode())
                return

            self._send(_render_not_found().encode(), 404)

        def _login_code(self, form):
            """Step two of a two-factor sign-in (#32).

            The email comes from the *signed pending token*, never from the
            form. Reading it from a form field would let anyone who can reach
            this endpoint name whichever account they liked and supply a code
            for their own enrolment."""
            pending = form.get("pending", [""])[0] or ""
            code = form.get("code", [""])[0] or ""
            email = verify_pending_token(secret, pending)
            ip = self._client_ip()
            if email is None:
                # Expired, forged, or a session token presented in its place.
                logger.warning("two-factor step 2 with an invalid pending token from %s", ip)
                self._send(_render_login(error="That sign-in expired. Please start again.").encode())
                return

            if throttle.blocked(_throttle_pair(ip, email)) or throttle.blocked(ip):
                logger.warning("two-factor throttled for %s from %s", email, ip)
                self._send(_render_login(error="Too many attempts — try again later.").encode(), 429)
                return

            if cipher is None or not check_second_factor(db, cipher, TWOFACTOR_MEMBER, email, code):
                # Counted on the same limiter as passwords. Six digits is a
                # million guesses, and an endpoint that accepts them as fast as
                # they arrive is not a second factor.
                throttle.record_failure(_throttle_pair(ip, email))
                throttle.record_failure(ip)
                logger.warning("two-factor code rejected for %s from %s", email, ip)
                self._send(_render_code_prompt(pending, error="That code was not accepted.").encode())
                return

            throttle.reset(_throttle_pair(ip, email))
            with db.cursor() as cur:
                cur.execute("SELECT session_epoch FROM portal_members WHERE email = ?", (email,))
                row = cur.fetchone()
            token = make_session_token(secret, email, epoch=row["session_epoch"] if row else 1)
            logger.info("portal login complete for %s from %s", email, ip)
            self._redirect("/", [(
                "Set-Cookie",
                f"{SESSION_COOKIE}={token}; Max-Age={SESSION_TTL_SECONDS}; Path=/; HttpOnly; "
                f"Secure; SameSite=Lax",
            )])

        def _forgot(self, form):
            """Request a reset link (#33).

            Throttled on the same limiter as login, per address and per IP.
            Without it this endpoint sends mail to anyone, as fast as anyone
            asks - a way to bury a real alert under a hundred reset emails, or
            simply to make Minas a spam source."""
            # Both paths are padded to the same floor before responding.
            # Identical wording is not enough on its own: issuing a token
            # writes twice and sends mail, and an unknown address does
            # neither, so a stopwatch answers the question the wording
            # refuses to. The floor is generous next to that difference and
            # small next to a human.
            #
            # Residual, stated rather than hidden: a mail server slow enough
            # to blow through the floor still leaks. Moving the send off this
            # path entirely is the real fix and needs a queue.
            deadline = time.monotonic() + _RESET_RESPONSE_FLOOR_SECONDS
            address = normalize_email((form.get("email", [""])[0] or ""))
            ip = self._client_ip()
            if throttle.blocked(_throttle_pair(ip, address)) or throttle.blocked(ip):
                logger.warning("reset request throttled for %s from %s", address or "<empty>", ip)
                # Same acknowledgement even when throttled: "you are being
                # rate limited" is itself a signal that this address is worth
                # rate limiting.
                self._pad_to(deadline)
                self._send(_render_forgot(notice=_RESET_ACK).encode())
                return
            throttle.record_failure(_throttle_pair(ip, address))
            throttle.record_failure(ip)

            token = create_reset_token(db, address)
            if token:
                logger.info("password reset requested for %s from %s", address, ip)
                _send_reset_email(db, address, token)
            else:
                logger.info("password reset requested for an unknown address from %s", ip)
            # One sentence, both paths, always 200, same elapsed time.
            self._pad_to(deadline)
            self._send(_render_forgot(notice=_RESET_ACK).encode())

        def _reset(self, form):
            """Redeem a reset link and set a new password (#33)."""
            token = (form.get("token", [""])[0] or "")
            password = form.get("password", [""])[0] or ""
            if len(password) < MIN_PASSWORD_LENGTH:
                self._send(_render_reset_form(
                    token, error=f"Use at least {MIN_PASSWORD_LENGTH} characters.").encode())
                return

            address = consume_reset_token(db, token)
            if address is None:
                # Expired, already used, or never real. One message for all
                # three: which one it was is not the customer's problem and is
                # information an attacker would like.
                self._send(_render_forgot(
                    notice="That link is no longer valid. Request a new one.").encode())
                return

            set_member_password(db, address, password, invalidate_sessions=True,
                                notify=lambda addr: _send_password_changed_email(db, addr))
            logger.info("password reset completed for %s from %s", address, self._client_ip())
            throttle.reset(_throttle_pair(self._client_ip(), address))
            # No session issued. Setting a password proves control of the
            # mailbox, not knowledge of the password, and signing them straight
            # in would make the link itself a login - which is the thing the
            # single-use expiry exists to limit.
            self._redirect("/")

        def _acknowledge(self, form):
            """The portal's only member-facing write (G-22, #26).

            Every failure renders the same not-found page. A member poking at
            alert ids must not be able to tell "that isn't yours" from "that
            doesn't exist" - the first answer maps out other customers' alert
            volume one id at a time."""
            email = self._session_email()
            if email is None:
                self._send(_render_login().encode())
                return
            try:
                alert_id = int((form.get("alert_id") or [""])[0])
            except (TypeError, ValueError):
                self._send(_render_not_found().encode(), 404)
                return

            account_ids = [r["id"] for r in member_accounts(db, email)]
            alert = _member_alert(db, alert_id, account_ids)
            if alert is None:
                self._send(_render_not_found().encode(), 404)
                return
            account_id = alert["account_id"]

            if acknowledge_alert_as_member(db, alert_id, email, account_ids):
                logger.info("portal: alert %d acknowledged by %s", alert_id, email)
            # An already-acknowledged alert is not an error - two people
            # opening the same incident is the normal case. It redirects to the
            # same page, where the first acknowledgement is still shown.
            self._redirect(f"/account?id={account_id}")

        def _expected(self, form):
            """"That was me" (G-24, #28).

            Same isolation as `_acknowledge` and `_snooze`: the alert is looked
            up inside the member's own accounts, and every failure renders the
            same not-found page, so poking at ids cannot map another
            customer's alert volume.

            Unlike a snooze this changes nothing about what the product
            watches or delivers. It records a judgement, which is why it is
            safe to make it one click - and why it must never grow into a
            suppression: the customer clicking it is telling us about the
            past, not asking for silence."""
            email = self._session_email()
            if email is None:
                self._send(_render_login().encode())
                return
            try:
                alert_id = int((form.get("alert_id") or [""])[0])
            except (TypeError, ValueError):
                self._send(_render_not_found().encode(), 404)
                return

            account_ids = [r["id"] for r in member_accounts(db, email)]
            alert = _member_alert(db, alert_id, account_ids)
            if alert is None:
                self._send(_render_not_found().encode(), 404)
                return

            note = (form.get("note") or [""])[0]
            first = mark_expected(db, alert_id, alert["account_id"], alert["rule"],
                                  alert["dedup_key"], marked_by=normalize_email(email),
                                  note=note)
            # Counted once per alert per person, matching the table's
            # uniqueness: a customer clicking twice has not found two false
            # positives, and counting it twice would inflate the only number
            # we have about how wrong a rule is.
            if first and metrics is not None:
                metrics.alert_marked_expected(alert["rule"])
            self._redirect(f"/account?id={alert['account_id']}")

        def _snooze(self, form):
            """Silence one alert for a while (G-26, #30).

            Same isolation as `_acknowledge`, and the stakes are higher: that
            one suppresses the escalation of an alert already delivered, this
            one suppresses the delivery. The duration is not validated here -
            `snooze_alert` clamps it and refuses a non-duration, because a form
            field is a POST body and a POST body is whatever the sender
            types."""
            email = self._session_email()
            if email is None:
                self._send(_render_login().encode())
                return
            try:
                alert_id = int((form.get("alert_id") or [""])[0])
            except (TypeError, ValueError):
                self._send(_render_not_found().encode(), 404)
                return
            try:
                hours = float((form.get("hours") or ["0"])[0])
            except (TypeError, ValueError):
                hours = 0.0

            account_ids = [r["id"] for r in member_accounts(db, email)]
            alert = _member_alert(db, alert_id, account_ids)
            if alert is None:
                self._send(_render_not_found().encode(), 404)
                return

            snooze_alert(db, alert["account_id"], alert["dedup_key"], alert["rule"],
                         hours, requested_by=normalize_email(email),
                         severity=alert["severity"], rules=rules)
            self._redirect(f"/account?id={alert['account_id']}")

        def _replace_key(self, form):
            """Install a customer-supplied replacement monitoring key (#69).

            The portal's first write that accepts a secret, so the ordering
            here is deliberate: session first, then scope, then the validation
            that costs exchange calls. Somebody without a session must not be
            able to make this server talk to an exchange at all.

            The submitted values are never logged, never rendered back, and
            never put in an event payload - `keyreplacement` holds that rule
            too, and it is repeated here because this is where they arrive."""
            email = self._session_email()
            if email is None:
                self._send(_render_login().encode())
                return
            try:
                account_id = int((form.get("account_id") or [""])[0])
            except (TypeError, ValueError):
                self._send(_render_not_found().encode(), 404)
                return

            # Scope before anything else. `_render_replace_key` returning None
            # is the same isolation boundary every other account page uses, so
            # an account the member cannot see is indistinguishable from one
            # that does not exist.
            if _render_replace_key(db, email, account_id) is None:
                self._send(_render_not_found().encode(), 404)
                return

            if cipher is None:
                # Without a cipher there is nowhere safe to put the key. Same
                # refusal the two-factor routes make, and for the same reason.
                page = _render_replace_key(
                    db, email, account_id,
                    error="Key replacement is not available on this server. Contact Minas.")
                self._send(page.encode(), 503)
                return

            from sentinel.keyreplacement import replace_monitoring_key

            outcome = replace_monitoring_key(
                db, cipher,
                account_id=account_id,
                api_key=(form.get("api_key") or [""])[0],
                secret=(form.get("secret") or [""])[0],
                passphrase=(form.get("passphrase") or [""])[0],
                actor=normalize_email(email),
                operator_alerter=operator_alerter,
            )
            if outcome.ok:
                logger.info("portal: monitoring key replaced on account %d by %s", account_id, email)
            else:
                # The reason code, not the detail: the detail names what the
                # exchange said, and this line ends up in a log an operator
                # reads over somebody's shoulder.
                logger.warning("portal: key replacement refused on account %d for %s (%s)",
                               account_id, email, outcome.reason_code)
            page = _render_replace_key(
                db, email, account_id,
                error=None if outcome.ok else outcome.message,
                notice=outcome.message if outcome.ok else None,
            )
            self._send(page.encode(), 200 if outcome.ok else 400)

        def do_POST(self):
            path = urlparse(self.path).path.rstrip("/")
            if not _same_origin(self):
                self._send(b"cross-origin POST rejected", 403)
                return
            length = int(self.headers.get("Content-Length", 0))
            if length < 0 or length > 8 * 1024:
                self._send(_render_login(error="Invalid request").encode(), 400)
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8")) if length else {}

            # Every route, in one table, classified in exactly one of
            # PRIMARY_ONLY / NON_PRIMARY_ALLOWED (#25). Add a route here and
            # tests/test_member_roles.py fails until it is classified.
            routes = {
                "/login": self._login,
                "/login-code": self._login_code,
                "/forgot": self._forgot,
                "/reset": self._reset,
                "/logout": self._logout,
                "/security/2fa/start": lambda form: self._twofactor(path, form),
                "/security/2fa/confirm": lambda form: self._twofactor(path, form),
                "/security/2fa/disable": lambda form: self._twofactor(path, form),
                "/telegram/link": lambda form: self._telegram_link(path),
                "/telegram/unlink": lambda form: self._telegram_link(path),
                "/digest/frequency": self._digest_frequency,
                "/preferences": self._preferences,
                "/drill": lambda form: self._drill(),
                "/erase": self._erase,
                "/pause/request": lambda form: self._pause(path, form),
                "/pause/cancel": lambda form: self._pause(path, form),
                "/acknowledge": self._acknowledge,
                "/snooze": self._snooze,
                "/expected": self._expected,
                "/key": self._replace_key,
            }
            assert set(routes) == POST_ROUTES, "POST_ROUTES must mirror the dispatch table"
            handler = routes.get(path)
            if handler is None:
                self._send(_render_not_found().encode(), 404)
                return
            if path in PRIMARY_ONLY and not self._may_change(path, form):
                return
            handler(form)

        def _may_change(self, path, form) -> bool:
            """The #25 gate: True when the signed-in login is the primary
            contact for what this route changes; otherwise the refusal page
            has been sent and the handler must not run.

            No session at all is the handler's own business (it renders the
            login page). A login that is primary nowhere is refused every
            primary-only route, whatever it names - including an account id
            that does not exist, so the refusal cannot be used to probe.
            An account-scoped route names its target; the login must be
            primary on THAT account. A member-scoped route needs the login
            to be primary somewhere."""
            email = self._session_email()
            if email is None:
                return True
            if not primary_anywhere(db, email):
                self._refuse(email)
                return False
            scoped = ACCOUNT_SCOPED.get(path)
            if scoped is None:
                return True
            target = self._post_target(email, form, *scoped)
            if target is None:
                # Unresolvable or outside the login's scope: the handler's
                # own not-found is the right answer for a primary; the gate
                # has nothing to add.
                return True
            role = member_role(db, email, target)
            if role == "non-primary":
                self._refuse(email)
                return False
            # 'primary' proceeds; None (outside the login's scope) is the
            # handler's not-found, exactly as before this gate existed.
            return True

        def _post_target(self, email, form, field, kind):
            try:
                ident = int((form.get(field) or [""])[0])
            except (TypeError, ValueError):
                return None
            if kind == "account":
                return ident
            account_ids = [r["id"] for r in member_accounts(db, email)]
            alert = _member_alert(db, ident, account_ids)
            return None if alert is None else int(alert["account_id"])

        def _refuse(self, email):
            logger.info("portal: non-primary contact %s refused a primary-only change", email)
            self._send(_render_refused(email).encode(), 403)

        def _logout(self, form):
            self._redirect("/", [(
                "Set-Cookie",
                f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax",
            )])

        def _login(self, form):
            email = normalize_email((form.get("email", [""])[0] or ""))
            password = form.get("password", [""])[0] or ""
            ip = self._client_ip()
            if throttle.blocked(_throttle_pair(ip, email)) or throttle.blocked(ip):
                logger.warning("portal login throttled for %s from %s", email or "<empty>", ip)
                self._send(_render_login(error="Too many attempts — try again later.").encode(), 429)
                return
            if email and password and check_member_login(db, email, password):
                throttle.reset(_throttle_pair(ip, email))
                if member_login_requires_code(db, email):
                    # No cookie. A correct password buys a pending token and
                    # nothing else - if a session were issued here the second
                    # factor would be decoration (#32).
                    logger.info("portal login step 1 ok for %s from %s, awaiting code", email, ip)
                    self._send(_render_code_prompt(make_pending_token(secret, email)).encode())
                    return
                with db.cursor() as cur:
                    cur.execute("SELECT session_epoch FROM portal_members WHERE email = ?", (email,))
                    row = cur.fetchone()
                token = make_session_token(secret, email,
                                           epoch=row["session_epoch"] if row else 1)
                logger.info("portal login ok for %s from %s", email, ip)
                self._redirect("/", [(
                    "Set-Cookie",
                    f"{SESSION_COOKIE}={token}; Max-Age={SESSION_TTL_SECONDS}; Path=/; HttpOnly; Secure; SameSite=Lax",
                )])
                return
            throttle.record_failure(_throttle_pair(ip, email))
            throttle.record_failure(ip)
            logger.warning("portal login failed for %s from %s", email or "<empty>", ip)
            # Identical message whether the email exists or not.
            self._send(_render_login(error="Invalid email or password.").encode(), 200)

        def log_message(self, *args):  # keep stdout JSON-clean
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="portal").start()
    return server
