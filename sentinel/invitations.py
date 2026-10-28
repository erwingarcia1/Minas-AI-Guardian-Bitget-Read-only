"""Invitations: how a Minas member gets a login (decision 17, 2026-10-28).

The owner's answer: sign-up is for Minas members, and an exchange key is
added only by an admin, during onboarding. So the admin onboards the
account, an invitation goes to the contact's mailbox, and the member makes
their own login from it - no password chosen by Minas and handed over by an
unnamed channel, which is how only four of fourteen accounts ever got a
login (docs/AUTONOMOUS-LOG.md, 2026-10-25).

**A code, never a link.** The email carries a code the member types on the
website or in the app. A link would only work in a browser, and these
customers are taught that Minas never emails them a link asking for a
password (decision 16; the sign-in page and the app say so).

**No login until every step is proven.** The code proves the mailbox, the
password is chosen, and two-factor is confirmed against a working code;
only then does `portal_members` get a row. A half-finished invitation
leaves nothing anyone can sign in with.

**It opens what it was issued for.** The login it creates is scoped
(decision 10): it opens the accounts the invitation names, and only those
on which the address is still a contact when the member accepts - a
contact move between the email and the acceptance takes the account off
it (26a).

**Guessing is bounded per invitation, in the database.** Five wrong codes
retire the invitation, whatever the source address and across restarts;
the web's per-source limiter sits in front of that.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sentinel.db import Database
from sentinel.passwords import hash_password

logger = logging.getLogger("sentinel.invitations")

# How long an emailed code stays good. Copy that states it is built from
# this number (`expiry_phrase`), never typed.
INVITATION_TTL_SECONDS = 3 * 24 * 3600

# Wrong codes against one invitation before it is retired. The code has 60
# bits, so five guesses are nothing; the cap is what keeps that true when
# the guesses come from many addresses at once.
INVITATION_MAX_ATTEMPTS = 5

# How long one step of the acceptance may sit in a browser or an app. The
# step token carries the proof that the code was right, so it is measured
# in minutes, like the sign-in's pending token.
STEP_TTL_SECONDS = 15 * 60

# Crockford's base32: no I, L, O or U, so nothing in a code can be misread
# as something else. 12 characters, 60 bits, shown in groups of four.
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 12

# The rules that name these emails (RULE_ADVICE, the subjects).
INVITATION_RULE = "member_invitation"
LOGIN_CREATED_RULE = "member_login_created"


class InvitationRefused(ValueError):
    """An invitation that cannot be issued or completed, with the reason in
    words an operator (issuing) or the member (completing) can act on."""


@dataclass(frozen=True)
class Invitation:
    id: int
    email: str
    code: str
    account_ids: tuple[int, ...]
    expires_at: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(email: str | None) -> str:
    """The portal's normalize_email, here so this module never imports the
    portal (which imports it)."""
    return (email or "").strip().lower()


def expiry_phrase() -> str:
    """How long a code lasts, in words, from INVITATION_TTL_SECONDS."""
    days, rest = divmod(INVITATION_TTL_SECONDS, 24 * 3600)
    if days and not rest:
        return f"{days} day{'s' if days != 1 else ''}"
    hours = INVITATION_TTL_SECONDS // 3600
    return f"{hours} hour{'s' if hours != 1 else ''}"


# --- the code -----------------------------------------------------------------

def generate_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[i:i + 4] for i in range(0, CODE_LENGTH, 4))


def normalize_code(raw: str | None) -> str:
    """What the member typed, as the code it was meant to be: case, spaces
    and dashes ignored, and the letters people read for digits read as
    digits (O for 0; I and L for 1). Anything else outside the alphabet, or
    the wrong length, is no code at all - an empty string."""
    text = (raw or "").upper().replace(" ", "").replace("-", "")
    text = text.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))
    if len(text) != CODE_LENGTH or any(ch not in CODE_ALPHABET for ch in text):
        return ""
    return text


def _code_hash(code: str) -> str:
    """SHA-256, like a reset token's: the code is random, so there is no
    dictionary to slow down, and the hash only stops a database read from
    yielding a working code."""
    return hashlib.sha256(normalize_code(code).encode()).hexdigest()


# --- issuing ------------------------------------------------------------------

def _eligible_account(cur, email: str, account_id: int):
    """The account row when `email` is one of its contacts and it is still
    watched; None otherwise. An invitation must never open an account to an
    address that is not on it."""
    cur.execute(
        "SELECT id, label FROM accounts WHERE id = ? AND erased_at IS NULL "
        "AND (lower(alert_email) = ? OR lower(alert_email_secondary) = ?)",
        (int(account_id), email, email),
    )
    return cur.fetchone()


def issue_invitation(db: Database, email: str, account_id: int, *, actor: str | None) -> Invitation:
    """A fresh invitation for `email` to open `account_id`, and its code -
    the only time the code exists in plain text outside the member's mailbox.

    Refused when the address already has a login (it signs in, or resets its
    password) or is not a contact on the account. An open invitation for the
    same address is retired and its accounts carried into the new one, so a
    member invited from two accounts' pages holds one code that opens both."""
    email = _norm(email)
    if not email:
        raise InvitationRefused("there is no email address to invite")
    now = _now()
    expires = now + timedelta(seconds=INVITATION_TTL_SECONDS)
    code = generate_code()
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM portal_members WHERE email = ?", (email,))
        if cur.fetchone() is not None:
            raise InvitationRefused("this address already has a login - it can sign in, or reset its password")
        if _eligible_account(cur, email, account_id) is None:
            raise InvitationRefused("this address is not a contact on that account")
        cur.execute(
            "SELECT id, account_ids_json FROM member_invitations WHERE email = ? AND used_at IS NULL "
            "AND retired_at IS NULL", (email,))
        carried: list[int] = []
        for row in cur.fetchall():
            carried += [int(a) for a in json.loads(row["account_ids_json"] or "[]")]
        accounts = sorted({int(account_id), *(a for a in carried if _eligible_account(cur, email, a))})
        cur.execute(
            "UPDATE member_invitations SET retired_at = ?, password_hash = NULL "
            "WHERE email = ? AND used_at IS NULL AND retired_at IS NULL", (now.isoformat(), email))
        cur.execute(
            "INSERT INTO member_invitations (email, code_hash, account_ids_json, created_at, created_by, "
            "expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (email, _code_hash(code), json.dumps(accounts), now.isoformat(), actor, expires.isoformat()))
        invitation_id = int(cur.lastrowid)
    from sentinel.events import append_event

    for aid in accounts:
        append_event(db, aid, "member_invitation_sent", "LOW", {"login": email}, actor=actor)
    logger.info("invitation %d issued for %s by %s - opens %d account(s)",
                invitation_id, email, actor or "an unattributed operator", len(accounts))
    return Invitation(invitation_id, email, code, tuple(accounts), expires.isoformat())


def open_invitation(db: Database, email: str):
    """The address's current invitation when it can still be accepted -
    unused, not retired, not expired, guesses left - else None."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM member_invitations WHERE email = ? AND used_at IS NULL AND retired_at IS NULL "
            "AND expires_at > ? AND attempts < ? ORDER BY id DESC LIMIT 1",
            (_norm(email), _now().isoformat(), INVITATION_MAX_ATTEMPTS))
        return cur.fetchone()


# --- accepting ----------------------------------------------------------------

def check_code(db: Database, email: str, code: str) -> int | None:
    """The invitation id when `code` is the address's open invitation's
    code; None otherwise. A wrong code counts against the invitation, and
    the last allowed one retires it. An unknown address, an expired code and
    a wrong one all answer None: which it was is not the caller's business."""
    email = _norm(email)
    row = open_invitation(db, email) if email else None
    if row is None:
        return None
    # Not a code at all - the wrong length, a character outside the
    # alphabet - cannot match, so it costs the member nothing: counting a
    # typo as a guess would retire an invitation nobody was attacking.
    if not normalize_code(code):
        return None
    if hmac.compare_digest(_code_hash(code), row["code_hash"]):
        return int(row["id"])
    now = _now().isoformat()
    with db.cursor() as cur:
        cur.execute("UPDATE member_invitations SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
        cur.execute(
            "UPDATE member_invitations SET retired_at = ?, password_hash = NULL "
            "WHERE id = ? AND attempts >= ? AND retired_at IS NULL",
            (now, row["id"], INVITATION_MAX_ATTEMPTS))
        if cur.rowcount:
            logger.warning("invitation %d retired after %d wrong codes", row["id"], INVITATION_MAX_ATTEMPTS)
    return None


def make_step_token(secret: bytes, invitation_id: int, email: str) -> str:
    """Proof that this browser or phone presented the right code moments
    ago, for the steps after it. Signed over its own context string, so it
    verifies as nothing where a session or a sign-in's pending token is
    expected - and neither of those verifies here."""
    email_b64 = base64.urlsafe_b64encode(_norm(email).encode()).decode().rstrip("=")
    expires = int(time.time() + STEP_TTL_SECONDS)
    payload = f"invite.{int(invitation_id)}.{email_b64}.{expires}"
    sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_step_token(secret: bytes, token: str | None) -> tuple[int, str] | None:
    try:
        context, invitation_id, email_b64, expires_s, sig = (token or "").split(".")
        if context != "invite":
            return None
        payload = f"invite.{invitation_id}.{email_b64}.{expires_s}"
        if not hmac.compare_digest(sig, hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()):
            return None
        if int(expires_s) < time.time():
            return None
        pad = "=" * (-len(email_b64) % 4)
        return int(invitation_id), base64.urlsafe_b64decode(email_b64 + pad).decode()
    except (ValueError, TypeError):
        return None


def step_invitation(db: Database, invitation_id: int, email: str):
    """The invitation a step token names, while it can still be accepted.
    The token is a minute-scale proof; this is the check that the
    invitation behind it has not since been used, retired or replaced."""
    row = open_invitation(db, email)
    if row is None or int(row["id"]) != int(invitation_id):
        return None
    return row


def store_password(db: Database, invitation_id: int, password: str) -> None:
    """The member's chosen password, held on the invitation (hashed) until
    two-factor is confirmed. Not a login yet: nothing signs in with it."""
    with db.cursor() as cur:
        cur.execute("UPDATE member_invitations SET password_hash = ? WHERE id = ? AND used_at IS NULL "
                    "AND retired_at IS NULL", (hash_password(password), int(invitation_id)))


def complete_invitation(db: Database, invitation_id: int, email: str) -> list[int]:
    """Turn an accepted invitation into a login, in one transaction: the
    login (scoped, with the password the member chose), a grant for each
    account the invitation names that is still watched and still lists the
    address, and the invitation marked used. Returns the granted account ids.

    The caller has already proven the code, the password and a working
    two-factor code; this re-checks everything that could have changed
    since, because a second tab or a second phone can finish first."""
    email = _norm(email)
    now = _now().isoformat()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM member_invitations WHERE id = ? AND email = ? AND used_at IS NULL "
            "AND retired_at IS NULL AND expires_at > ?", (int(invitation_id), email, now))
        row = cur.fetchone()
        if row is None or not row["password_hash"]:
            raise InvitationRefused("this invitation is no longer open - ask Minas for a new one")
        cur.execute("SELECT 1 FROM portal_members WHERE email = ?", (email,))
        if cur.fetchone() is not None:
            raise InvitationRefused("this address already has a login - sign in instead")
        granted = [int(a) for a in json.loads(row["account_ids_json"] or "[]")
                   if _eligible_account(cur, email, int(a))]
        if not granted:
            raise InvitationRefused("the accounts this invitation was for no longer list this address - "
                                    "ask Minas for a new one")
        cur.execute(
            "INSERT INTO portal_members (email, password_hash, created_at, updated_at, scoped) "
            "VALUES (?, ?, ?, ?, 1)", (email, row["password_hash"], now, now))
        for aid in granted:
            cur.execute(
                "INSERT OR IGNORE INTO member_account_grants (email, account_id, granted_at, granted_by) "
                "VALUES (?, ?, ?, ?)", (email, aid, now, row["created_by"]))
        cur.execute("UPDATE member_invitations SET used_at = ?, password_hash = NULL WHERE id = ?",
                    (now, int(invitation_id)))
    from sentinel.events import append_event

    for aid in granted:
        append_event(db, aid, "member_invitation_accepted", "LOW", {"login": email}, actor=email)
    logger.info("invitation %d accepted: login created for %s, opens %d account(s)",
                invitation_id, email, len(granted))
    return granted


def latest_invitation(db: Database, email: str):
    """The address's newest invitation of any state, for the dashboard to
    say where it stands."""
    with db.cursor() as cur:
        cur.execute("SELECT * FROM member_invitations WHERE email = ? ORDER BY id DESC LIMIT 1",
                    (_norm(email),))
        return cur.fetchone()


def invitation_state(row) -> str:
    """One word for a row: open, accepted, expired or retired."""
    if row is None:
        return "none"
    if row["used_at"]:
        return "accepted"
    if row["retired_at"] or int(row["attempts"] or 0) >= INVITATION_MAX_ATTEMPTS:
        return "retired"
    if row["expires_at"] <= _now().isoformat():
        return "expired"
    return "open"


# --- the emails ---------------------------------------------------------------

def _delivery_status(db: Database, rule: str, recipient: str, since: str) -> str | None:
    """What the email provider did with the newest send of `rule` to
    `recipient` at or after `since`: 'accepted', 'failed', or None when no
    email leg ran at all - no email channel configured, or one that skipped
    the send - so nothing is on its way."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT status FROM deliveries WHERE rule = ? AND channel = 'email' AND lower(recipient) = ? "
            "AND created_at >= ? ORDER BY id DESC LIMIT 1", (rule, _norm(recipient), since))
        row = cur.fetchone()
    if row is None or row["status"] not in ("accepted", "failed"):
        return None
    return row["status"]


def send_invitation_email(db: Database, invitation: Invitation) -> str | None:
    """Email the code, to the mailbox only (like the reset link, decision
    26c: whoever holds a connected Telegram chat must not be able to make a
    login with it). Returns the delivery status for the operator: the
    person who pressed the button is the one who has to know when it did
    not go."""
    from sentinel.alerts.base import AlertMessage
    from sentinel.alerts.contact import ContactRouter
    from sentinel.edge import portal_host
    from sentinel.portal import _PlainNotice

    where = portal_host() or "the Minas AI Guardian website"
    expires = datetime.fromisoformat(invitation.expires_at).strftime("%d %B %Y, %H:%M UTC")
    message = AlertMessage(
        account_label="your Minas AI Guardian login",
        rule=INVITATION_RULE,
        severity="LOW",
        confidence="n/a",
        message=(
            f"Minas has set up Minas AI Guardian for {invitation.email}. To create your sign-in, go to "
            f"{where} - type the address yourself - and choose \"Accept an invitation\". You can also "
            "start from the welcome screen of the Minas AI Guardian app. Enter this email address and "
            f"the code below, then choose a password and set up two-factor sign-in.\n\n"
            f"Invitation code: {invitation.code}\n\n"
            f"The code works once and expires in {expiry_phrase()} ({expires}). Minas will never ask "
            "you for this code, your password or your exchange keys by phone, chat or email. If you "
            "were not expecting this, you can ignore it - nothing happens unless the code is used."
        ),
        evidence={},
    )
    started = _now().isoformat()
    try:
        ContactRouter(db=db).send(
            _PlainNotice(message, email_only=True), "your Minas AI Guardian login", [invitation.email], [],
        )
    except Exception as exc:  # noqa: BLE001 - the operator is told below, not by a 500
        logger.error("could not send invitation %d: %s", invitation.id, exc)
        return "failed"
    return _delivery_status(db, INVITATION_RULE, invitation.email, started)


def send_login_created_email(db: Database, email: str) -> None:
    """The tripwire for a login that was just made: sent to the address it
    belongs to, so a member whose invitation somebody else used hears about
    it. Guarded - the login exists either way."""
    from sentinel.alerts.base import AlertMessage
    from sentinel.alerts.contact import ContactRouter
    from sentinel.portal import _PlainNotice

    message = AlertMessage(
        account_label="your Minas AI Guardian login",
        rule=LOGIN_CREATED_RULE,
        severity="LOW",
        confidence="n/a",
        message=(
            f"A Minas AI Guardian login was just created for {email} from your invitation, with "
            "two-factor sign-in on. If this was not you, contact Minas immediately - somebody else "
            "has access to this mailbox."
        ),
        evidence={},
    )
    try:
        ContactRouter(db=db).send(_PlainNotice(message), "your Minas AI Guardian login", [email], [])
    except Exception as exc:  # noqa: BLE001 - the login is made; the notice is the tripwire
        logger.error("could not send the login-created notice to %s: %s", email, exc)
