"""Enrolling in, using and removing a second factor (G-28, #32).

`test_totp.py` covers the algorithm. This covers the part attackers actually
go for, because the maths is not where these things fail.

| Attack | Defence |
|---|---|
| Read the shared secret out of the database or a backup | encrypted at rest, same cipher as exchange API keys |
| Turn 2FA off with a stolen session | disabling requires a current code — proof of possession, not proof of a cookie |
| Turn 2FA off quietly | disabling emails the owner |
| Overwrite live 2FA by starting a fresh enrolment | enrolment refuses while a confirmed one exists |
| Reuse a recovery code | single-use, and consumed before success is returned |
| Keep recovery codes that outlive their secret | re-enrolling deletes the old ones |
| Sign in as an operator without a second factor | operators cannot disable it, and enrolment is required to complete a sign-in |
| Lock a customer out during enrolment | 2FA is not in force until a code has verified |
"""
from __future__ import annotations

import pytest

from sentinel.totp import STEP_SECONDS, totp_at
from sentinel.twofactor import (
    MEMBER,
    OPERATOR,
    begin_enrolment,
    check_second_factor,
    confirm_enrolment,
    disable,
    is_enabled,
    remaining_recovery_codes,
)
from tests.conftest import PINNED_TOTP_NOW

ALICE = "alice@example.com"


def _now():
    """The instant `tests/conftest.py` pins the verification clock to (#72), so
    a code this file builds is judged against the step it was built for rather
    than against a second, independent reading of the wall clock."""
    return PINNED_TOTP_NOW


def _code(secret, offset=0):
    return totp_at(secret, _now() + offset)


@pytest.fixture
def enrolled(db, cipher):
    secret, _ = begin_enrolment(db, cipher, MEMBER, ALICE)
    codes = confirm_enrolment(db, cipher, MEMBER, ALICE, _code(secret))
    assert codes
    return secret, codes


# --- enrolment cannot lock anybody out ----------------------------------------


def test_two_factor_is_not_in_force_until_a_code_verifies(db, cipher):
    """The mis-scanned QR case. A secret is stored so the customer can scan it,
    but until they prove they can produce a code, 2FA is off — otherwise a bad
    scan locks them out of the thing protecting their exchange accounts."""
    begin_enrolment(db, cipher, MEMBER, ALICE)

    assert is_enabled(db, MEMBER, ALICE) is False


def test_a_wrong_code_during_enrolment_changes_nothing(db, cipher):
    begin_enrolment(db, cipher, MEMBER, ALICE)

    assert confirm_enrolment(db, cipher, MEMBER, ALICE, "000000") is None
    assert is_enabled(db, MEMBER, ALICE) is False


def test_confirming_enables_it_and_issues_recovery_codes(db, cipher, enrolled):
    _, codes = enrolled

    assert is_enabled(db, MEMBER, ALICE) is True
    assert len(codes) == remaining_recovery_codes(db, MEMBER, ALICE)


def test_starting_a_fresh_enrolment_cannot_overwrite_live_two_factor(db, cipher, enrolled):
    """Otherwise the enrolment endpoint is a reset with no proof of
    possession — an attacker with a session just re-enrols to their own
    phone."""
    with pytest.raises(ValueError, match="already enabled"):
        begin_enrolment(db, cipher, MEMBER, ALICE)

    assert is_enabled(db, MEMBER, ALICE) is True


def test_restarting_an_unconfirmed_enrolment_is_allowed(db, cipher):
    """Somebody who closed the tab must be able to start again."""
    first, _ = begin_enrolment(db, cipher, MEMBER, ALICE)
    second, _ = begin_enrolment(db, cipher, MEMBER, ALICE)

    assert first != second
    assert confirm_enrolment(db, cipher, MEMBER, ALICE, _code(first)) is None
    assert confirm_enrolment(db, cipher, MEMBER, ALICE, _code(second)) is not None


# --- the secret is a credential -----------------------------------------------


def test_the_secret_is_encrypted_at_rest(db, cipher):
    """Same cipher as an exchange API key. A database read, or a backup, must
    not yield a working authenticator."""
    secret, _ = begin_enrolment(db, cipher, MEMBER, ALICE)

    with db.cursor() as cur:
        cur.execute("SELECT encrypted_secret FROM totp_enrolments")
        stored = cur.fetchone()["encrypted_secret"]

    assert secret not in stored
    assert cipher.decrypt(stored) == secret


# --- using it -----------------------------------------------------------------


