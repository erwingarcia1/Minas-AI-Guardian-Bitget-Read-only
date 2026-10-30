"""The store reviewer's login (decision 28, the owner 2026-10-30: option D).

Google reviews an app before production and wants sign-in details it can use
again and again with no one-time code: its reviewers run no authenticator.
The app requires two-factor at every sign-in (decision 21c) - a phone holds
its sign-in for weeks, and a stolen sign-in can acknowledge an alert, which
stops CRITICAL escalation - so no real login can be handed over.

The answer is ONE review login whose second factor is a fixed six-digit code,
and a login that opens nothing but a demo account:

- **The demo account is invented.** No exchange key, so nothing ever polls
  it (`status = 'disabled'`); no contact address, so no alert, digest,
  escalation or note ever goes anywhere; its alerts are already escalated,
  so the escalator skips them; and it says "Demo account" on every screen.
  It is never a customer's and never the drill account (decision 2's drill
  account fires real messages).
- **The fixed code is boxed in.** It works only for the app's sign-in
  (`POST /api/v1/session`), only for the login that carries it, and only
  while that login opens the demo account and nothing else - checked at
  every sign-in, not only when the code was set. The login cannot sign in
  on the website, ask for a reset link, or set up two-factor of its own;
  it is not the demo account's primary contact, so every change a primary
  makes is refused to it.
- **The credentials never touch the repository** (it is public). `sentinel
  review-login create` makes them and prints them once; the owner types them
  into Play Console's App access form.
- **Every use is visible and revocable.** Each sign-in is an activity entry
  on the demo account, a new-device email to the login's own address, and a
  line on the ops dashboard; `rotate` replaces the password and the code and
  signs every phone out, and `disable` removes the login.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sentinel.db import Database

logger = logging.getLogger("sentinel.review")

DEMO_LABEL = "demo-account"
# The name every screen that shows the account carries - the app's home
# shows a paused account's name and badge and not its reason, so the name
# says it too.
DEMO_NAME = "Demo account (sample data)"
# What every screen says about it, in the reason a member reads.
DEMO_REASON = "this is a demo account with sample data - Minas is not watching an exchange account here"
CODE_DIGITS = 6
PASSWORD_BYTES = 18


class ReviewLoginRefused(ValueError):
    """A review login that cannot be made, with the reason for the operator."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(email: str | None) -> str:
    return (email or "").strip().lower()


def _code_hash(code: str) -> str:
    return hashlib.sha256((code or "").strip().encode()).hexdigest()


# --- the demo account -------------------------------------------------------------

def demo_account_id(db: Database) -> int | None:
    with db.cursor() as cur:
        cur.execute("SELECT id FROM accounts WHERE is_demo = 1 ORDER BY id LIMIT 1")
        row = cur.fetchone()
    return int(row["id"]) if row else None


def ensure_demo_account(db: Database, actor: str | None = None) -> int:
    """The demo account, made once with its sample history. Idempotent."""
    existing = demo_account_id(db)
    if existing is not None:
        return existing
    now = _now()

    def ago(**delta) -> str:
        return (now - timedelta(**delta)).isoformat()

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, customer_name, member_name, alert_email, status, is_demo, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', '', '', '', ?, ?, NULL, 'disabled', 1, ?, ?)",
            (DEMO_LABEL, DEMO_NAME, DEMO_NAME, ago(days=40), now.isoformat()))
        account_id = int(cur.lastrowid)
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
        for coin, quantity, when in (("BTC", "1.20000000", ago(days=6, minutes=2)),
                                     ("USDT", "2500.00", ago(days=6, minutes=2)),
                                     ("BTC", "0.35000000", ago(days=6)),
                                     ("USDT", "2500.00", ago(days=6))):
            cur.execute("INSERT INTO balance_snapshots (account_id, coin, quantity, snapshot_at) "
                        "VALUES (?, ?, ?, ?)", (account_id, coin, quantity, when))
        # Already escalated, so the escalator never picks them up; the
        # account has no contact, so nothing would reach anyone anyway.
        samples = (
            ("withdrawal_to_unseen_address", "CRITICAL",
             "Sample alert: a withdrawal of 0.85 BTC to an address this account has never used.",
             {"coin": "BTC", "amount": "0.85", "address": "bc1q-sample-address-not-real", "sample": True},
             ago(days=6), ago(days=6, minutes=-4)),
            ("account_permission_state_changed", "HIGH",
             "Sample alert: the API permissions on this account changed.",
             {"before": ["read"], "after": ["read", "trade"], "sample": True},
             ago(days=2), None),
        )
        for rule, severity, message, evidence, sent_at, acknowledged_at in samples:
            cur.execute(
                "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, confidence, "
                "delivered_to, sent_at, acknowledged_at, acknowledged_by, escalated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'high', 1, ?, ?, ?, ?)",
                (account_id, rule, severity, f"demo:{rule}", message, json.dumps(evidence), sent_at,
                 acknowledged_at, "the demo member" if acknowledged_at else None, sent_at))
    from sentinel.events import append_event

    append_event(db, account_id, "customer_added", "LOW",
                 {"label": DEMO_LABEL, "exchange": "bitget", "topology": "independent", "demo": True},
                 actor=actor)
    logger.info("demo account %d created for the store review login", account_id)
    return account_id


# --- the login ----------------------------------------------------------------------

def review_login(db: Database) -> str | None:
    """The review login's address, if one exists."""
    with db.cursor() as cur:
        cur.execute("SELECT email FROM portal_members WHERE review_code_hash IS NOT NULL ORDER BY id LIMIT 1")
        row = cur.fetchone()
    return row["email"] if row else None


