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
                        invalidate_sessions: bool = True, notify=None) -> None:
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
        cur.execute(
            "INSERT INTO portal_members (email, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?) "
            f"ON CONFLICT(email) DO UPDATE SET password_hash = excluded.password_hash, {bump}"
            "updated_at = excluded.updated_at",
            (email, hash_password(password), now, now),
        )
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


def member_accounts(db: Database, email: str) -> list:
    """Every account this member is a contact on. THE isolation boundary: all
    portal queries go through the ids returned here.

    Matches the secondary contact as well as the primary (G-21). The second
    pair of eyes is alerted about an account, so it can look that account up -
    and only that account: being someone's secondary contact confers nothing
    over their other accounts, because the match is per-row.

    An empty lookup returns nothing rather than falling through to the query.
    `alert_email_secondary` is '' on accounts saved through a form that left it
    blank, and `lower('') = ''` would otherwise hand a caller with no email
    every account in the fleet."""
    normalized = normalize_email(email)
    if not normalized:
        return []
    with db.cursor() as cur:
        cur.execute(
            "SELECT a.*, p.last_success_at, p.consecutive_failures, p.consecutive_auth_failures "
            "FROM accounts a LEFT JOIN poll_state p ON p.account_id = a.id "
            "WHERE lower(a.alert_email) = ? OR lower(a.alert_email_secondary) = ? "
            "ORDER BY a.label",
            (normalized, normalized),
        )
        return cur.fetchall()


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
                       epoch: int | None = None) -> str:
    """`epoch` is the member's `session_epoch`, baked into the signed payload.

    Sessions are stateless - there is no server-side record to delete - so
    this is the only thing that can make an already-issued cookie stop
    working. Bumping the stored epoch on a password change retires every
    cookie ever issued to that member (#33)."""
    email_b64 = base64.urlsafe_b64encode(normalize_email(email).encode()).decode().rstrip("=")
    expires = int((now if now is not None else time.time()) + SESSION_TTL_SECONDS)
    payload = f"{email_b64}.{expires}.{int(epoch or 1)}"
    sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(secret: bytes, token: str, now: float | None = None,
                         epoch: int | None = None) -> str | None:
    """Returns the member email for a valid, unexpired, current-epoch token.

    A token whose epoch does not match the member's current one was issued
    before a password change and is refused - that is what "resetting signs
    out all other sessions" means when there is no session store."""
    try:
        email_b64, expires_s, epoch_s, sig = token.split(".")
        payload = f"{email_b64}.{expires_s}.{epoch_s}"
        expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(expires_s) < (now if now is not None else time.time()):
            return None
        if epoch is not None and int(epoch_s) != int(epoch):
            return None
        pad = "=" * (-len(email_b64) % 4)
        return base64.urlsafe_b64decode(email_b64 + pad).decode()
    except (ValueError, TypeError):
        return None




# --- pages -------------------------------------------------------------------

