"""The JSON API the iOS and Android apps run on (decision 21).

Served by the member portal's process under `/api/v1/`, for MEMBERS only
(21a): nothing here can add a customer, read another customer's contacts or
change a threshold, the same line decision 20 drew for the portal.

Sign-in (21c) is the web's sign-in - email, password and the member's TOTP,
counted on the portal's own LoginThrottle - with one difference: the app
requires two-factor to be on. A phone holds this sign-in for weeks, not for
the portal's twelve hours, and a stolen session can acknowledge an alert,
which stops CRITICAL escalation.

What the app holds:

- an ACCESS token, valid 15 minutes, stateless and HMAC-signed like the
  portal's cookie but under its own key. Every request still reads the device
  row, so a revoked device or a password change takes effect on the next
  request rather than when the token runs out;
- a REFRESH token, one per device, 32 random bytes held here only as a hash.
  Every use replaces it. Presenting the one it replaced means two parties hold
  the device's credential, and the device is revoked on the spot.

Both travel as `Authorization: Bearer`, never as a cookie. That is why this
path sits outside the portal's CSRF gate instead of through it: a browser
attaches cookies to a cross-site request on its own, and never a bearer
token. The API does not read the session cookie at all.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sentinel.db import Database

logger = logging.getLogger("sentinel.api")

API_PREFIX = "/api/v1"
ACCESS_TTL_SECONDS = 15 * 60
# Slides: every refresh issues a new token with a fresh 30 days, so an app in
# use stays signed in and one left unopened for a month signs out.
REFRESH_TTL_SECONDS = 30 * 24 * 3600
DEVICE_NAME_MAX = 64
UNNAMED_DEVICE = "Phone app"
MAX_BODY_BYTES = 8 * 1024

# Why a sign-in ended, in the words the activity page shows the member.
REVOKE_REASONS = {
    "signed_out": "signed out in the app",
    "revoked": "removed from the Security page",
    "refresh_reused": "its sign-in was used from two places at once, so Minas ended it",
}

# --- the route registry ------------------------------------------------------
#
# The portal's #25 classification, extended to methods and to reads. Every
# route sits in exactly one bucket and the dispatch table must name the same
# routes (tests/test_api_routes.py), so a new route breaks the build until
# somebody decides who may use it. An allowlist, never a denylist.
#
# There is no PRIMARY_ONLY bucket yet: v1 changes nothing a primary contact
# alone may change (21d). v2's routes bring it, with the portal's gate.

# No session to judge yet: signing in, and trading a refresh token.
PRE_AUTH = frozenset({
    ("POST", "/api/v1/session"),
    ("POST", "/api/v1/session/refresh"),
})

# What any signed-in contact may read, primary or not (#25: a non-primary
# may READ).
MEMBER_READ = frozenset({
    ("GET", "/api/v1/security"),
})

# Writes any signed-in contact may make: ending their own sign-ins.
NON_PRIMARY_ALLOWED = frozenset({
    ("DELETE", "/api/v1/session"),
    ("DELETE", "/api/v1/devices/{id}"),
})

ROUTES = PRE_AUTH | MEMBER_READ | NON_PRIMARY_ALLOWED


def _clock() -> float:
    """Indirected so a test can move the API's time without moving the rest
    of the process's."""
    return time.time()


def _iso(ts: float) -> str:
    # Fixed precision, so the stored strings compare in time order.
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="microseconds")


def _hash(token: str) -> str:
    """SHA-256, for the reason `portal._token_hash` gives: 32 random bytes
    have no dictionary to attack, and the hash exists so a database read does
    not yield a working credential."""
    return hashlib.sha256(token.encode()).hexdigest()


# --- access tokens -------------------------------------------------------------

def access_key(secret: bytes) -> bytes:
    """The access-token key, derived from the portal's signing secret under
    its own context string. The cookie and the access token are signed with
    different keys, so neither can ever be presented as the other."""
    return hmac.new(secret, b"minas-api-access-v1", hashlib.sha256).digest()


