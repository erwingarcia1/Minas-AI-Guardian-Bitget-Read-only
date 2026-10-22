"""Driving the apps' JSON API (decision 21) against a real portal server."""
from __future__ import annotations

import http.client
import json

from sentinel.portal import set_member_password
from sentinel.totp import STEP_SECONDS, totp_at
from sentinel.twofactor import MEMBER, begin_enrolment, confirm_enrolment
from tests.conftest import PINNED_TOTP_NOW

PASSWORD = "a-long-enough-password"


class ApiClient:
    def __init__(self, port: int):
        self.port = port

    def request(self, method: str, path: str, body=None, token: str | None = None,
                headers: dict | None = None):
        """(status, decoded JSON or None, headers). `body` may be a dict (sent
        as JSON) or raw bytes (sent as they are)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        sent = dict(headers or {})
        payload = None
        if body is not None:
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            sent.setdefault("Content-Type", "application/json")
        if token is not None:
            sent["Authorization"] = f"Bearer {token}"
        conn.request(method, path, body=payload, headers=sent)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            data = raw.decode("utf-8", "replace")
        return resp.status, data, {k.lower(): v for k, v in resp.getheaders()}

    def sign_in(self, email: str, secret: str, *, at: float = PINNED_TOTP_NOW,
                password: str = PASSWORD, device_name: str = "Test phone"):
        return self.request("POST", "/api/v1/session", {
            "email": email, "password": password, "code": totp_at(secret, at),
            "device_name": device_name,
        })


def add_account(db, email: str, label: str = "acct-1", secondary: str | None = None) -> int:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, alert_email_secondary, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, ?, 'active', '2026-01-01', '2026-01-01')",
            (label, email, secondary),
        )
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
    return account_id


def enrolled_member(db, cipher, email: str, *, password: str = PASSWORD) -> str:
    """A member with a login and confirmed two-factor. Returns the TOTP secret.

    Confirmed with the PREVIOUS step's code, so the pinned step is still
    unused for a sign-in: a code is good once (`last_used_step`)."""
    set_member_password(db, email, password, notify=None)
    secret, _ = begin_enrolment(db, cipher, MEMBER, email)
    assert confirm_enrolment(db, cipher, MEMBER, email, totp_at(secret, PINNED_TOTP_NOW - STEP_SECONDS))
    return secret
