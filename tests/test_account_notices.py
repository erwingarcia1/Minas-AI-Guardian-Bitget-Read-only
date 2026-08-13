"""Account notices carry their meaning in the message (#33, #32, #36).

Four emails are not detections: the password-reset link, the
password-changed and two-factor-disabled tripwires, and the
alert-destination-change notice. Each one exists for the sentence it carries -
a link somebody must click, or the specific thing that changed on their
account - and each is delivered through the same `ContactRouter` as a
detection.

Anything that reformats a detection into a summary must therefore be able to
tell the two apart, or these arrive with their point removed: a reset email
with no link is a customer locked out, and a tripwire with no warning text is a
customer told nothing at the moment somebody else is in their account.

`_PlainNotice.is_notice` is that signal. This asserts every sender goes through
it, rather than asserting the attribute exists - a class attribute is trivially
true and proves nothing about the four call sites that have to use it.
"""
from __future__ import annotations

import pytest

from sentinel.events import notify_alert_destination_change
from sentinel.portal import (
    _send_password_changed_email,
    _send_reset_email,
    _send_two_factor_disabled_email,
    set_member_password,
)


@pytest.fixture
def captured(monkeypatch):
    """Every notice this module sends, as (detection, recipients)."""
    import sentinel.alerts.contact as contact_module

    sent: list[tuple] = []

    class _Capture:
        def __init__(self, *a, **kw):
            pass

        def send(self, detection, account_label, emails, phones, **kw):
            sent.append((detection, tuple(emails)))

    monkeypatch.setattr(contact_module, "ContactRouter", _Capture)
    return sent


def _account(db, label, email):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', '2026-01-01', '2026-01-01')",
            (label, email),
        )
        return cur.lastrowid


EMAIL = "customer@example.com"


def test_the_reset_email_is_a_notice_and_carries_the_link(db, captured):
    _send_reset_email(db, EMAIL, "a-token-value")
    assert len(captured) == 1
    detection, recipients = captured[0]
    assert recipients == (EMAIL,)
    assert detection.is_notice is True
    # The reason it must not be summarised: this is the whole email.
    assert "a-token-value" in detection.message


def test_the_password_changed_tripwire_is_a_notice(db, captured):
    _send_password_changed_email(db, EMAIL)
    detection, _recipients = captured[0]
    assert detection.is_notice is True
    assert "was just changed" in detection.message


def test_the_two_factor_disabled_tripwire_is_a_notice(db, captured):
    _send_two_factor_disabled_email(db, EMAIL)
    detection, _recipients = captured[0]
    assert detection.is_notice is True
    assert "turned off" in detection.message


def test_the_destination_change_notice_is_a_notice(db, captured):
    notify_alert_destination_change(db, "Main", EMAIL, "attacker@example.com")
    detection, recipients = captured[0]
    assert recipients == (EMAIL,)  # the address being removed
    assert detection.is_notice is True
    assert "attacker@example.com" in detection.message


def test_every_notice_sender_goes_through_the_same_adapter(db, captured):
    """One list, so a fifth notice added without the adapter is visible here
    rather than discovered in somebody's inbox."""
    _account(db, "Main", EMAIL)
    _send_reset_email(db, EMAIL, "t")
    _send_password_changed_email(db, EMAIL)
    _send_two_factor_disabled_email(db, EMAIL)
    notify_alert_destination_change(db, "Main", EMAIL, "new@example.com")
    set_member_password(db, EMAIL, "a-long-portal-password",
                        notify=lambda addr: _send_password_changed_email(db, addr))

    assert len(captured) == 5
    assert all(d.is_notice is True for d, _ in captured)