def make_access_token(key: bytes, device_id: int, now: float | None = None) -> str:
    now_v = _clock() if now is None else now
    payload = f"{int(device_id)}.{int(now_v + ACCESS_TTL_SECONDS)}"
    sig = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_access_token(key: bytes, token: str, now: float | None = None) -> int | None:
    """The device id a valid, unexpired token names, or None."""
    try:
        device_s, expires_s, sig = token.split(".")
        expected = hmac.new(key, f"{device_s}.{expires_s}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        now_v = _clock() if now is None else now
        if int(expires_s) <= now_v:
            return None
        return int(device_s)
    except (ValueError, TypeError):
        return None


# --- devices -------------------------------------------------------------------

def device_name(raw) -> str:
    """What the app says it is, made safe to show: printable, single-spaced,
    capped. Absent or unusable becomes a plain label rather than a blank row
    on the member's device list."""
    if not isinstance(raw, str):
        return UNNAMED_DEVICE
    cleaned = " ".join("".join(ch for ch in raw if ch.isprintable()).split())
    return cleaned[:DEVICE_NAME_MAX] or UNNAMED_DEVICE


def create_device(db: Database, email: str, name: str, epoch: int) -> tuple[int, str]:
    """A new signed-in device. Returns (device id, refresh token); the token
    itself exists only in this return value and in the app."""
    refresh = secrets.token_urlsafe(32)
    now = _clock()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO api_devices (email, name, session_epoch, refresh_hash, refresh_expires_at, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (email, name, int(epoch), _hash(refresh), _iso(now + REFRESH_TTL_SECONDS), _iso(now)),
        )
        return int(cur.lastrowid), refresh


def authenticate(db: Database, key: bytes, authorization: str | None):
    """The device row an `Authorization: Bearer` header proves, or None.

    Read-only on purpose: GET routes run through here, and no GET may write
    (tests/test_get_is_read_only.py, and its API twin). So the device's
    last-seen time moves on refresh, never on a read."""
    scheme, _, token = (authorization or "").strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    device_id = verify_access_token(key, token.strip())
    if device_id is None:
        return None
    with db.cursor() as cur:
        cur.execute(
            "SELECT d.*, m.session_epoch AS member_epoch FROM api_devices d "
            "JOIN portal_members m ON m.email = d.email WHERE d.id = ?",
            (device_id,),
        )
        row = cur.fetchone()
    if row is None or row["revoked_at"] is not None or row["member_epoch"] != row["session_epoch"]:
        return None
    return row


def rotate_refresh(db: Database, presented: str) -> tuple[int, str] | None:
    """Trade a refresh token for a new one. Returns (device id, new token),
    or None when the token is unknown, expired, revoked or from before a
    password change.

    Presenting the token that the current one REPLACED revokes the device.
    Either the app's copy or somebody else's was used first, and from here
    there is no telling which; ending the sign-in costs the real member one
    sign-in, and leaving it costs them their alerts."""
    if not presented:
        return None
    digest = _hash(presented)
    now = _clock()
    with db.cursor() as cur:
        cur.execute(
            "SELECT d.id, d.session_epoch, d.refresh_expires_at, d.revoked_at, "
            "m.session_epoch AS member_epoch FROM api_devices d "
            "LEFT JOIN portal_members m ON m.email = d.email WHERE d.refresh_hash = ?",
            (digest,),
        )
        row = cur.fetchone()
        reused = None
        if row is None:
            cur.execute("SELECT id, email FROM api_devices WHERE previous_refresh_hash = ? "
                        "AND revoked_at IS NULL", (digest,))
            reused = cur.fetchone()
    if row is None:
        if reused is not None:
            logger.warning("api: a replaced refresh token was presented for device %d; revoking it",
                           reused["id"])
            revoke_device(db, reused["email"], int(reused["id"]), "refresh_reused")
        return None
    if (row["revoked_at"] is not None or row["member_epoch"] is None
            or row["member_epoch"] != row["session_epoch"]
            or row["refresh_expires_at"] <= _iso(now)):
        return None
    fresh = secrets.token_urlsafe(32)
    with db.cursor() as cur:
        # Conditional on the hash still being current, so two refreshes racing
        # with one token cannot both succeed.
        cur.execute(
            "UPDATE api_devices SET previous_refresh_hash = refresh_hash, refresh_hash = ?, "
            "refresh_expires_at = ?, last_refreshed_at = ? "
            "WHERE id = ? AND refresh_hash = ? AND revoked_at IS NULL",
            (_hash(fresh), _iso(now + REFRESH_TTL_SECONDS), _iso(now), int(row["id"]), digest),
        )
        if cur.rowcount != 1:
            return None
    return int(row["id"]), fresh


def revoke_device(db: Database, email: str, device_id: int, reason: str) -> bool:
    """End one sign-in. The login's scope is in the UPDATE itself, as it is
    for acknowledging an alert: a device that is not this login's is
    untouched and indistinguishable from one that does not exist."""
    with db.cursor() as cur:
        cur.execute(
            "UPDATE api_devices SET revoked_at = ?, revoked_reason = ? "
            "WHERE id = ? AND email = ? AND revoked_at IS NULL",
            (_iso(_clock()), reason, int(device_id), email),
        )
        revoked = cur.rowcount == 1
        name = None
        if revoked:
            cur.execute("SELECT name FROM api_devices WHERE id = ?", (int(device_id),))
            name = cur.fetchone()["name"]
    if revoked:
        _record(db, email, "member_app_device_revoked", {"device": name, "reason": reason})
    return revoked


def live_devices(db: Database, email: str) -> list:
    """This login's sign-ins that still work, newest first."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT d.id, d.name, d.created_at, d.last_refreshed_at FROM api_devices d "
            "JOIN portal_members m ON m.email = d.email "
            "WHERE d.email = ? AND d.revoked_at IS NULL AND d.session_epoch = m.session_epoch "
            "AND d.refresh_expires_at > ? ORDER BY d.id DESC",
            (email, _iso(_clock())),
        )
        return cur.fetchall()


def _record(db: Database, email: str, event_type: str, payload: dict) -> None:
    """On the member's activity page. Guarded: the sign-in or the revocation
    is the point, and a failed audit write is logged as the error it is."""
    try:
        from sentinel.events import record_member_security_event

        record_member_security_event(db, email, event_type, payload)
    except Exception as exc:  # noqa: BLE001 - the change is the point
        logger.error("could not record %s for %s: %s", event_type, email, exc)


def send_new_device_email(db: Database, address: str, name: str) -> None:
    """Sent on every app sign-in (21c). Not a receipt - a tripwire, the
    password-change notice's reasoning: a sign-in the member did not make is
    the only signal they will get that somebody else has their password and
    their second factor."""
    from sentinel.alerts.base import AlertMessage
    from sentinel.alerts.contact import ContactRouter
    from sentinel.portal import _PlainNotice

    message = AlertMessage(
        account_label="your Minas AI Guardian login",
        rule="app_signed_in",
        severity="LOW",
        confidence="n/a",
        message=(
            f"The Minas AI Guardian app was just signed in to {address} on a device called "
            f"\"{name}\". If this was not you, sign in to the portal, remove the device under "
            f"Security, and change your password immediately."
        ),
        evidence={},
    )
    ContactRouter(db=db).send(
        _PlainNotice(message), "your Minas AI Guardian login", [address], [],
    )


# --- requests ------------------------------------------------------------------

@dataclass
class Request:
    method: str
    path: str
    params: dict
    query: dict
    headers: object
    body: bytes
    client_ip: str

    def json(self) -> dict | None:
        """The body as a JSON object, or None if it is not one."""
        try:
            loaded = json.loads(self.body.decode("utf-8")) if self.body else {}
        except (ValueError, UnicodeDecodeError):
            return None
        return loaded if isinstance(loaded, dict) else None


@dataclass
class Response:
    status: int
    body: dict | None = None
    headers: list = field(default_factory=list)


def error(status: int, code: str, message: str, headers: list | None = None) -> Response:
    return Response(status, {"error": code, "message": message}, headers or [])


def _segments_match(template: str, path: str) -> dict | None:
    """The `{id}` values if `path` fits `template`, else None. An id is ASCII
    digits and short enough for SQLite: `str.isdigit` alone accepts '²'."""
    want, got = template.split("/"), path.split("/")
    if len(want) != len(got):
        return None
    params = {}
    for w, g in zip(want, got, strict=True):
        if w == "{id}":
            if not (g.isascii() and g.isdigit() and len(g) <= 18):
                return None
            params["id"] = int(g)
        elif w != g:
            return None
    return params


def match(method: str, path: str) -> tuple[tuple[str, str] | None, dict, list[str]]:
    """(route, params, allowed methods). The route is None when nothing fits
    this method; `allowed` says whether the path exists under another one."""
    allowed = []
    for route_method, template in sorted(ROUTES):
        params = _segments_match(template, path)
        if params is None:
            continue
        if route_method == method:
            return (route_method, template), params, [route_method]
        allowed.append(route_method)
    return None, {}, allowed


def _string(data: dict, key: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else ""


class Api:
    """One instance per portal server, sharing its signing secret, its login
    throttle and its credential cipher."""

    def __init__(self, db: Database, secret: bytes, throttle, throttle_pair, cipher=None, rules=None):
        self.db = db
        self.key = access_key(secret)
        self.throttle = throttle
        self.throttle_pair = throttle_pair
        self.cipher = cipher
        self.rules = rules
        self.handlers = {
            ("POST", "/api/v1/session"): self._sign_in,
            ("POST", "/api/v1/session/refresh"): self._refresh,
            ("DELETE", "/api/v1/session"): self._sign_out,
            ("DELETE", "/api/v1/devices/{id}"): self._revoke,
            ("GET", "/api/v1/security"): self._security,
        }
        assert set(self.handlers) == ROUTES, "ROUTES must mirror the dispatch table"

    def handle(self, method: str, path: str, headers, body: bytes, client_ip: str,
               query: dict | None = None) -> Response:
        route, params, allowed = match(method, path)
        if route is None:
            if allowed:
                return error(405, "method_not_allowed", "That method is not allowed here.",
                             [("Allow", ", ".join(allowed))])
            return error(404, "not_found", "There is no such API route.")
        request = Request(method, path, params, query or {}, headers, body, client_ip)
        if route in PRE_AUTH:
            return self.handlers[route](request, None)
        device = authenticate(self.db, self.key, headers.get("Authorization"))
        if device is None:
            return error(401, "unauthorized", "Sign in again.",
                         [("WWW-Authenticate", 'Bearer realm="minas-guardian"')])
        return self.handlers[route](request, device)

    def _tokens(self, device_id: int, refresh: str) -> dict:
        return {
            "token_type": "Bearer",
            "access_token": make_access_token(self.key, device_id),
            "expires_in": ACCESS_TTL_SECONDS,
            "refresh_token": refresh,
            "refresh_expires_in": REFRESH_TTL_SECONDS,
            "device_id": device_id,
        }

    # -- sign-in --------------------------------------------------------------

    def _sign_in(self, request: Request, _device) -> Response:
        from sentinel.portal import check_member_login, member_login_requires_code, normalize_email
        from sentinel.twofactor import MEMBER, check_second_factor

        data = request.json()
        if data is None:
            return error(400, "invalid_json", "The request body must be a JSON object.")
        email = normalize_email(_string(data, "email"))
        password = _string(data, "password")
        code = _string(data, "code").strip()
        ip = request.client_ip
        pair = self.throttle_pair(ip, email)
        if self.throttle.blocked(pair) or self.throttle.blocked(ip):
            logger.warning("api sign-in throttled for %s from %s", email or "<empty>", ip)
            return error(429, "too_many_attempts", "Too many attempts. Try again later.")

        if not (email and password and check_member_login(self.db, email, password)):
            self.throttle.record_failure(pair)
            self.throttle.record_failure(ip)
            logger.warning("api sign-in failed for %s from %s", email or "<empty>", ip)
            return error(401, "invalid_credentials", "That email, password or code was not accepted.")

        if not member_login_requires_code(self.db, email):
            # Only reachable with the right password, so it tells nobody
            # anything the web sign-in would not.
            logger.info("api sign-in refused for %s from %s: two-factor is off", email, ip)
            return error(403, "two_factor_required",
                         "Turn on two-factor sign-in on the portal's Security page, then sign in here.")

        if self.cipher is None or not check_second_factor(self.db, self.cipher, MEMBER, email, code):
            # The same answer as a wrong password: the app has all three
            # fields at once, so there is no step for a correct password to
            # unlock, and no reason to say which one was wrong.
            self.throttle.record_failure(pair)
            self.throttle.record_failure(ip)
            logger.warning("api sign-in code rejected for %s from %s", email, ip)
            return error(401, "invalid_credentials", "That email, password or code was not accepted.")

        self.throttle.reset(pair)
        with self.db.cursor() as cur:
            cur.execute("SELECT session_epoch FROM portal_members WHERE email = ?", (email,))
            epoch = cur.fetchone()["session_epoch"]
        name = device_name(data.get("device_name"))
        device_id, refresh = create_device(self.db, email, name, epoch)
        logger.info("api sign-in for %s from %s: device %d (%s)", email, ip, device_id, name)
        _record(self.db, email, "member_app_signed_in", {"device": name})
        try:
            send_new_device_email(self.db, email, name)
        except Exception as exc:  # noqa: BLE001 - the sign-in has happened; the notice must not undo it
            logger.error("new-device notice to %s failed: %s", email, exc)
        return Response(200, self._tokens(device_id, refresh))

    def _refresh(self, request: Request, _device) -> Response:
        data = request.json()
        if data is None:
            return error(400, "invalid_json", "The request body must be a JSON object.")
        rotated = rotate_refresh(self.db, _string(data, "refresh_token"))
        if rotated is None:
            return error(401, "invalid_refresh_token", "Sign in again.")
        return Response(200, self._tokens(*rotated))

    def _sign_out(self, _request: Request, device) -> Response:
        revoke_device(self.db, device["email"], int(device["id"]), "signed_out")
        return Response(204)

    def _revoke(self, request: Request, device) -> Response:
        if not revoke_device(self.db, device["email"], request.params["id"], "revoked"):
            return error(404, "not_found", "There is no such device.")
        return Response(204)

    # -- reads ------------------------------------------------------------------

    def _security(self, _request: Request, device) -> Response:
        from sentinel.twofactor import MEMBER, is_enabled, remaining_recovery_codes

        email = device["email"]
        return Response(200, {
            "two_factor_enabled": is_enabled(self.db, MEMBER, email),
            "recovery_codes_left": remaining_recovery_codes(self.db, MEMBER, email),
            "devices": [{
                "id": int(d["id"]),
                "name": d["name"],
                "signed_in_at": d["created_at"],
                "last_refreshed_at": d["last_refreshed_at"],
                "current": int(d["id"]) == int(device["id"]),
            } for d in live_devices(self.db, email)],
        })
