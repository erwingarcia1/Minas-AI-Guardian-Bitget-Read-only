"""Saying what a portal password actually grants, before it is granted (#61).

The behaviour is coherent: portal identity is the email, and `member_accounts`
returns every account that email is a contact on. Nothing is broken in the
sense of not matching its design.

What is wrong is that **the control is not scoped the way its placement
implies**. The button sits on one account's page, under that account's heading,
and reads as an action on that account. It is not: it issues a credential that
opens every account the same person is a contact on — including ones the
operator was not looking at and may not have intended to share.

That gap between what a control looks like it does and what it does is where
mistakes come from, and it widened when G-21 let a *secondary* contact hold
their own credential.

This fixes the affordance, not the model. Per-account credentials are a
different and much larger change — a join table, a migration, and a decision
about what portal identity even is — and that is the owner's call, logged as
a decision rather than made here. What is not the owner's call is whether an
operator can see the blast radius before they click.
"""
from __future__ import annotations

import re

import pytest

from sentinel.dashboard import _portal_access_card


def _account(db, label, email=None, secondary=None):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, alert_email_secondary, status, onboarded_at, "
            "updated_at) VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, ?, 'active', "
            "'2026-01-01', '2026-01-01')",
            (label, email, secondary),
        )
        return cur.lastrowid


def _text(html_fragment: str) -> str:
    return re.sub(r"<[^>]+>", " ", html_fragment)


def test_the_card_names_every_account_the_credential_will_open(db):
    """The whole point. An operator on `alice-trading`'s page is about to issue
    a credential that also opens `alice-cold` and `alice-futures`, and until
    now the page said nothing about them."""
    _account(db, "alice-trading", email="alice@example.com")
    _account(db, "alice-cold", email="alice@example.com")
    _account(db, "alice-futures", email="ALICE@example.com")  # case-insensitive match
    here = _account(db, "alice-trading-2", email="alice@example.com")

    card = _text(_portal_access_card(db, here, "alice@example.com"))

    for label in ("alice-trading", "alice-cold", "alice-futures"):
        assert label in card, f"{label} is not named on the card"


def test_a_single_account_contact_is_not_warned_about_nothing(db):
    """When the credential really does open only this account, the card must
    not manufacture a warning. A caution shown every time is a caution nobody
    reads, which is how the real ones get missed."""
    here = _account(db, "solo-1", email="solo@example.com")

    card = _text(_portal_access_card(db, here, "solo@example.com"))

    # Matched against the phrase the card actually uses. Asserting on wording
    # the page never had ("also opens", "other accounts") is a test that
    # passes no matter what the code does — which is exactly how the first
    # version of this one passed while the caution was shown to everybody.
    assert "not just this one" not in card.lower()
    assert "portal identity is the email" not in card.lower()


def test_the_secondary_contact_gets_its_own_scope(db):
    """Two contacts on one account are two people with two different blast
    radii. Showing the primary's list next to the secondary's button would be
    worse than showing nothing."""
    here = _account(db, "shared-1", email="alice@example.com", secondary="bob@example.com")
    _account(db, "alice-only", email="alice@example.com")
    _account(db, "bob-only", email="bob@example.com")

    card = _text(_portal_access_card(db, here, "alice@example.com", "bob@example.com"))

    # Both scopes appear, and each is attached to its own contact's row.
    assert "alice-only" in card
    assert "bob-only" in card


def test_the_account_labels_are_escaped(db):
    """Labels are operator-supplied free text and this is the ops dashboard,
    so the audience is trusted — but every other label on these pages is
    escaped and a new one that is not is the beginning of the exception."""
    here = _account(db, "<script>x</script>", email="alice@example.com")
    _account(db, "<img src=x>", email="alice@example.com")

    card = _portal_access_card(db, here, "alice@example.com")

    assert "<script>x</script>" not in card
    assert "<img src=x>" not in card
    assert "&lt;" in card


def test_an_account_with_no_email_still_says_so(db):
    """The pre-existing behaviour, re-pinned: the scope line must not replace
    the message that there is nobody to issue a credential to."""
    here = _account(db, "no-contact")

    card = _text(_portal_access_card(db, here, None))

    assert "no email" in card.lower()


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_email_grants_nothing_and_claims_nothing(db, blank):
    """`lower('') = ''` matching every account with a blank contact is the bug
    `member_accounts` already guards against. The card must not undo it by
    listing the whole fleet as the blast radius of an empty address."""
    here = _account(db, "blank-1", email=blank)
    _account(db, "someone-else", email="")

    card = _text(_portal_access_card(db, here, blank))

    assert "someone-else" not in card