def test_a_current_code_is_accepted_once(db, cipher, enrolled):
    secret, _ = enrolled
    code = _code(secret, offset=STEP_SECONDS)  # a step we have not spent

    assert check_second_factor(db, cipher, MEMBER, ALICE, code) is True
    assert check_second_factor(db, cipher, MEMBER, ALICE, code) is False


def test_a_wrong_code_is_refused(db, cipher, enrolled):
    assert check_second_factor(db, cipher, MEMBER, ALICE, "000000") is False


def test_somebody_with_no_enrolment_never_passes(db, cipher):
    """Fails closed. A subject with no enrolment must not sail through the
    second factor because there is nothing to check."""
    assert check_second_factor(db, cipher, MEMBER, "nobody@example.com", "123456") is False


def test_an_unconfirmed_enrolment_does_not_satisfy_the_second_factor(db, cipher):
    secret, _ = begin_enrolment(db, cipher, MEMBER, ALICE)

    assert check_second_factor(db, cipher, MEMBER, ALICE, _code(secret)) is False


def test_the_same_code_is_accepted_once_that_enrolment_is_confirmed(db, cipher):
    """What makes the test above mean anything (#72).

    `is False` on its own proves nothing: a wrong code, an expired code and a
    rejected-because-unconfirmed enrolment are all False. This runs the same
    secret at the same pinned instant through both states, so the only thing
    that differs between the two answers is `confirmed`. Without it, the
    unconfirmed test could pass with the guard deleted - and it did, in the
    health-check run that started this."""
    secret, _ = begin_enrolment(db, cipher, MEMBER, ALICE)
    assert check_second_factor(db, cipher, MEMBER, ALICE, _code(secret)) is False

    # A step we have not spent, so replay protection is not what answers here.
    assert confirm_enrolment(db, cipher, MEMBER, ALICE, _code(secret)) is not None
    assert check_second_factor(db, cipher, MEMBER, ALICE,
                               _code(secret, offset=STEP_SECONDS)) is True


def test_a_code_from_a_distant_step_is_refused_even_when_confirmed(db, cipher, enrolled):
    """The hazard itself, pinned so it stays understood rather than rediscovered.

    A confirmed enrolment - nothing wrong with it - still returns False when the
    code is judged against a step far from the one it was built for. That is the
    same False the unconfirmed guard returns, which is why every test in this
    file pins the clock instead of taking two independent readings of it."""
    secret, _ = enrolled

    stale = totp_at(secret, PINNED_TOTP_NOW - 3600)

    assert check_second_factor(db, cipher, MEMBER, ALICE, stale) is False


# --- recovery codes -----------------------------------------------------------


def test_a_recovery_code_works_once(db, cipher, enrolled):
    _, codes = enrolled

    assert check_second_factor(db, cipher, MEMBER, ALICE, codes[0]) is True
    assert check_second_factor(db, cipher, MEMBER, ALICE, codes[0]) is False
    assert remaining_recovery_codes(db, MEMBER, ALICE) == len(codes) - 1


def test_a_recovery_code_is_accepted_however_the_customer_types_it(db, cipher, enrolled):
    """They are read off paper under stress. Case and hyphens must not
    matter."""
    _, codes = enrolled
    mangled = codes[0].lower().replace("-", " ")

    assert check_second_factor(db, cipher, MEMBER, ALICE, mangled) is True


def test_recovery_codes_are_hashed_at_rest(db, cipher, enrolled):
    """They bypass the second factor entirely, so a database read must not
    yield a way past it."""
    _, codes = enrolled

    with db.cursor() as cur:
        cur.execute("SELECT code_hash FROM totp_recovery_codes")
        stored = [r["code_hash"] for r in cur.fetchall()]

    for code in codes:
        assert code not in stored
        assert code.replace("-", "") not in stored


def test_another_subjects_recovery_code_does_not_work(db, cipher, enrolled):
    _, alice_codes = enrolled
    bob_secret, _ = begin_enrolment(db, cipher, MEMBER, "bob@example.com")
    confirm_enrolment(db, cipher, MEMBER, "bob@example.com", _code(bob_secret))

    assert check_second_factor(db, cipher, MEMBER, "bob@example.com", alice_codes[0]) is False


# --- turning it off costs the same as turning it on ---------------------------


def test_disabling_requires_a_current_code(db, cipher, enrolled):
    """The one that matters most. 2FA whoever holds the session can remove
    protects nothing an attacker cannot undo in one click."""
    assert disable(db, cipher, MEMBER, ALICE, "000000") is False
    assert is_enabled(db, MEMBER, ALICE) is True


