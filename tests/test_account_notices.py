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

import argparse

import pytest

from sentinel.cli import cmd_set_contact
from sentinel.events import notify_alert_destination_change
from sentinel.portal import (
    _send_password_changed_email,
    _send_reset_email,
    _send_two_factor_disabled_email,
    member_accounts,
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


# --- the destination-change notice says only what the portal does -------------
#
# The notice to the address being removed says "this address can no longer
# see the account in the Minas portal". Until set-contact took the portal
# login with the move, that was false for a SCOPED login (#61: it opens its
# grants, which the move did not touch) and it is still false for an address
# the same command kept on the account - moved from primary to emergency
# contact. A tripwire that lies about access is one the customer learns to
# disbelieve, so the sentence must agree with `member_accounts`, the
# function the portal enforces with, after the change.

PORTAL_SENTENCE = "this address can no longer see the account in the Minas portal"


@pytest.mark.parametrize("login, move, says_closed", [
    ("none", {}, True),
    ("legacy", {}, True),
    ("scoped", {}, True),
    ("legacy", {"alert_email_secondary": EMAIL}, False),
    ("scoped", {"alert_email_secondary": EMAIL}, False),
], ids=["no-login", "legacy", "scoped", "legacy-kept-as-emergency", "scoped-kept-as-emergency"])
def test_the_notice_says_the_portal_is_closed_only_when_it_is(db, captured, monkeypatch,
                                                              login, move, says_closed):
    account_id = _account(db, "Main", EMAIL)
    if login == "legacy":
        set_member_password(db, EMAIL, "a-long-portal-password")
    elif login == "scoped":
        set_member_password(db, EMAIL, "a-long-portal-password", grant_account_id=account_id)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))

    assert cmd_set_contact(argparse.Namespace(
        label="Main", alert_email="new@example.com", alert_phone=None, clear=False, **move)) == 0

    notices = [(d, r) for d, r in captured if d.rule == "alert_destination_changed"]
    assert len(notices) == 1
    detection, recipients = notices[0]
    assert recipients == (EMAIL,)
    still_opens = any(int(r["id"]) == account_id for r in member_accounts(db, EMAIL))
    assert (PORTAL_SENTENCE in detection.message) is says_closed
    assert (PORTAL_SENTENCE in detection.message) is not still_opens, (
        "the notice and the portal disagree about whether this address can still see the account")
    # The alerts half is held to the same truth: an address kept on as the
    # emergency contact is still written to when an alert goes unanswered.
    kept = "alert_email_secondary" in move
    assert ("Alerts about this account will no longer be sent to this address" in detection.message) is not kept
    assert ("This address stays on the account as its emergency contact" in detection.message) is kept
    # Whatever either half says, the tripwire itself is intact.
    assert "new@example.com" in detection.message
    assert "If you did not ask for this, contact Minas immediately" in detection.message
