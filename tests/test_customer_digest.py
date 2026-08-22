"""#21: the proof-of-life digest — for a product whose success looks like
silence, this is how a customer tells good silence from a dead poller.

Three plain statements (accounts watched — the same judgement as /status,
so the digest and the page cannot disagree; checks performed — the
measured counter's delta, never a cadence estimate; issues found — counts
in severity words), sent even when all is quiet because that is the point,
per the member's own daily/weekly/off choice, once per day at most, and
never to secondary contacts (decision 1: they hear escalations only).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sentinel.alerts.digest import (
    build_member_digest,
    digest_frequency,
    send_member_digests,
    set_digest_frequency,
)

EMAIL = "alice@example.com"


@pytest.fixture
def sent(monkeypatch):
    import sentinel.alerts.contact as contact_module

    out: list[tuple] = []

    class _Capture:
        def __init__(self, *a, **kw):
            pass

        def send(self, detection, account_label, emails, phones, **kw):
            out.append((detection, tuple(emails)))

    monkeypatch.setattr(contact_module, "ContactRouter", _Capture)
    return out


def _account(db, label="acct-d1", email=EMAIL, *, secondary=None, status="active",
             checks=120, fresh=True):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, alert_email_secondary, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, ?, ?, '2026-01-01', '2026-01-01')",
            (label, email, secondary, status))
        account_id = cur.lastrowid
        last = datetime.now(timezone.utc).isoformat() if fresh else "2026-01-01T00:00:00+00:00"
        cur.execute(
            "INSERT INTO poll_state (account_id, last_success_at, checks_total) VALUES (?, ?, ?)",
            (account_id, last, checks))
        return account_id


def _member(db, email=EMAIL, frequency=None):
    from sentinel.portal import set_member_password

    set_member_password(db, email, "a-long-enough-password-1", notify=None)
    if frequency:
        set_digest_frequency(db, email, frequency)


# --- the three plain statements ----------------------------------------------

def test_the_digest_states_watched_checks_and_issues_plainly(db, sent):
    _account(db, checks=120)
    body, _mark = build_member_digest(db, EMAIL)
    assert "Accounts watched: 1 of 1." in body
    assert "Checks performed since monitoring began: 120." in body
    assert "Issues found: none — all quiet." in body
    assert "That silence is real" in body, "the proof-of-life is the message"


def test_checks_performed_is_the_measured_delta_not_a_cadence_estimate(db, sent):
    account_id = _account(db, checks=120)
    body, mark = build_member_digest(db, EMAIL)
    # The mark is stored on SEND; simulate that, then more checks happen.
    from sentinel.alerts.digest import _set_digest_mark

    _set_digest_mark(db, EMAIL, mark)
    with db.cursor() as cur:
        cur.execute("UPDATE poll_state SET checks_total = 175 WHERE account_id = ?", (account_id,))
    body, _ = build_member_digest(db, EMAIL)
    assert "Checks performed since your last digest" in body
    assert ": 55." in body, "the delta is measured against the stored mark"


def test_issues_are_counts_in_plain_words_never_rule_names(db, sent):
    account_id = _account(db)
    with db.cursor() as cur:
        for sev in ("CRITICAL", "LOW"):
            cur.execute(
                "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, "
                "evidence_json, confidence, sent_at, delivered_to) "
                "VALUES (?, 'withdrawal_to_unseen_address', ?, 'k', 'raw 0xdeadbeef', '{}', "
                "'high', ?, 1)", (account_id, sev, datetime.now(timezone.utc).isoformat()))
    body, _ = build_member_digest(db, EMAIL)
    assert "1 urgent alert(s) — you were told at the time" in body
    assert "1 routine notice(s)" in body
    assert "withdrawal_to_unseen_address" not in body, "rule names are internal"
    assert "0xdeadbeef" not in body, "evidence never reaches customer copy"


def test_a_dark_account_is_named_with_its_reason(db, sent):
    _account(db, fresh=False)
    body, _ = build_member_digest(db, EMAIL)
    assert "Accounts watched: 0 of 1." in body
    assert "NOT being watched" in body


def test_a_paused_account_reads_as_the_customers_choice(db, sent):
    _account(db, status="disabled")
    body, _ = build_member_digest(db, EMAIL)
    assert "paused, at your request" in body


# --- frequency: the member's choice ------------------------------------------

def test_daily_is_the_default_and_sends(db, sent):
    _account(db)
    assert digest_frequency(db, EMAIL) == "daily"
    n = send_member_digests(db, now=datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc))
    assert n == 1
    assert [e for _, e in sent] == [(EMAIL,)]


def test_off_sends_nothing(db, sent):
    _account(db)
    _member(db, frequency="off")
    assert send_member_digests(db) == 0
    assert sent == []


def test_weekly_sends_only_on_mondays(db, sent):
    _account(db)
    _member(db, frequency="weekly")
    tuesday = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
    monday = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)
    assert send_member_digests(db, now=tuesday) == 0
    assert send_member_digests(db, now=monday) == 1


def test_a_second_run_on_the_same_day_does_not_double_send(db, sent):
    _account(db)
    when = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
    assert send_member_digests(db, now=when) == 1
    assert send_member_digests(db, now=when) == 0, "a cron re-run must be a no-op"
    assert len(sent) == 1


def test_secondary_contacts_get_no_digest(db, sent):
    """Decision 1's rule: the emergency contact hears escalations and the
    closing line, never routine traffic."""
    _account(db, secondary="kin@example.com")
    send_member_digests(db, now=datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc))
    recipients = {e for _, emails in sent for e in emails}
    assert "kin@example.com" not in recipients
    assert EMAIL in recipients


def test_a_disabled_accounts_contact_is_not_digested_for_it_alone(db, sent):
    _account(db, status="disabled")
    assert send_member_digests(db, now=datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)) == 0


def test_a_corrupt_stored_preference_reads_as_daily_not_off(db, sent):
    _account(db)
    _member(db)
    with db.cursor() as cur:
        cur.execute("UPDATE portal_members SET digest_frequency = 'garbage' WHERE email = ?", (EMAIL,))
    assert digest_frequency(db, EMAIL) == "daily", (
        "a corrupted preference must not silently turn the proof of life off")


# --- the portal setting -------------------------------------------------------

def test_the_security_page_offers_the_three_choices(db, sent):
    from sentinel.portal import _render_security

    _account(db)
    _member(db)
    page = _render_security(db, EMAIL)
    assert "Proof-of-life digest" in page
    for value in ("daily", "weekly", "off"):
        assert f'value="{value}"' in page
    assert 'action="/digest/frequency"' in page


def test_changing_the_frequency_is_a_recorded_event(db, sent):
    account_id = _account(db)
    _member(db)
    assert set_digest_frequency(db, EMAIL, "weekly") is True
    assert set_digest_frequency(db, EMAIL, "weekly") is False, "same value, no diff"
    from sentinel.events import ADMIN_EVENT_LABELS, ADMIN_EVENT_TYPES

    assert "digest_frequency_changed" in ADMIN_EVENT_TYPES
    assert ADMIN_EVENT_LABELS["digest_frequency_changed"] == "Digest schedule changed"
    assert account_id  # the event itself is written by the portal handler, evented per diff