def test_disabling_with_a_current_code_works_and_notifies(db, cipher, enrolled):
    secret, _ = enrolled
    told = []

    assert disable(db, cipher, MEMBER, ALICE, _code(secret, offset=STEP_SECONDS),
                   notify=told.append) is True
    assert is_enabled(db, MEMBER, ALICE) is False
    assert told == [ALICE]


def test_disabling_removes_the_recovery_codes_too(db, cipher, enrolled):
    """Codes printed against a secret that no longer exists are a way in that
    survives the thing it was meant to recover."""
    secret, _ = enrolled

    disable(db, cipher, MEMBER, ALICE, _code(secret, offset=STEP_SECONDS))

    assert remaining_recovery_codes(db, MEMBER, ALICE) == 0


def test_a_failing_notification_does_not_leave_two_factor_half_off(db, cipher, enrolled):
    secret, _ = enrolled

    def explode(_):
        raise RuntimeError("smtp down")

    assert disable(db, cipher, MEMBER, ALICE, _code(secret, offset=STEP_SECONDS),
                   notify=explode) is True
    assert is_enabled(db, MEMBER, ALICE) is False


def test_re_enrolling_retires_the_previous_recovery_codes(db, cipher, enrolled):
    secret, old_codes = enrolled
    disable(db, cipher, MEMBER, ALICE, _code(secret, offset=STEP_SECONDS))

    new_secret, _ = begin_enrolment(db, cipher, MEMBER, ALICE)
    confirm_enrolment(db, cipher, MEMBER, ALICE, _code(new_secret))

    assert check_second_factor(db, cipher, MEMBER, ALICE, old_codes[0]) is False


# --- operators do not get a choice --------------------------------------------


def test_an_operator_cannot_disable_two_factor(db, cipher):
    """G-28's fourth criterion, enforced at the point of use rather than by a
    setting somebody can turn off. There is no code that removes it."""
    secret, _ = begin_enrolment(db, cipher, OPERATOR, "erwin")
    confirm_enrolment(db, cipher, OPERATOR, "erwin", _code(secret))

    with pytest.raises(ValueError, match="must keep two-factor"):
        disable(db, cipher, OPERATOR, "erwin", _code(secret, offset=STEP_SECONDS))

    assert is_enabled(db, OPERATOR, "erwin") is True


def test_members_and_operators_are_separate_subjects(db, cipher):
    """An operator is not a customer with extra rights, and the two live in
    separate tables reached through separate logins. One 2FA mechanism serving
    both must not quietly join them: a member and an operator who happen to
    share a name are two people."""
    secret, _ = begin_enrolment(db, cipher, OPERATOR, "alice@example.com")
    confirm_enrolment(db, cipher, OPERATOR, "alice@example.com", _code(secret))

    assert is_enabled(db, OPERATOR, "alice@example.com") is True
    assert is_enabled(db, MEMBER, "alice@example.com") is False


# --- the second factor is enforced at sign-in, not merely available ----------


def test_a_member_with_two_factor_needs_a_code_to_sign_in(db, cipher, enrolled):
    """A second factor nothing checks is a settings page.

    `check_member_login` verifies the password. `member_login_requires_code`
    is what stops a session being issued on the password alone — and it is a
    separate call precisely so the login route has to make a decision rather
    than inherit one."""
    from sentinel.portal import member_login_requires_code, set_member_password

    set_member_password(db, ALICE, "a-long-enough-password")

    assert member_login_requires_code(db, ALICE) is True


def test_a_member_without_two_factor_signs_in_on_the_password_alone(db, cipher):
    from sentinel.portal import member_login_requires_code, set_member_password

    set_member_password(db, ALICE, "a-long-enough-password")

    assert member_login_requires_code(db, ALICE) is False


def test_an_operator_without_two_factor_cannot_complete_a_sign_in(db, cipher):
    """G-28's fourth criterion has two halves: an enrolled operator cannot turn
    it off, and an *unenrolled* one cannot sign in at all. Without the second
    half, "operators must have 2FA" is satisfied by nobody enrolling."""
    from sentinel.operators import create_operator, operator_login_requires_code

    create_operator(db, "erwin", "pw-erwin")

    assert operator_login_requires_code(db, "erwin") is True


def test_an_enrolled_operator_still_requires_a_code(db, cipher):
    from sentinel.operators import create_operator, operator_login_requires_code

    create_operator(db, "erwin", "pw-erwin")
    secret, _ = begin_enrolment(db, cipher, OPERATOR, "erwin")
    confirm_enrolment(db, cipher, OPERATOR, "erwin", _code(secret))

    assert operator_login_requires_code(db, "erwin") is True
