"""Enrolling, verifying and removing a second factor (G-28, #32).

`totp.py` is the algorithm. This is the part an attacker actually goes for,
because the maths is not where these things fail:

**Enrolment must not be able to lock a customer out.** The secret is stored the
moment it is generated so the customer can scan it, but 2FA is not *in force*
until they have produced a working code. A mis-scanned QR code, a phone with
the wrong clock, an app that was not saved - all of those end with the customer
still able to sign in, rather than locked out of the thing protecting their
exchange accounts.

**Disabling must cost as much as enabling.** 2FA that can be turned off by
whoever holds the session protects nothing an attacker cannot undo in one
click. Removing it requires a current code or a recovery code - proof of
possession, not proof of a cookie - and it tells the owner afterwards.

**The secret is a credential.** Encrypted at rest with the same cipher as
exchange API keys. A database read must not yield a working authenticator.

**Operators are not optional.** G-28's fourth criterion, and it is enforced at
the point of use rather than by a setting somebody can turn off: an operator
without a confirmed enrolment cannot complete a sign-in.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sentinel.db import Database
from sentinel.totp import (
    generate_recovery_codes,
    generate_secret,
    hash_recovery_code,
    provisioning_uri,
    verify_code,
)

logger = logging.getLogger("sentinel.twofactor")

MEMBER = "member"
OPERATOR = "operator"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def begin_enrolment(db: Database, cipher, kind: str, subject: str) -> tuple[str, str]:
    """Generate and store an unconfirmed secret. Returns (secret, otpauth URI).

    Replaces any existing *unconfirmed* enrolment - somebody who started twice
    should get the secret they most recently scanned. It deliberately does NOT
    replace a confirmed one: overwriting live 2FA from an enrolment endpoint
    would be a reset with no proof of possession, which is the whole attack
    this module exists to stop."""
    subject = subject.strip().lower()
    with db.cursor() as cur:
        cur.execute(
            "SELECT confirmed FROM totp_enrolments WHERE subject_kind = ? AND subject = ?",
            (kind, subject),
        )
        row = cur.fetchone()
        if row is not None and row["confirmed"]:
            raise ValueError("two-factor is already enabled; disable it first")

    secret = generate_secret()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO totp_enrolments (subject_kind, subject, encrypted_secret, confirmed, "
            "created_at) VALUES (?, ?, ?, 0, ?) "
            "ON CONFLICT(subject_kind, subject) DO UPDATE SET "
            "encrypted_secret = excluded.encrypted_secret, confirmed = 0, "
            "last_used_step = NULL, created_at = excluded.created_at",
            (kind, subject, cipher.encrypt(secret), _now()),
        )
    return secret, provisioning_uri(secret, subject)


def confirm_enrolment(db: Database, cipher, kind: str, subject: str, code: str) -> list[str] | None:
    """Turn 2FA on, but only against a working code. Returns the recovery codes.

    None means the code did not verify and nothing changed - which is the
    point. Confirming without checking would enable 2FA against a secret the
    customer may never have successfully scanned."""
    subject = subject.strip().lower()
    row = _enrolment(db, kind, subject)
    if row is None:
        return None
    secret = cipher.decrypt(row["encrypted_secret"])
    step = verify_code(secret, code, at=datetime.now(timezone.utc).timestamp(),
                       last_used_step=row["last_used_step"])
    if step is None:
        return None

    codes = generate_recovery_codes()
    now = _now()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE totp_enrolments SET confirmed = 1, confirmed_at = ?, last_used_step = ? "
            "WHERE id = ?",
            (now, step, row["id"]),
        )
        # Any codes from a previous enrolment are dead: they were printed
        # against a secret that no longer exists.
        cur.execute(
            "DELETE FROM totp_recovery_codes WHERE subject_kind = ? AND subject = ?",
            (kind, subject),
        )
        cur.executemany(
            "INSERT INTO totp_recovery_codes (subject_kind, subject, code_hash, created_at) "
            "VALUES (?, ?, ?, ?)",
            [(kind, subject, hash_recovery_code(c), now) for c in codes],
        )
    logger.info("two-factor enabled for %s %s", kind, subject)
    _record_change(db, kind, subject, "member_two_factor_enabled")
    return codes


def _record_change(db: Database, kind: str, subject: str, event_type: str) -> None:
    """Leave an audit row on every account this login opens (G-32, #36).

    Here rather than in the HTTP handler, on the same reasoning as
    `set_member_password`: a record written by the caller is a record the next
    caller forgets. An operator subject writes nothing - an operator username
    is not an account contact - which is correct, not a silent failure:
    operator 2FA is not a change to any customer's account.

    Guarded. The second factor has already changed by the time this runs, and
    an exception here would report a failure that did not happen."""
    if kind != MEMBER:
        return
    try:
        from sentinel.events import record_member_security_event

        record_member_security_event(db, subject, event_type)
    except Exception as exc:  # noqa: BLE001 - the change has already happened
        logger.error("could not record %s for %s: %s", event_type, subject, exc)


def is_enabled(db: Database, kind: str, subject: str) -> bool:
    row = _enrolment(db, kind, subject)
    return bool(row and row["confirmed"])


def check_second_factor(db: Database, cipher, kind: str, subject: str, code: str) -> bool:
    """Verify a TOTP code or a recovery code. Consumes whichever matched.

    Order matters: TOTP first. A six-digit numeric string cannot be a recovery
    code, so trying recovery codes first would burn nothing - but it would put
    an unindexed scan on the hot path of every sign-in."""
    subject = subject.strip().lower()
    row = _enrolment(db, kind, subject)
    if row is None or not row["confirmed"]:
        return False

    secret = cipher.decrypt(row["encrypted_secret"])
    step = verify_code(secret, code, at=datetime.now(timezone.utc).timestamp(),
                       last_used_step=row["last_used_step"])
    if step is not None:
        # Recorded BEFORE returning success, so a code cannot be spent twice
        # by two requests arriving together.
        with db.cursor() as cur:
            cur.execute("UPDATE totp_enrolments SET last_used_step = ? WHERE id = ?",
                        (step, row["id"]))
        return True

    return _consume_recovery_code(db, kind, subject, code)


def _consume_recovery_code(db: Database, kind: str, subject: str, code: str) -> bool:
    if not code:
        return False
    with db.cursor() as cur:
        cur.execute(
            "SELECT id FROM totp_recovery_codes WHERE subject_kind = ? AND subject = ? "
            "AND code_hash = ? AND used_at IS NULL",
            (kind, subject, hash_recovery_code(code)),
        )
        row = cur.fetchone()
        if row is None:
            return False
        cur.execute("UPDATE totp_recovery_codes SET used_at = ? WHERE id = ?", (_now(), row["id"]))
    logger.warning(
        "recovery code used for %s %s - they no longer have their authenticator", kind, subject,
    )
    return True


def remaining_recovery_codes(db: Database, kind: str, subject: str) -> int:
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) c FROM totp_recovery_codes WHERE subject_kind = ? AND subject = ? "
            "AND used_at IS NULL",
            (kind, subject.strip().lower()),
        )
        return cur.fetchone()["c"]


def disable(db: Database, cipher, kind: str, subject: str, code: str, notify=None) -> bool:
    """Turn 2FA off, against a current code. False if the code did not verify.

    The code requirement is the whole point (G-28's third criterion). 2FA that
    whoever holds the session can remove protects nothing an attacker cannot
    undo in one click - it has to cost proof of possession, the same thing
    enabling it cost.

    `notify` is called afterwards and guarded. A customer who did not do this
    needs to know, and a mail failure must not leave 2FA in an in-between
    state."""
    subject = subject.strip().lower()
    if kind == OPERATOR:
        # G-28's fourth criterion: not optional for operators, so there is no
        # code that turns it off. Re-enrolling replaces the secret; nothing
        # removes the requirement.
        raise ValueError("operator accounts must keep two-factor enabled")
    if not check_second_factor(db, cipher, kind, subject, code):
        return False
    with db.cursor() as cur:
        cur.execute("DELETE FROM totp_enrolments WHERE subject_kind = ? AND subject = ?",
                    (kind, subject))
        cur.execute("DELETE FROM totp_recovery_codes WHERE subject_kind = ? AND subject = ?",
                    (kind, subject))
    logger.warning("two-factor DISABLED for %s %s", kind, subject)
    _record_change(db, kind, subject, "member_two_factor_disabled")
    if notify is not None:
        try:
            notify(subject)
        except Exception as exc:  # noqa: BLE001 - the change already happened
            logger.error("could not send the two-factor-disabled notice to %s: %s", subject, exc)
    return True


def _enrolment(db: Database, kind: str, subject: str):
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM totp_enrolments WHERE subject_kind = ? AND subject = ?",
            (kind, subject.strip().lower()),
        )
        return cur.fetchone()
