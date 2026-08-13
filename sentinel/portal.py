from __future__ import annotations

import base64
import hashlib
import hmac
import html
import logging
import secrets
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from sentinel.dashboard import (
    _LOCAL_TIME_SCRIPT,
    _MARK_SVG,
    _STYLE,
    _account_type_label,
    _status_pill,
    _ts,
)
from sentinel.db import Database
from sentinel.passwords import DUMMY_HASH as _DUMMY_HASH
from sentinel.passwords import LoginThrottle
from sentinel.snooze import SNOOZE_CHOICES, active_snoozes_for_accounts, snooze_alert

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


def set_member_password(db: Database, email: str, password: str) -> None:
    """Create or reset a member login. Called from the ops dashboard only -
    members cannot self-register or self-reset."""
    email = normalize_email(email)
    if not email:
        raise ValueError("member email is empty")
    now = datetime.now(timezone.utc).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO portal_members (email, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(email) DO UPDATE SET password_hash = excluded.password_hash, updated_at = excluded.updated_at",
            (email, hash_password(password), now, now),
        )


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


def make_session_token(secret: bytes, email: str, now: float | None = None) -> str:
    email_b64 = base64.urlsafe_b64encode(normalize_email(email).encode()).decode().rstrip("=")
    expires = int((now if now is not None else time.time()) + SESSION_TTL_SECONDS)
    payload = f"{email_b64}.{expires}"
    sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(secret: bytes, token: str, now: float | None = None) -> str | None:
    """Returns the member email for a valid, unexpired token; None otherwise."""
    try:
        email_b64, expires_s, sig = token.split(".")
        payload = f"{email_b64}.{expires_s}"
        expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(expires_s) < (now if now is not None else time.time()):
            return None
        pad = "=" * (-len(email_b64) % 4)
        return base64.urlsafe_b64decode(email_b64 + pad).decode()
    except (ValueError, TypeError):
        return None




# --- pages -------------------------------------------------------------------

def _page(body_html: str, nav_html: str = "") -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Minas AI Guardian — Member Portal</title><style>" + _STYLE + """
  .loginbox { max-width:420px; margin:60px auto 0; }
  .loginbox .card { padding:8px 0; }
  .portal-tag { font-size:11px; letter-spacing:1.5px; text-transform:uppercase; color:var(--gold); }
</style></head><body><div class="wrap"><header><div class="brand">""" + _MARK_SVG
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
            f"SELECT id, account_id, rule, dedup_key FROM alerts "
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


def _snooze_control(row, snoozed_until: str | None) -> str:
    """A snooze offer, or the silence already in force.

    Shown only while the alert is unacknowledged: once someone has said they
    have seen it, "stop telling me about this" is what acknowledgement already
    means, and offering both invites silencing an alert nobody has looked at
    twice."""
    if snoozed_until:
        return f"<div class='snoozed'>Snoozed until {_ts(snoozed_until)}</div>"
    options = "".join(
        f"<option value='{h}'>{h}h</option>" for h in SNOOZE_CHOICES
    )
    return (
        "<form method='post' action='/snooze' style='margin:6px 0 0'>"
        f"<input type='hidden' name='alert_id' value=\"{int(row['id'])}\">"
        f"<select class='snoozesel' name='hours'>{options}</select>"
        "<button class='ackbtn' type='submit'>Snooze</button></form>"
    )


def _ack_cell(row, snoozed_until: str | None = None) -> str:
    """The acknowledgement column: who and how long if it's done, a button if
    it isn't. One tap, from the row the alert is on."""
    if row["acknowledged_at"]:
        who = html.escape(row["acknowledged_by"] or "—")
        elapsed = _time_to_acknowledge(row["sent_at"], row["acknowledged_at"])
        suffix = f" · {elapsed}" if elapsed else ""
        return f"<td class='ack-yes'>✓ {who}{suffix}</td>"
    return (
        "<td class='ack-no'><form method='post' action='/acknowledge' style='margin:0'>"
        f"<input type='hidden' name='alert_id' value=\"{int(row['id'])}\">"
        "<button class='ackbtn' type='submit'>Acknowledge</button></form>"
        f"{_snooze_control(row, snoozed_until)}</td>"
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
        '<span class="hint">Your access is provided by the Minas team.</span></div>'
        "</form></div></div>"
    )
    return _page(body)


def _render_home(db: Database, email: str) -> str:
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
        f"<td class='rule'>{html.escape(r['rule'])}</td>"
        f"{_ack_cell(r, snoozed.get(r['dedup_key']))}</tr>"
        for r in alerts
    ) or "<tr><td colspan=5 class='empty'>No alerts — all quiet</td></tr>"

    body = (
        '<section><div class="card"><div class="card-head"><h2>Your accounts</h2>'
        f'<span class="count">{len(accounts)} monitored</span></div>'
        "<table><thead><tr><th>Account</th><th>Exchange</th><th>Account type</th>"
        "<th>Status</th><th>Last check</th></tr></thead>"
        f"<tbody>{account_rows}</tbody></table></div></section>"
        '<section><div class="card"><div class="card-head"><h2>Your recent alerts</h2>'
        '<span class="count">latest 30</span></div>'
        "<table><thead><tr><th>Time</th><th>Account</th><th>Severity</th><th>Rule</th>"
        "<th>Acknowledged</th></tr></thead>"
        f"<tbody>{alert_rows}</tbody></table></div></section>"
    )
    nav = (
        f'<span class="live">{html.escape(email)}</span>'
        '<form method="post" action="/logout" style="margin:0">'
        '<button class="live" type="submit" style="cursor:pointer;font-family:inherit">Sign out</button></form>'
    )
    return _page(body, nav)


def _render_member_account(db: Database, email: str, account_id: int) -> str | None:
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
        f"<td class='rule'>{html.escape(r['rule'])}</td>"
        f"{_ack_cell(r, snoozed.get(r['dedup_key']))}</tr>"
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


def _render_not_found() -> str:
    return _page(
        '<section><div class="card"><div class="empty">Not found</div></div></section>',
        '<a class="live" href="/">← Your accounts</a>',
    )


# --- server ------------------------------------------------------------------

def serve_portal(db: Database, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
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
        def _send(self, body: bytes, status: int = 200, extra_headers: list[tuple[str, str]] | None = None):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
            cookie = self.headers.get("Cookie") or ""
            for part in cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name == SESSION_COOKIE and value:
                    return verify_session_token(secret, value)
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
                # Every unauthenticated GET lands on the login page.
                self._send(_render_login().encode())
                return
            if path == "/":
                self._send(_render_home(db, email).encode())
            elif path == "/account":
                qs = parse_qs(urlparse(self.path).query)
                try:
                    aid = int(qs.get("id", ["0"])[0])
                except ValueError:
                    aid = 0
                page = _render_member_account(db, email, aid)
                if page is None:
                    self._send(_render_not_found().encode(), 404)
                else:
                    self._send(page.encode())
            else:
                self._send(_render_not_found().encode(), 404)

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
                         hours, requested_by=normalize_email(email))
            self._redirect(f"/account?id={alert['account_id']}")

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

            if path == "/acknowledge":
                self._acknowledge(form)
                return

            if path == "/snooze":
                self._snooze(form)
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
                token = make_session_token(secret, email)
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