# The portal design foundation (#82). Tokens first, primitives second, and
# every screen inherits: a page that needs a new colour or size adds a token
# WITH A ROLE NAME here, never a hex in situ. The system is documented in
# docs/portal-design.md; the decisions it implements (no framework, brand
# the portal not the dashboard, interface over presentation) are recorded on
# #82. Deliberately interface-oriented where the shared ancestor stylesheet
# was presentation-oriented: no animation, no glow, no gradient panels — a
# customer opens this page frightened at 3am, and it is printed as evidence.
# The one decorative element is the gold hairline at the top of the page:
# the portal's signature, cheap to recognise and absent from the ops
# dashboard on purpose.
_PORTAL_STYLE = """
  :root {
    /* -- colour roles ---------------------------------------------------- */
    --bg:#0e0a17; --surface:#161022; --surface-2:#1c1530;
    --line:#2a2140; --line-soft:#221a36;
    --ink:#efeaf7; --ink-muted:#a79bc2; --ink-faint:#6f6390;
    --gold:#f0b429;            /* brand: identity moments only            */
    --accent:#8b5cf6;          /* brand: interactive accents              */
    --ok:#34d399; --warn:#ffa63d; --crit:#ff5d6c;
    --med:#ffd23d; --low:#8a7fa5;
    /* -- type scale ------------------------------------------------------ */
    --t-display:22px; --t-title:15px; --t-body:14px;
    --t-small:12.5px; --t-micro:11px;
    /* -- spacing scale (4px base) ---------------------------------------- */
    --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s5:22px; --s6:32px; --s7:48px;
    --radius:12px; --radius-pill:999px;
  }
  * { box-sizing:border-box; }
  body {
    margin:0; background:var(--bg); color:var(--ink);
    font-family:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    font-size:var(--t-body); line-height:1.55;
    -webkit-font-smoothing:antialiased; min-height:100vh;
    border-top:2px solid var(--gold);   /* the portal signature */
  }
  .wrap { max-width:1080px; margin:0 auto; padding:var(--s6) var(--s6) var(--s7); }
  a { color:var(--accent); }
  :focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:4px; }

  /* -- header / brand ---------------------------------------------------- */
  header { display:flex; align-items:center; justify-content:space-between;
           gap:var(--s4); margin-bottom:var(--s6); }
  .brand { display:flex; align-items:center; gap:var(--s3); }
  .mark { width:38px; height:38px; border-radius:10px; background:var(--surface);
          border:1px solid var(--line); display:grid; place-items:center; }
  .mark svg { width:24px; height:24px; }
  .brand h1 { font-size:17px; font-weight:650; margin:0; letter-spacing:.2px; }
  .brand h1 .minas { color:var(--gold); font-weight:780; letter-spacing:3px; }
  .brand .sub { margin-top:2px; }
  .portal-tag { font-size:var(--t-micro); letter-spacing:1.5px;
                text-transform:uppercase; color:var(--gold); }
  .nav { display:flex; align-items:center; gap:var(--s2); }
  .live { display:inline-flex; align-items:center; gap:var(--s2);
          font-size:var(--t-small); color:var(--ink-muted);
          background:var(--surface); border:1px solid var(--line);
          padding:7px var(--s4); border-radius:var(--radius-pill);
          text-decoration:none; }
  a.live:hover { border-color:var(--accent); color:var(--ink); }
  a.live.primary { color:var(--bg); background:var(--gold);
                   border-color:transparent; font-weight:650; }
  a.live.primary:hover { filter:brightness(1.06); }
  footer { margin-top:var(--s7); font-size:var(--t-small); color:var(--ink-faint); }

  /* -- the banner (one style; tones ok / warn / err) ---------------------- */
  .banner { display:flex; align-items:baseline; gap:var(--s2);
            padding:var(--s3) var(--s4); margin-bottom:var(--s4);
            border-radius:var(--radius); border:1px solid var(--line);
            background:var(--surface); font-size:var(--t-body); }
  .banner.ok   { border-color:rgba(52,211,153,.4);  color:var(--ok); }
  .banner.warn { border-color:rgba(255,166,61,.4);  color:var(--warn); }
  .banner.err  { border-color:rgba(255,93,108,.45); color:var(--crit); }

  /* -- cards and sections ------------------------------------------------- */
  section { margin-top:var(--s5); }
  .card { background:var(--surface); border:1px solid var(--line);
          border-radius:var(--radius); overflow:hidden; }
  .card-head { display:flex; align-items:baseline; justify-content:space-between;
               padding:var(--s4) var(--s5); border-bottom:1px solid var(--line-soft); }
  .card-head h2 { font-size:var(--t-title); font-weight:640; margin:0; letter-spacing:.2px; }
  .card-head .count { font-size:var(--t-small); color:var(--ink-faint);
                      font-variant-numeric:tabular-nums; }
  .cols { display:grid; grid-template-columns:1fr 1fr; gap:var(--s4); }
  @media (max-width:760px) { .cols { grid-template-columns:1fr; } }

  /* -- the table (one style) ---------------------------------------------- */
  table { width:100%; border-collapse:collapse; }
  thead th { font-size:var(--t-micro); text-transform:uppercase; letter-spacing:.7px;
             color:var(--ink-faint); text-align:left; font-weight:600;
             padding:var(--s3) var(--s5); }
  tbody td { padding:var(--s4) var(--s5); font-size:var(--t-body);
             border-top:1px solid var(--line-soft); }
  tbody tr:hover { background:var(--surface-2); }
  td.empty, .empty { color:var(--ink-faint); }

  /* -- the status pill (one style; role modifiers) ------------------------ */
  .pill { display:inline-flex; align-items:center; gap:6px;
          font-size:var(--t-micro); font-weight:600; letter-spacing:.3px;
          padding:4px 11px; border-radius:var(--radius-pill); }
  .pill::before { content:""; width:6px; height:6px; border-radius:50%;
                  background:currentColor; }
  .s-active   { color:var(--ok);        background:rgba(52,211,153,.12); }
  .s-disabled { color:var(--ink-faint); background:rgba(111,99,144,.16); }
  .s-tamper   { color:var(--crit);      background:rgba(255,93,108,.12); }
  .sev-CRITICAL { color:var(--crit); background:rgba(255,93,108,.13); }
  .sev-HIGH     { color:var(--warn); background:rgba(255,166,61,.13); }
  .sev-MEDIUM   { color:var(--med);  background:rgba(255,210,61,.13); }
  .sev-LOW      { color:var(--low);  background:rgba(138,127,165,.15); }

  /* -- check rows (posture and status statements) -------------------------- */
  .checks { padding:var(--s2) 0; }
  .check { display:flex; gap:var(--s3); padding:var(--s3) var(--s5);
           align-items:baseline; }
  .check + .check { border-top:1px solid var(--line-soft); }
  .ic { flex:none; width:20px; height:20px; border-radius:50%; display:grid;
        place-items:center; font-size:var(--t-micro); font-weight:700; }
  .ic.ok   { color:var(--ok);   background:rgba(52,211,153,.12); }
  .ic.warn { color:var(--warn); background:rgba(255,166,61,.14); }
  .ic.crit { color:var(--crit); background:rgba(255,93,108,.13); }
  .check .txt { color:var(--ink-muted); }
  .check .txt b { color:var(--ink); font-weight:620; }

  /* -- text roles ---------------------------------------------------------- */
  .mono, .rule { font-family:"SF Mono","JetBrains Mono",ui-monospace,Menlo,monospace;
                 font-size:var(--t-small); font-variant-numeric:tabular-nums; }
  .mono { color:var(--ink-muted); }
  .lbl { font-weight:550; }
  .meta { font-size:var(--t-small); color:var(--ink-faint); }
  .desc { color:var(--ink-muted); }
  .det { padding:var(--s3) var(--s5); }
  .hint { font-size:var(--t-small); color:var(--ink-faint); }
  .acctlink { color:var(--accent); text-decoration:none; }
  .acctlink:hover { text-decoration:underline; }
  .ack-yes { color:var(--ok); } .ack-no { color:var(--ink-faint); }
  .snoozed { font-size:var(--t-small); color:var(--ink-muted); }

  /* -- definition lists ----------------------------------------------------- */
  .dl { margin:0; padding:var(--s2) var(--s5) var(--s4); }
  .dl dt { font-size:var(--t-micro); text-transform:uppercase; letter-spacing:.7px;
           color:var(--ink-faint); margin-top:var(--s3); }
  .dl dd { margin:var(--s1) 0 0; }

  /* -- forms ---------------------------------------------------------------- */
  .loginbox { max-width:420px; margin:56px auto 0; }
  .loginbox .card, .addf .card { padding-bottom:var(--s2); }
  .addf, .fld { font-size:var(--t-body); }
  .fld { padding:var(--s2) var(--s5) 0; }
  .fld label { display:block; font-size:var(--t-micro); text-transform:uppercase;
               letter-spacing:.7px; color:var(--ink-faint); margin-bottom:var(--s1); }
  .fld input, .fld select {
    width:100%; font:inherit; color:var(--ink); background:var(--surface-2);
    border:1px solid var(--line); border-radius:8px; padding:9px var(--s3); }
  .fld input:focus, .fld select:focus { outline:none; border-color:var(--accent); }
  .actions { padding:var(--s4) var(--s5) var(--s4); }
  .submit { font:inherit; font-weight:650; color:var(--bg); background:var(--gold);
            border:none; border-radius:8px; padding:10px var(--s5); cursor:pointer; }
  .submit:hover { filter:brightness(1.06); }
  .ackbtn { font:inherit; font-size:var(--t-small); cursor:pointer;
            padding:3px 10px; border:1px solid var(--line); border-radius:6px;
            background:transparent; color:var(--ink); }
  .ackbtn:hover { border-color:var(--ok); color:var(--ok); }
  .snoozesel { font:inherit; font-size:var(--t-small); margin-right:6px;
               padding:3px 6px; border:1px solid var(--line); border-radius:6px;
               background:var(--surface-2); color:var(--ink); }

  /* -- print: an incident page is handed to an exchange, an insurer or the
        police, so it has to survive being printed. Export stays the
        browser's own print-to-PDF (decision 5): a PDF library is a new
        dependency in a security product, server-side rendering drifts from
        what the customer actually saw, and the browser's footer carries the
        URL and a timestamp - better provenance than a document we composed
        ourselves. ------------------------------------------------------------ */
  @media print {
    :root { --bg:#fff; --surface:#fff; --surface-2:#fff; --ink:#111;
            --ink-muted:#444; --ink-faint:#666; --line:#bbb; --line-soft:#ddd; }
    body { background:#fff !important; color:#111; border-top:none; }
    .wrap { max-width:none; padding:0; }
    .noprint, .nav form, .ackbtn, .snoozesel { display:none !important; }
    .card { border:1px solid #bbb; break-inside:avoid; }
    a { color:#111; text-decoration:none; }
  }
"""