def is_review_login(db: Database, email: str) -> bool:
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM portal_members WHERE email = ? AND review_code_hash IS NOT NULL",
                    (_norm(email),))
        return cur.fetchone() is not None


def opens_only_the_demo(db: Database, email: str) -> bool:
    """True when the login is scoped and opens at least one account, every
    one of them a demo account. Checked at every sign-in."""
    from sentinel.portal import is_scoped, member_accounts

    if not is_scoped(db, email):
        return False
    ids = [int(r["id"]) for r in member_accounts(db, email)]
    if not ids:
        return False
    with db.cursor() as cur:
        marks = ",".join("?" for _ in ids)
        cur.execute(f"SELECT count(*) AS n FROM accounts WHERE id IN ({marks}) AND is_demo = 1", ids)
        return int(cur.fetchone()["n"]) == len(ids)


def check_review_code(db: Database, email: str, code: str) -> bool:
    """The fixed code, for this login, while it opens only the demo."""
    email = _norm(email)
    with db.cursor() as cur:
        cur.execute("SELECT review_code_hash FROM portal_members WHERE email = ?", (email,))
        row = cur.fetchone()
    if row is None or not row["review_code_hash"]:
        return False
    if not hmac.compare_digest(_code_hash(code), row["review_code_hash"]):
        return False
    if not opens_only_the_demo(db, email):
        logger.error("review login %s offered its code but opens more than the demo account - refused", email)
        return False
    return True


def _revoke_phones(db: Database, email: str) -> int:
    """Sign out every phone this login holds, each recorded as the portal's
    "Sign out this phone" is. (A bumped session epoch already refuses them;
    this makes the device list and the activity page say so.)"""
    from sentinel.api import revoke_device

    with db.cursor() as cur:
        cur.execute("SELECT id FROM api_devices WHERE email = ? AND revoked_at IS NULL", (email,))
        ids = [int(r["id"]) for r in cur.fetchall()]
    return sum(1 for device_id in ids if revoke_device(db, email, device_id, "revoked"))


def _new_credentials() -> tuple[str, str]:
    password = secrets.token_urlsafe(PASSWORD_BYTES)
    code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
    return password, code


def create_review_login(db: Database, email: str, *, actor: str | None = None) -> tuple[str, str]:
    """Make the review login and the demo account it opens. Returns
    (password, code) - the only time either exists in plain text."""
    from sentinel.passwords import hash_password
    from sentinel.portal import grant_account

    email = _norm(email)
    if not email or "@" not in email:
        raise ReviewLoginRefused("give the review login an email address Minas owns")
    if review_login(db) is not None:
        raise ReviewLoginRefused("a review login exists already - rotate or disable it")
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM portal_members WHERE email = ?", (email,))
        if cur.fetchone() is not None:
            raise ReviewLoginRefused("that address already has a login - use an address of its own")
        cur.execute("SELECT 1 FROM accounts WHERE lower(alert_email) = ? OR lower(alert_email_secondary) = ?",
                    (email, email))
        if cur.fetchone() is not None:
            raise ReviewLoginRefused("that address is a contact on an account - use an address of its own")
    account_id = ensure_demo_account(db, actor)
    password, code = _new_credentials()
    now = _now().isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO portal_members (email, password_hash, created_at, updated_at, scoped, review_code_hash) "
            "VALUES (?, ?, ?, ?, 1, ?)", (email, hash_password(password), now, now, _code_hash(code)))
    grant_account(db, email, account_id, actor)
    logger.warning("review login %s created by %s", email, actor or "an unattributed operator")
    return password, code


def rotate_review_login(db: Database, *, actor: str | None = None) -> tuple[str, str]:
    """A new password and code, and every phone signed in with the old ones
    signed out."""
    from sentinel.passwords import hash_password

    email = review_login(db)
    if email is None:
        raise ReviewLoginRefused("there is no review login - create one")
    password, code = _new_credentials()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE portal_members SET password_hash = ?, review_code_hash = ?, session_epoch = session_epoch + 1, "
            "updated_at = ? WHERE email = ?", (hash_password(password), _code_hash(code), _now().isoformat(), email))
    _revoke_phones(db, email)
    logger.warning("review login %s rotated by %s", email, actor or "an unattributed operator")
    return password, code


def disable_review_login(db: Database, *, actor: str | None = None) -> str:
    """Remove the review login, its grant and its phones. The demo account
    stays, with its history."""
    email = review_login(db)
    if email is None:
        raise ReviewLoginRefused("there is no review login")
    _revoke_phones(db, email)
    with db.cursor() as cur:
        cur.execute("DELETE FROM member_account_grants WHERE email = ?", (email,))
        cur.execute("DELETE FROM portal_members WHERE email = ?", (email,))
    logger.warning("review login %s disabled by %s", email, actor or "an unattributed operator")
    return email


def review_sign_ins(db: Database) -> dict | None:
    """For the dashboard: the login, how many app sign-ins it has made, the
    last one, and its phones still signed in. None without a login."""
    email = review_login(db)
    if email is None:
        return None
    account_id = demo_account_id(db)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n, max(created_at) AS last FROM events WHERE account_id = ? "
                    "AND event_type = 'member_app_signed_in'", (account_id,))
        row = cur.fetchone()
        cur.execute("SELECT count(*) AS n FROM api_devices WHERE email = ? AND revoked_at IS NULL", (email,))
        live = int(cur.fetchone()["n"])
    return {"email": email, "sign_ins": int(row["n"] or 0), "last": row["last"], "phones": live}
