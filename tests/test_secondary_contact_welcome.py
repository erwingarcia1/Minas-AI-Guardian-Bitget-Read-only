"""#89: a new emergency contact is told they are one, exactly once.

The complaint that produced decision 1 (#27) came from a secondary contact
who did not know what her role meant. The note fires from EVERY writer of
`alert_email_secondary` - onboarding and set-contact, the pair-of-gates
rule - exactly once per newly-added address, its content follows decision
1's rules (why she is receiving it, the real windows, no balances, no
amounts, no addresses, no link), and a send failure must not fail the edit
that triggered it.
"""
from __future__ import annotations

import argparse

import pytest

from sentinel.cli import cmd_set_contact
from sentinel.events import notify_secondary_contact_added
from tests.conftest import make_account

KIN = "kin@example.com"


@pytest.fixture
def captured(monkeypatch):
    """Every note sent, as (detection, recipient-emails)."""
    import sentinel.alerts.contact as contact_module

    sent: list[tuple] = []

    class _Capture:
        def __init__(self, *a, **kw):
            pass

        def send(self, detection, account_label, emails, phones, **kw):
            sent.append((detection, tuple(emails)))

    monkeypatch.setattr(contact_module, "ContactRouter", _Capture)
    return sent


def _args(**kw):
    ns = argparse.Namespace(label=None, alert_email=None, alert_phone=None, clear=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# --- writer 1: set-contact ----------------------------------------------------

def test_adding_a_secondary_via_set_contact_sends_the_note_to_them(db, captured, monkeypatch):
    make_account(db, "acct-w1")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-w1", alert_email_secondary=KIN))
    assert [emails for _, emails in captured] == [(KIN,)]


def test_rerunning_the_same_secondary_sends_no_second_note(db, captured, monkeypatch):
    make_account(db, "acct-w2")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-w2", alert_email_secondary=KIN))
    cmd_set_contact(_args(label="acct-w2", alert_email_secondary=KIN))
    assert len(captured) == 1, "exactly once per newly-added address"


def test_replacing_the_secondary_notes_the_new_person_not_the_old(db, captured, monkeypatch):
    make_account(db, "acct-w3")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-w3", alert_email_secondary="first@example.com"))
    cmd_set_contact(_args(label="acct-w3", alert_email_secondary=KIN))
    assert [emails for _, emails in captured] == [("first@example.com",), (KIN,)]


def test_removing_the_secondary_sends_nothing(db, captured, monkeypatch):
    make_account(db, "acct-w4")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    cmd_set_contact(_args(label="acct-w4", alert_email_secondary=KIN))
    cmd_set_contact(_args(label="acct-w4", clear_secondary=True))
    assert len(captured) == 1, "a removal is not a listing - no welcome note"


# --- writer 2: onboarding -----------------------------------------------------

def _onboard(db, cipher, monkeypatch, label, secondary):
    from tests.test_onboarding import _client_with, account_info_response

    monkeypatch.setattr(
        "sentinel.onboarding.make_client",
        lambda *a, **kw: _client_with(
            account_info_result=account_info_response(authorities="spot_account_r")),
    )
    from sentinel.onboarding import onboard_account

    return onboard_account(
        db, cipher, label=label, topology="independent",
        api_key="k", secret="s", passphrase="p",
        alert_email_secondary=secondary,
    )


def test_onboarding_with_a_secondary_sends_the_note(db, cipher, captured, monkeypatch):
    _onboard(db, cipher, monkeypatch, "acct-w5", KIN)
    assert [emails for _, emails in captured] == [(KIN,)]


def test_onboarding_without_a_secondary_sends_nothing(db, cipher, captured, monkeypatch):
    _onboard(db, cipher, monkeypatch, "acct-w6", None)
    assert captured == []


# --- the content rules (decision 1's, #27) ------------------------------------

def test_the_note_says_why_states_the_windows_and_carries_no_link(db, captured):
    notify_secondary_contact_added(db, "acct-w7", KIN)
    (detection, emails), = captured
    text = detection.message

    assert "You have been listed as the emergency contact" in text, "the why"
    assert "hear nothing" in text, "the normal case is silence, said plainly"
    assert "60 minutes" in text and "15 minutes" in text, (
        "the real windows the Escalator uses (decision 1)")
    assert "closing line" in text, "the ack closing line is part of the deal"
    assert "http" not in text.lower(), "no one-tap links"
    assert "$" not in text and "balance" not in text.split("never contain")[0], (
        "no balances or amounts before the disclaimer that promises none")
    assert detection.is_notice, "renders verbatim, not as a detection summary"


def test_the_windows_follow_the_rules_config_when_given(db, captured):
    class _Rules:
        def get(self, *keys, default=None):
            return {"escalation_unacknowledged_minutes": 30,
                    "escalation_unacknowledged_minutes_high": 120}.get(keys[-1], default)

    notify_secondary_contact_added(db, "acct-w8", KIN, rules=_Rules())
    (detection, _), = captured
    assert "120 minutes" in detection.message
    assert "30 minutes" in detection.message


# --- the guard: a dead mail server must not fail the edit ---------------------

def test_a_send_failure_does_not_fail_the_edit(db, monkeypatch):
    import sentinel.alerts.contact as contact_module

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        def send(self, *a, **kw):
            raise RuntimeError("smtp is down")

    monkeypatch.setattr(contact_module, "ContactRouter", _Boom)
    make_account(db, "acct-w9")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))

    rc = cmd_set_contact(_args(label="acct-w9", alert_email_secondary=KIN))

    assert rc == 0, "the change happened; the note is best-effort"
    with db.cursor() as cur:
        cur.execute("SELECT alert_email_secondary FROM accounts WHERE label = 'acct-w9'")
        assert cur.fetchone()["alert_email_secondary"] == KIN