def _status_pill(status: str) -> str:
    """The portal's own pill - the dashboard has its own. One primitive, role
    modifiers; a new status gets a role class in _PORTAL_STYLE, not an
    inline colour."""
    cls = {"active": "s-active", "disabled": "s-disabled", "tamper_suspected": "s-tamper"}.get(status, "s-disabled")
    return f'<span class="pill {cls}">{html.escape(status.replace("_", " "))}</span>'


def _page(body_html: str, nav_html: str = "") -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Minas AI Guardian — Member Portal</title><style>" + _PORTAL_STYLE
        + '</style></head><body><div class="wrap"><header><div class="brand">' + MARK_SVG
        + '<div><h1><span class="minas">MINAS</span> AI Guardian</h1>'
        '<div class="sub"><span class="portal-tag">Member portal</span></div></div></div>'
        '<div class="nav">' + nav_html + "</div></header>"
        + body_html
        + "<footer>Minas AI Guardian · member portal · your accounts only</footer></div>"
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
        return cur.rowcount == 1


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
        return f"<div class='snoozed'>Snoozed until {_ts(snoozed_until)}</div>"
    choices = snooze_choices(row["severity"], rules)
    if not choices:
        return ""  # a zero cap: this severity cannot be snoozed
    options = "".join(f"<option value='{h}'>{h}h</option>" for h in choices)
    return (
        "<form method='post' action='/snooze' style='margin:6px 0 0'>"
        f"<input type='hidden' name='alert_id' value=\"{int(row['id'])}\">"
        f"<select class='snoozesel' name='hours'>{options}</select>"
        "<button class='ackbtn' type='submit'>Snooze</button></form>"
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
        return "<div class='snoozed'>You marked this as your own activity</div>"
    return (
        "<form method='post' action='/expected' style='margin:6px 0 0'>"
        f"<input type='hidden' name='alert_id' value=\"{int(row['id'])}\">"
        "<input class='snoozesel' type='text' name='note' maxlength='500' "
        "placeholder='Optional note'>"
        "<button class='ackbtn' type='submit'>That was me</button></form>"
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
        return (f"<td class='ack-yes'>✓ {who}{suffix}"
                f"{_expected_control(row, marked)}</td>")
    return (
        "<td class='ack-no'><form method='post' action='/acknowledge' style='margin:0'>"
        f"<input type='hidden' name='alert_id' value=\"{int(row['id'])}\">"
        "<button class='ackbtn' type='submit'>Acknowledge</button></form>"
        f"{_snooze_control(row, snoozed_until, rules)}"
        f"{_expected_control(row, marked)}</td>"
    )


def _render_login(error: str | None = None) -> str:
    banner = f'<div class="banner err">✕ {html.escape(error)}</div>' if error else ""
    body = (
        '<div class="loginbox">' + banner
        + '<div class="card"><div class="card-head"><h2>Member sign-in</h2></div>'
        '<form class="addf" method="post" action="/login">'
        '<div class="fld"><label>Email</label>'
        '<input type="email" name="email" required autocomplete="username"></div>'
        '<div class="fld" style="margin-top:14px"><label>Password</label>'
        '<input type="password" name="password" required autocomplete="current-password"></div>'
        '<div class="actions"><button class="submit" type="submit">Sign in</button>'
        '<span class="hint">Your access is provided by the Minas team. '
        '<a class="acctlink" href="/forgot">Forgotten your password?</a></span></div>'
        "</form></div></div>"
    )
    return _page(body)


def _member_nav(email: str, *, home_link: bool = False) -> str:
    """The signed-in member's navigation, in one place.

    It was duplicated across three pages, which is how a new page ends up
    reachable from some of them and not others - the reader who most needs
    "what are the limits of this thing" is the one who arrived on a page that
    does not link to it.
    """
    return (
        f'<span class="live">{html.escape(email)}</span>'
        + ('<a class="live" href="/">Overview</a>' if home_link else "")
        + '<a class="live" href="/status">System status</a>'
          '<a class="live" href="/alerts">Alert history</a>'
          '<a class="live" href="/activity">Account activity</a>'
          '<a class="live" href="/limits">What we can and cannot do</a>'
          '<a class="live" href="/security">Security</a>'
          '<form method="post" action="/logout" style="margin:0">'
          '<button class="live" type="submit" style="cursor:pointer;font-family:inherit">'
          "Sign out</button></form>"
    )


def _render_home(db: Database, email: str, rules=None) -> str:
    accounts = member_accounts(db, email)
    ids = [r["id"] for r in accounts]
    alerts = []
    if ids:
        marks = ",".join("?" for _ in ids)
        with db.cursor() as cur:
            cur.execute(
                f"SELECT al.id, al.sent_at, al.severity, al.rule, al.acknowledged_at, al.acknowledged_by, al.dedup_key, ac.label "
                f"FROM alerts al JOIN accounts ac ON ac.id = al.account_id "
                f"WHERE al.account_id IN ({marks}) ORDER BY al.sent_at DESC LIMIT 30",
                ids,
            )
            alerts = cur.fetchall()
    snoozed = active_snoozes_for_accounts(db, ids)
    marked = marked_alert_ids(db, ids)

    account_rows = "".join(
        f"<tr><td><a class='acctlink' href='/account?id={r['id']}'>{html.escape(r['label'])} ›</a></td>"
        f"<td class='mono'>{html.escape(r['exchange'] or 'bitget')}</td>"
        f"<td class='mono'>{_account_type_label(r['topology'])}</td>"
        f"<td>{_status_pill(r['status'])}</td>"
        f"<td class='mono'>{_ts(r['last_success_at'])}</td></tr>"
        for r in accounts
    ) or "<tr><td colspan=5 class='empty'>No accounts yet — contact the Minas team</td></tr>"

    alert_rows = "".join(
        f"<tr><td class='mono'>{_ts(r['sent_at'])}</td>"
        f"<td class='lbl'>{html.escape(r['label'])}</td>"
        f"<td><span class='pill sev-{r['severity']}'>{r['severity']}</span></td>"
        f"<td class='rule'><a class='acctlink' href='/incident?id={int(r['id'])}'>"
        f"{html.escape(r['rule'])} ›</a></td>"
        f"{_ack_cell(r, snoozed.get(r['dedup_key']), rules, int(r['id']) in marked)}</tr>"
        for r in alerts
    ) or "<tr><td colspan=5 class='empty'>No alerts — all quiet</td></tr>"

    body = (
        '<section><div class="card"><div class="card-head"><h2>Your accounts</h2>'
        f'<span class="count">{len(accounts)} monitored</span></div>'
        "<table><thead><tr><th>Account</th><th>Exchange</th><th>Account type</th>"
        "<th>Status</th><th>Last check</th></tr></thead>"
        f"<tbody>{account_rows}</tbody></table></div></section>"
        '<section><div class="card"><div class="card-head"><h2>Your recent alerts</h2>'
        '<span class="count">latest 30 · <a class="acctlink" href="/alerts">full history ›</a></span></div>'
        "<table><thead><tr><th>Time</th><th>Account</th><th>Severity</th><th>Rule</th>"
        "<th>Acknowledged</th></tr></thead>"
        f"<tbody>{alert_rows}</tbody></table></div></section>"
    )
    nav = _member_nav(email)
    return _page(body, nav)


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
    live_check = (
        '<div class="check"><span class="ic ok">✓</span><span class="txt"><b>Monitoring live</b> — '
        "polled continuously for fund movement &amp; config drift</span>"
        f'<span class="meta">last check {_ts(last_ok)}</span></div>'
        if fresh and acct["status"] == "active" else
        '<div class="check"><span class="ic warn">!</span><span class="txt"><b>This account is NOT being watched</b> — '
        "movements on it are not currently being detected. Minas has been alerted and is "
        "restoring access; if we asked you for a new API key, that is what this is."
        "</span></div>"
    )
    posture = (
        '<div class="card"><div class="card-head"><h2>Security posture</h2></div><div class="checks">'
        '<div class="check"><span class="ic ok">✓</span><span class="txt"><b>Read-only access</b> — '
        "Minas holds a read-only key; it cannot trade, withdraw, or move your funds</span></div>"
        + live_check +
        '<div class="check"><span class="ic ok">✓</span><span class="txt"><b>Credentials encrypted</b> — '
        "your API key is encrypted at rest and never displayed</span></div>"
        # Offered on every account rather than only on unwatched ones. The
        # customer who needs it most has just revoked every key in a panic, and
        # asking them to first prove the account looks broken - on a page whose
        # freshness check may not have caught up yet - is a hurdle at the worst
        # possible moment (#69).
        '<div class="check"><span class="ic ok">✓</span><span class="txt"><b>Revoked our key?</b> — '
        f'<a class="acctlink" href="/key?id={int(account_id)}">Replace the monitoring key ›</a> '
        "and monitoring restarts within a minute</span></div>"
        "</div></div>"
    )

    event_rows = "".join(
        f"<tr><td class='mono'>{_ts(r['created_at'])}</td>"
        f"<td><span class='pill sev-{r['severity']}'>{r['severity']}</span></td>"
        f"<td class='rule'>{html.escape(r['event_type'])}</td></tr>"
        for r in events
    ) or "<tr><td colspan=3 class='empty'>No events — all quiet</td></tr>"
    alert_rows = "".join(
        f"<tr><td class='mono'>{_ts(r['sent_at'])}</td>"
        f"<td><span class='pill sev-{r['severity']}'>{r['severity']}</span></td>"
        f"<td class='rule'><a class='acctlink' href='/incident?id={int(r['id'])}'>"
        f"{html.escape(r['rule'])} ›</a></td>"
        f"{_ack_cell(r, snoozed.get(r['dedup_key']), rules, int(r['id']) in marked)}</tr>"
        for r in alerts
    ) or "<tr><td colspan=4 class='empty'>No alerts — all quiet</td></tr>"

    body = (
        '<section><div class="cols">'
        '<div class="card"><div class="card-head"><h2>Account</h2></div><dl class="dl">'
        f"<dt>Label</dt><dd>{html.escape(acct['label'])}</dd>"
        f"<dt>Exchange</dt><dd>{html.escape(acct['exchange'] or 'bitget')}</dd>"
        f"<dt>Account type</dt><dd>{_account_type_label(acct['topology'])}</dd>"
        f"<dt>Status</dt><dd>{_status_pill(acct['status'])}</dd>"
        f"<dt>Monitored since</dt><dd>{_ts(acct['onboarded_at'])}</dd>"
        f"<dt>Last check</dt><dd>{_ts(last_ok)}</dd>"
        "</dl></div>"
        + posture +
        "</div></section>"
        '<section><div class="card"><div class="card-head"><h2>Events</h2><span class="count">latest 20</span></div>'
        f"<table><thead><tr><th>Time</th><th>Sev</th><th>Event</th></tr></thead><tbody>{event_rows}</tbody></table></div></section>"
        '<section><div class="card"><div class="card-head"><h2>Alerts</h2><span class="count">latest 20</span></div>'
        f"<table><thead><tr><th>Time</th><th>Sev</th><th>Rule</th><th>Reviewed</th></tr></thead><tbody>{alert_rows}</tbody></table></div></section>"
    )
    nav = '<a class="live" href="/">← Your accounts</a>'
    return _page(body, nav)


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


_OVERALL_COPY = {
    "watching": ("ok", "✓", "Monitoring is running normally",
                 "Every one of your accounts was checked within the last few minutes."),
    "degraded": ("warn", "!", "Some accounts are not being watched",
                 "Monitoring is running, but not every account is being checked. "
                 "The ones affected are listed below."),
    "not_watching": ("warn", "!", "Your accounts are NOT being watched",
                     "Movements on them are not currently being detected. "
                     "Minas has been alerted; what to do is listed below."),
}


def _render_status(db: Database, email: str, rules=None) -> str:
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
    tone, icon, headline, sub = _OVERALL_COPY[status["overall"]]

    rows = "".join(
        "<tr><td class='lbl'>" + html.escape(a["label"]) + "</td>"
        + ("<td><span class='pill sev-LOW'>watched</span></td>" if a["watched"]
           else "<td><span class='pill sev-HIGH'>not watched</span></td>")
        + f"<td class='mono'>{_ts(a['last_success_at'])}</td>"
        + "<td>" + (html.escape(a["reason"]) if a["reason"] else "—") + "</td></tr>"
        for a in status["accounts"]
    ) or "<tr><td colspan=4 class='empty'>No accounts yet — contact the Minas team</td></tr>"

    problems_html = ""
    if status["problems"]:
        items = "".join(
            f'<div class="check"><span class="ic warn">!</span><span class="txt">'
            f'<b>{html.escape(p["label"])}</b> — {html.escape(p["reason"] or "not being watched")}'
            + (f' <a class="acctlink" href="/key?id={p["account_id"]}">Replace the monitoring key ›</a>'
               if p["reason"] and "key is being rejected" in p["reason"] else "")
            + "</span></div>"
            for p in status["problems"]
        )
        problems_html = (
            '<section><div class="card"><div class="card-head"><h2>What is wrong right now</h2>'
            f'<span class="count">{len(status["problems"])} to look at</span></div>'
            f'<div class="checks">{items}</div></div></section>'
        )

    body = (
        '<section><div class="card"><div class="card-head"><h2>System status</h2></div>'
        f'<div class="checks"><div class="check"><span class="ic {tone}">{icon}</span>'
        f'<span class="txt"><b>{headline}</b> — {sub}</span></div></div></div></section>'
        + problems_html +
        '<section><div class="card"><div class="card-head"><h2>Your accounts</h2>'
        f'<span class="count">checked every {status["stale_after_seconds"] // 60 or 1} min or better</span></div>'
        "<table><thead><tr><th>Account</th><th>Watched</th><th>Last successful check</th>"
        "<th>Note</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></section>"
        '<section><div class="card"><div class="card-head"><h2>What this page cannot tell you</h2></div>'
        '<div class="checks"><div class="check"><span class="ic ok">i</span><span class="txt">'
        "This page is served by the Guardian itself, so if it loaded, the Guardian is running. "
        "It cannot tell you the opposite: if the Guardian were completely down this page would "
        "not load at all, and that looks the same as any other connection problem. Treat it as "
        "evidence that monitoring is working, not as the alarm that would tell you it stopped."
        "</span></div></div></div></section>"
    )
    nav = _member_nav(email, home_link=True)
    return _page(body, nav)


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
        ("Your API key (encrypted) and contact details",
         "until your account is deleted"),
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

    def _check(icon_class, icon, title, text):
        return (f'<div class="check"><span class="ic {icon_class}">{icon}</span>'
                f"<span class=\"txt\"><b>{title}</b> — {text}</span></div>")

    does = "".join([
        _check("ok", "✓", "It watches for money leaving",
               "withdrawals, transfers, and balance drops that no trade explains."),
        _check("ok", "✓", "It watches your account's security settings",
               "API permissions and IP allowlists changing are how a takeover starts."),
        _check("ok", "✓", "It tells you, on the channels you have configured",
               "and tells a second contact, and escalates if nobody acknowledges."),
        _check("ok", "✓", "It keeps the record",
               "every alert and every check is stored, so you can reconstruct what "
               "happened and when afterwards."),
    ])

    cannot = "".join([
        _check("warn", "!", "It cannot stop an attack",
               "This is the important one. The Guardian has no power to freeze, "
               "cancel, block or reverse anything on your exchange account. If "
               "somebody is taking your funds, it tells you they are taking your "
               "funds. Acting on that is yours to do, and the speed you do it at "
               "is what decides the outcome."),
        _check("warn", "!", "It cannot see what the exchange does not report",
               "Every check reads the exchange's own API. If the exchange is slow "
               "to show a withdrawal, omits a reference, or is unreachable, the "
               "Guardian is that much later or that much blinder. It says so "
               "rather than guessing — see "
               "<a class=\"acctlink\" href=\"/status\">System status</a>."),
        _check("warn", "!", "It cannot recover money that has gone",
               "There is no reversal. The Guardian shortens the time between a "
               "theft starting and you knowing about it. That is the whole "
               "product."),
    ])

    why = (
        _check("ok", "✓", "The key we hold is read-only, on purpose",
               "It cannot trade, withdraw, or move anything. That means a "
               "compromise of Minas itself still cannot cost you money — we are "
               "not a way in to your funds. "
               "<b>The cost of that guarantee is this page:</b> a key that cannot "
               "move your money also cannot be used to rescue it. We chose the "
               "side that cannot hurt you.")
    )

    timing = (
        _check("ok", "i", "How quickly you hear",
               f"Your balances and recent movements are checked about every "
               f"<b>{fast} seconds</b>; permissions and allowlists about every "
               f"<b>{slow // 60} minutes</b>. An alert is sent as soon as a check "
               "finds something, so in normal conditions you hear within a minute "
               "or so of the exchange showing it. That is measured from when the "
               "<i>exchange</i> reports it, not from when it happened — we cannot "
               "be faster than the source we read.")
    )

    body = (
        '<section><div class="card"><div class="card-head"><h2>What the Guardian does</h2></div>'
        f'<div class="checks">{does}</div></div></section>'
        '<section><div class="card"><div class="card-head"><h2>What it cannot do</h2>'
        '<span class="count">read this one</span></div>'
        f'<div class="checks">{cannot}</div></div></section>'
        '<section><div class="card"><div class="card-head"><h2>Why it is built this way</h2></div>'
        f'<div class="checks">{why}</div></div></section>'
        '<section><div class="card"><div class="card-head"><h2>Timing, honestly</h2></div>'
        f'<div class="checks">{timing}</div></div></section>'
        '<section><div class="card"><div class="card-head"><h2>What we keep, and for how long</h2>'
        '<span class="count">read from the running configuration</span></div>'
        '<div class="checks">'
        + "".join(
            f'<div class="check"><span class="ic ok">i</span><span class="txt">'
            f"<b>{html.escape(what)}</b> — {html.escape(how_long)}</span></div>"
            for what, how_long in retention_summary(rules)
        )
        + '<div class="check"><span class="ic warn">!</span><span class="txt">'
        "<b>These periods are our current practice, not yet reviewed policy.</b> "
        "They are enforced automatically on the schedule shown, but a legal review "
        "of retention obligations is still pending - if it changes these numbers, "
        "this page changes with them, because it reads the same configuration the "
        "deletion job runs on.</span></div>"
        '</div></div></section>'
    )
    return _page(body, _member_nav(email, home_link=True))


def _render_activity(db: Database, email: str) -> str:
    """Who has touched this member's accounts, and when (G-32).

    An empty page says the record is empty, not that nothing happened -
    those are the same sentence to a reader and very different facts, and this
    product has already shipped one bug where an absence was rendered as
    reassurance."""
    from sentinel.events import ADMIN_EVENT_LABELS

    accounts = member_accounts(db, email)
    rows = account_activity(db, [r["id"] for r in accounts])

    def _actor_cell(actor) -> str:
        if actor:
            return f"<td>{html.escape(actor)}</td>"
        # An unattributed row is a finding, not a blank. The same reasoning as
        # the `actor` column on config_changes: a change nobody can be named
        # for is exactly what somebody reading this page is looking for.
        return "<td class='ack-no'>not attributed</td>"

    body_rows = "".join(
        f"<tr><td class='mono'>{_ts(r['created_at'])}</td>"
        f"<td class='lbl'>{html.escape(r['label'])}</td>"
        f"<td>{html.escape(ADMIN_EVENT_LABELS.get(r['event_type'], r['event_type']))}</td>"
        f"<td class='rule'>{_activity_detail(r['event_type'], _payload(r['payload_json']))}</td>"
        f"{_actor_cell(r['actor'])}</tr>"
        for r in rows
    ) or ("<tr><td colspan=5 class='empty'>Nothing recorded — no changes to your "
          "accounts or your login have been made since monitoring began</td></tr>")

    body = (
        '<section><div class="card"><div class="card-head"><h2>Who has touched your accounts</h2>'
        f'<span class="count">{len(rows)} change(s)</span></div>'
        '<div class="det"><span class="desc">Every change to how your accounts are watched or '
        "reached, including the ones Minas makes. Detections are not listed here — they are on "
        '<a class="acctlink" href="/alerts">your alert history</a>. This record is append-only: '
        "entries are never edited or removed, by us or by anybody else.</span></div>"
        "<table><thead><tr><th>Time</th><th>Account</th><th>Change</th><th>Detail</th>"
        "<th>Who</th></tr></thead>"
        f"<tbody>{body_rows}</tbody></table></div></section>"
    )
    return _page(body, '<a class="live" href="/">← Your accounts</a>')


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
            "al.delivered_to, al.acknowledged_at, al.acknowledged_by, ac.label "
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
        '<form method="get" action="/alerts" class="addf" '
        'style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">'
        f'<div class="fld"><label>Account</label><select name="account">{account_options}</select></div>'
        f'<div class="fld"><label>Severity</label><select name="severity">{severity_options}</select></div>'
        f'<div class="fld"><label>From</label><input type="date" name="from" value="{html.escape(f["from"])}"></div>'
        f'<div class="fld"><label>To</label><input type="date" name="to" value="{html.escape(f["to"])}"></div>'
        '<button class="submit" type="submit">Filter</button>'
        f'<a class="acctlink" href="/alerts.csv{"?" + query if query else ""}">Download CSV ↓</a>'
        "</form>"
    )

    def _ack_text(r) -> str:
        if r["acknowledged_at"]:
            who = html.escape(r["acknowledged_by"] or "—")
            return f"<td class='ack-yes'>✓ {who} · {_ts(r['acknowledged_at'])}</td>"
        return "<td class='ack-no'>not acknowledged</td>"

    alert_rows = "".join(
        f"<tr><td class='mono'>{_ts(r['sent_at'])}</td>"
        f"<td class='lbl'>{html.escape(r['label'])}</td>"
        f"<td><span class='pill sev-{r['severity']}'>{r['severity']}</span></td>"
        f"<td class='rule'><a class='acctlink' href='/incident?id={int(r['id'])}'>"
        f"{html.escape(r['rule'])} ›</a></td>"
        f"{_ack_text(r)}</tr>"
        for r in rows
    ) or "<tr><td colspan=5 class='empty'>No alerts match</td></tr>"

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
        pager = ("<div class='det'><span class='desc'>"
                 + " · ".join(parts) + "</span></div>")

    body = (
        '<section><div class="card"><div class="card-head"><h2>Alert history</h2>'
        f'<span class="count">{total} alert(s)</span></div>'
        + filter_form +
        "<table><thead><tr><th>Time</th><th>Account</th><th>Severity</th><th>Rule</th>"
        "<th>Acknowledged</th></tr></thead>"
        f"<tbody>{alert_rows}</tbody></table>{pager}</div></section>"
    )
    return _page(body, '<a class="live" href="/">← Your accounts</a>')


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

    steps = "".join(
        f'<div class="check"><span class="ic {cls}">{"✓" if cls == "ok" else "!"}</span>'
        f'<span class="txt">{text}</span><span class="meta">{when}</span></div>'
        for cls, text, when in timeline
    )

    def _balance_rows(rows, when_label):
        if not rows:
            return f"<tr><td colspan=3 class='empty'>No snapshot {when_label} this alert</td></tr>"
        return "".join(
            f"<tr><td class='lbl'>{html.escape(r['coin'])}</td>"
            f"<td class='mono'>{html.escape(str(r['quantity']))}</td>"
            f"<td class='mono'>{_ts(r['snapshot_at'])}</td></tr>"
            for r in rows
        )

    evidence_rows = "".join(
        f"<tr><td class='lbl'>{html.escape(str(k))}</td>"
        f"<td class='mono'>{html.escape(str(v))}</td></tr>"
        for k, v in sorted(evidence.items())
    ) or "<tr><td colspan=2 class='empty'>No structured evidence recorded</td></tr>"

    body = (
        '<section><div class="card"><div class="card-head">'
        f'<h2>Incident #{int(alert["id"])} — {html.escape(alert["rule"])}</h2>'
        f'<span class="count"><span class="pill sev-{alert["severity"]}">{alert["severity"]}</span>'
        "</span></div>"
        '<dl class="dl">'
        f"<dt>Account</dt><dd>{html.escape(alert['label'])}</dd>"
        f"<dt>Exchange</dt><dd>{html.escape(alert['exchange'] or 'bitget')}</dd>"
        f"<dt>Detected at</dt><dd>{_ts(alert['sent_at'])}</dd>"
        f"<dt>Confidence</dt><dd>{html.escape(alert['confidence'] or '')}</dd>"
        f"<dt>What we saw</dt><dd>{html.escape(alert['message'])}</dd>"
        "</dl></div></section>"

        '<section><div class="card"><div class="card-head"><h2>Timeline</h2>'
        '<span class="count">detection → notification → acknowledgement</span></div>'
        f'<div class="checks">{steps}</div></div></section>'

        '<section><div class="cols">'
        '<div class="card"><div class="card-head"><h2>Balances before</h2></div>'
        "<table><thead><tr><th>Coin</th><th>Quantity</th><th>Snapshot</th></tr></thead>"
        f"<tbody>{_balance_rows(before, 'before')}</tbody></table></div>"
        '<div class="card"><div class="card-head"><h2>Balances after</h2></div>'
        "<table><thead><tr><th>Coin</th><th>Quantity</th><th>Snapshot</th></tr></thead>"
        f"<tbody>{_balance_rows(after, 'after')}</tbody></table></div>"
        "</div></section>"

        '<section><div class="card"><div class="card-head"><h2>Evidence</h2>'
        '<span class="count">as recorded at detection</span></div>'
        f"<table><tbody>{evidence_rows}</tbody></table></div></section>"
    )
    nav = (
        f'<a class="live" href="/account?id={int(alert["account_id"])}">‹ Back to account</a>'
        '<button class="live noprint" type="button" onclick="window.print()" '
        'style="cursor:pointer;font-family:inherit">Print / Save as PDF</button>'
    )
    return _page(body, nav)


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
        inner = f'<div class="det"><span class="desc">{html.escape(notice)}</span></div>'
    else:
        inner = (
            '<form class="addf" method="post" action="/forgot">'
            '<div class="fld"><label>Email</label>'
            '<input type="email" name="email" required autocomplete="email"></div>'
            '<div class="actions"><button class="submit" type="submit">Send reset link</button>'
            '<span class="hint">A single-use link that expires within an hour.</span></div>'
            "</form>"
        )
    return _page(
        '<div class="loginbox"><div class="card">'
        '<div class="card-head"><h2>Reset your password</h2></div>' + inner +
        '<div class="det"><span class="desc">'
        '<a class="acctlink" href="/">‹ Back to sign in</a></span></div>'
        "</div></div>"
    )


def _render_reset_form(token: str, error: str | None = None) -> str:
    banner = f'<div class="banner err">✕ {html.escape(error)}</div>' if error else ""
    return _page(
        '<div class="loginbox">' + banner +
        '<div class="card"><div class="card-head"><h2>Choose a new password</h2></div>'
        '<form class="addf" method="post" action="/reset">'
        f'<input type="hidden" name="token" value="{html.escape(token)}">'
        '<div class="fld"><label>New password</label>'
        '<input type="password" name="password" required autocomplete="new-password" '
        f'minlength="{MIN_PASSWORD_LENGTH}"></div>'
        '<div class="actions"><button class="submit" type="submit">Set password</button>'
        f'<span class="hint">At least {MIN_PASSWORD_LENGTH} characters. '
        "Setting a new password signs out every other device.</span></div>"
        "</form></div></div>"
    )


def _render_code_prompt(pending: str, error: str | None = None) -> str:
    """Step two. The password has been accepted; the session has not been
    issued."""
    banner = f'<div class="banner err">✕ {html.escape(error)}</div>' if error else ""
    return _page(
        '<div class="loginbox">' + banner +
        '<div class="card"><div class="card-head"><h2>Two-factor code</h2></div>'
        '<form class="addf" method="post" action="/login-code">'
        f'<input type="hidden" name="pending" value="{html.escape(pending)}">'
        '<div class="fld"><label>Code from your authenticator app</label>'
        '<input type="text" name="code" required autocomplete="one-time-code" '
        'inputmode="numeric" autofocus></div>'
        '<div class="actions"><button class="submit" type="submit">Sign in</button>'
        '<span class="hint">Or enter one of your recovery codes.</span></div>'
        "</form></div></div>"
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
        banner = f'<div class="banner err">✕ {html.escape(error)}</div>'
    elif notice:
        banner = f'<div class="banner ok">✓ {html.escape(notice)}</div>'

    # Only Bitget and OKX take a passphrase. Showing the field to everyone and
    # explaining it is better than hiding it: a Bybit customer who sees no
    # passphrase box and expects one has no way to tell whether the page is
    # broken or their key is different.
    passphrase_hint = (
        "Required for Bitget and OKX keys; leave empty for Bybit and Binance."
    )

    body = (
        '<section>' + banner +
        '<div class="card"><div class="card-head"><h2>Replace the monitoring key</h2>'
        f'<span class="count">{html.escape(acct["label"])}</span></div>'
        '<div class="checks">'
        '<div class="check"><span class="ic ok">✓</span><span class="txt">'
        "<b>Read-only keys only.</b> A key that can trade or withdraw is refused, not "
        "accepted with a warning. Create the new key with view or read permission only."
        "</span></div>"
        '<div class="check"><span class="ic ok">✓</span><span class="txt">'
        "<b>It must be for this same exchange account.</b> We check the account the key "
        "belongs to before we store it, so a key from a different account is refused."
        "</span></div>"
        '<div class="check"><span class="ic warn">!</span><span class="txt">'
        "<b>Minas will never email you a link to this page.</b> You reached it by signing in, "
        "and that is the only way to reach it. If a message ever asks you to paste an API key, "
        "it is not from us."
        "</span></div>"
        "</div>"
        '<form class="addf" method="post" action="/key" autocomplete="off">'
        f'<input type="hidden" name="account_id" value="{int(account_id)}">'
        f'<div class="fld"><label>New API key ({html.escape(exchange)})</label>'
        '<input type="text" name="api_key" required autocomplete="off" spellcheck="false"></div>'
        '<div class="fld"><label>API secret</label>'
        '<input type="password" name="secret" required autocomplete="off"></div>'
        '<div class="fld"><label>API passphrase</label>'
        '<input type="password" name="passphrase" autocomplete="off"></div>'
        f'<div class="actions"><button class="submit" type="submit">Check and replace</button>'
        f'<span class="hint">{passphrase_hint}</span></div>'
        "</form></div></section>"
    )
    nav = f'<a class="live" href="/account?id={int(account_id)}">← Back to this account</a>'
    return _page(body, nav)


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
        banner = f'<div class="banner err">✕ {html.escape(error)}</div>'
    elif notice:
        banner = f'<div class="banner ok">✓ {html.escape(notice)}</div>'

    if codes:
        body = (
            '<div class="card"><div class="card-head"><h2>Save your recovery codes</h2></div>'
            '<div class="det"><span class="desc">Each code works <b>once</b> and lets you sign '
            "in if you lose your phone. They are stored hashed, so this is the only time they "
            "can be shown — write them down or put them in a password manager now.</span></div>"
            '<div class="det"><span class="desc mono">'
            + "<br>".join(html.escape(c) for c in codes)
            + "</span></div></div>"
        )
    elif secret:
        uri = _twofactor_uri(secret, email)
        body = (
            '<div class="card"><div class="card-head"><h2>Set up two-factor</h2></div>'
            '<div class="det"><span class="desc">Add this to your authenticator app, then enter '
            "a code to switch it on. Two-factor is not active until you do — if the code never "
            "works, nothing has changed and you can still sign in.</span></div>"
            f'<div class="det"><span class="desc mono">{html.escape(secret)}</span></div>'
            f'<div class="det"><span class="desc mono">{html.escape(uri)}</span></div>'
            '<form class="addf" method="post" action="/security/2fa/confirm">'
            '<div class="fld"><label>Code from your app</label>'
            '<input type="text" name="code" required inputmode="numeric" autofocus></div>'
            '<div class="actions"><button class="submit" type="submit">Turn on two-factor</button>'
            "</div></form></div>"
        )
    elif is_enabled(db, _MEMBER, email):
        left = remaining_recovery_codes(db, _MEMBER, email)
        body = (
            '<div class="card"><div class="card-head"><h2>Two-factor is on</h2>'
            f'<span class="count">{left} recovery code(s) left</span></div>'
            '<div class="det"><span class="desc">Turning it off needs a current code from your '
            "app or a recovery code. We will email you when it happens — if you get that email "
            "and it was not you, somebody else is in your account.</span></div>"
            '<form class="addf" method="post" action="/security/2fa/disable">'
            '<div class="fld"><label>Current code</label>'
            '<input type="text" name="code" required inputmode="numeric"></div>'
            '<div class="actions"><button class="submit" type="submit">Turn off two-factor</button>'
            "</div></form></div>"
        )
    else:
        body = (
            '<div class="card"><div class="card-head"><h2>Two-factor is off</h2></div>'
            '<div class="det"><span class="desc">Your Minas login is what protects the alerts '
            "about your exchange accounts. Adding a second factor means a stolen password is "
            "not enough on its own.</span></div>"
            '<form class="addf" method="post" action="/security/2fa/start">'
            '<div class="actions"><button class="submit" type="submit">Set up two-factor</button>'
            "</div></form></div>"
        )
    return _page(f'<section>{banner}{body}</section>',
                 '<a class="live" href="/">‹ Back</a>')


def _twofactor_uri(secret: str, email: str) -> str:
    from sentinel.totp import provisioning_uri

    return provisioning_uri(secret, email)


def _render_not_found() -> str:
    return _page(
        '<section><div class="card"><div class="empty">Not found</div></div></section>',
        '<a class="live" href="/">← Your accounts</a>',
    )


# --- server ------------------------------------------------------------------

def serve_portal(db: Database, port: int, host: str = "127.0.0.1",
                 rules=None, cipher=None, metrics=None,
                 operator_alerter=None) -> ThreadingHTTPServer:
    """Customer-facing member portal.

    Unlike the ops dashboard this one has its own login and is *meant* to be
    reachable by customers — but it still defaults to loopback, because it
    serves plain HTTP and its session cookie would cross the network in the
    clear. Reaching customers is the job of a TLS-terminating proxy in front
    of it, not of this socket. Set `SENTINEL_PORTAL_HOST` to widen it
    deliberately once that proxy exists (G-36)."""
    secret = _session_secret(db)
    throttle = LoginThrottle()

    def _same_origin(handler) -> bool:
        origin = handler.headers.get("Origin")
        if not origin:
            return True
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
            self.send_header("Referrer-Policy", "no-referrer")
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
                return verify_session_token(secret, value, epoch=row["session_epoch"])
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

            if throttle.blocked(email) or throttle.blocked(ip):
                logger.warning("two-factor throttled for %s from %s", email, ip)
                self._send(_render_login(error="Too many attempts — try again later.").encode(), 429)
                return

            if cipher is None or not check_second_factor(db, cipher, TWOFACTOR_MEMBER, email, code):
                # Counted on the same limiter as passwords. Six digits is a
                # million guesses, and an endpoint that accepts them as fast as
                # they arrive is not a second factor.
                throttle.record_failure(email)
                throttle.record_failure(ip)
                logger.warning("two-factor code rejected for %s from %s", email, ip)
                self._send(_render_code_prompt(pending, error="That code was not accepted.").encode())
                return

            throttle.reset(email)
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
            if throttle.blocked(address) or throttle.blocked(ip):
                logger.warning("reset request throttled for %s from %s", address or "<empty>", ip)
                # Same acknowledgement even when throttled: "you are being
                # rate limited" is itself a signal that this address is worth
                # rate limiting.
                self._pad_to(deadline)
                self._send(_render_forgot(notice=_RESET_ACK).encode())
                return
            throttle.record_failure(address)
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
            throttle.reset(address)
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

            if path == "/logout":
                self._redirect("/", [(
                    "Set-Cookie",
                    f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax",
                )])
                return

            if path.startswith("/security/2fa/"):
                self._twofactor(path, form)
                return

            if path == "/login-code":
                self._login_code(form)
                return

            if path == "/forgot":
                self._forgot(form)
                return

            if path == "/reset":
                self._reset(form)
                return

            if path == "/acknowledge":
                self._acknowledge(form)
                return

            if path == "/snooze":
                self._snooze(form)
                return

            if path == "/expected":
                self._expected(form)
                return

            if path == "/key":
                self._replace_key(form)
                return

            if path != "/login":
                self._send(_render_not_found().encode(), 404)
                return

            email = normalize_email((form.get("email", [""])[0] or ""))
            password = form.get("password", [""])[0] or ""
            ip = self._client_ip()
            if throttle.blocked(email) or throttle.blocked(ip):
                logger.warning("portal login throttled for %s from %s", email or "<empty>", ip)
                self._send(_render_login(error="Too many attempts — try again later.").encode(), 429)
                return
            if email and password and check_member_login(db, email, password):
                throttle.reset(email)
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
            throttle.record_failure(email)
            throttle.record_failure(ip)
            logger.warning("portal login failed for %s from %s", email or "<empty>", ip)
            # Identical message whether the email exists or not.
            self._send(_render_login(error="Invalid email or password.").encode(), 200)

        def log_message(self, *args):  # keep stdout JSON-clean
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="portal").start()
    return server
