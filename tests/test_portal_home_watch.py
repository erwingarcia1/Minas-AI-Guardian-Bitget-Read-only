"""#34 + #10: the home screen answers "am I covered" per account, at a glance.

Each account card carries the tri-state a customer can act on - Watching,
Paused, Trouble - derived from member_status's judgement (never recomputed,
so the card and /status cannot disagree), with the reason in the card when
something is wrong. And if ANY account is not being watched, a banner says
so before anything else: a customer who stops at the first screen still
learns it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sentinel.config import RulesConfig
from sentinel.portal import _render_home, _watch_state, member_status

EMAIL = "alice@example.com"


def _now():
    """Per-call, not module-level: a module-import NOW aged past the ~225s
    staleness window when a loaded machine ran the suite slowly, and every
    'fresh' account read as Trouble - a flake the 2026-08-24 run hit for
    real. Freshness is measured from when the TEST runs."""
    return datetime.now(timezone.utc)


@pytest.fixture
def rules():
    return RulesConfig("config/rules.yaml")


def _account(db, label, *, last_ok=None, status="active", auth_failures=0):
    last_ok = _now() - timedelta(seconds=10) if last_ok is None else last_ok
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, ?, '2026-01-01', '2026-01-01')",
            (label, EMAIL, status),
        )
        account_id = cur.lastrowid
        cur.execute(
            "INSERT INTO poll_state (account_id, last_success_at, consecutive_auth_failures) "
            "VALUES (?, ?, ?)",
            (account_id, last_ok.isoformat() if last_ok else None, auth_failures),
        )
        return account_id


# --- the tri-state -----------------------------------------------------------

def test_a_fresh_account_reads_watching(db, rules):
    _account(db, "alice-1")
    page = _render_home(db, EMAIL, rules)
    assert "Watching" in page
    assert 'role="alert"' not in page, "all watched - no banner"


def test_a_disabled_account_reads_paused_not_trouble(db, rules):
    """Stillness by choice is not a fault: a customer who asked for the pause
    must not be shown an error state for it."""
    _account(db, "alice-1", status="disabled")
    page = _render_home(db, EMAIL, rules)
    assert "Paused" in page
    assert "Trouble" not in page


def test_a_stale_account_reads_trouble_with_the_reason_in_the_card(db, rules):
    _account(db, "alice-1", last_ok=_now() - timedelta(hours=6))
    page = _render_home(db, EMAIL, rules)
    assert "Trouble" in page
    assert "no successful check recently" in page, (
        "the card must say what is wrong, not send the customer guessing")


def test_a_rejected_key_reads_trouble_with_the_fixable_reason(db, rules):
    _account(db, "alice-1", auth_failures=3)
    page = _render_home(db, EMAIL, rules)
    assert "Trouble" in page
    assert "key is being rejected" in page


def test_the_state_is_member_statuss_judgement_not_a_recomputation(db, rules):
    """The mapping function consumes member_status rows verbatim - the pin
    that stops the home card and /status ever disagreeing."""
    _account(db, "alice-1", status="disabled")
    account = member_status(db, EMAIL, rules)["accounts"][0]
    assert _watch_state(account) == ("badge--neutral", "Paused")
    assert _watch_state(None) == ("badge--danger", "Trouble"), (
        "an account member_status somehow missed must read as trouble, never watched")


# --- the banner (#34: prominent if ANY account isn't watched) -----------------

def test_one_dark_account_among_watched_raises_the_banner(db, rules):
    _account(db, "alice-1")
    _account(db, "alice-2", last_ok=_now() - timedelta(hours=6))
    page = _render_home(db, EMAIL, rules)
    assert 'role="alert"' in page
    assert "1 of your 2 accounts is not being watched." in page
    assert 'href="/status"' in page, "the banner leads to what each account needs"


def test_no_accounts_watched_raises_the_danger_banner(db, rules):
    _account(db, "alice-1", last_ok=_now() - timedelta(hours=6))
    _account(db, "alice-2", auth_failures=2)
    page = _render_home(db, EMAIL, rules)
    assert "Your accounts are NOT being watched." in page
    assert "state--danger" in page


def test_a_paused_account_still_counts_as_not_watched_in_the_banner(db, rules):
    """Paused IS not watched. The customer chose it, and the banner is where
    that choice stays visible instead of quietly forgotten."""
    _account(db, "alice-1")
    _account(db, "alice-2", status="disabled")
    page = _render_home(db, EMAIL, rules)
    assert 'role="alert"' in page
    assert "1 of your 2 accounts is not being watched." in page


def test_a_member_with_no_accounts_gets_no_banner(db, rules):
    page = _render_home(db, EMAIL, rules)
    assert 'role="alert"' not in page
    assert "No accounts yet" in page


# --- decision 16's landing half (#20) -----------------------------------------

def _alert(db, account_id, *, severity="CRITICAL", acked=False, when=None):
    when = (when or _now()).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at, delivered_to, acknowledged_at) VALUES (?, 'withdrawal_initiated', "
            "?, ?, 'm', '{}', 'high', ?, 1, ?)",
            (account_id, severity, f"k{when}", when, when if acked else None))
        return cur.lastrowid


def test_the_newest_unacknowledged_incident_is_pinned_above_the_fold(db, rules):
    account_id = _account(db, "alice-1")
    old = _alert(db, account_id, when=_now() - timedelta(hours=3))
    newest = _alert(db, account_id, when=_now() - timedelta(minutes=5))

    page = _render_home(db, EMAIL, rules)

    assert f"Needs your attention — incident #{newest}" in page
    assert f'href="/incident?id={newest}"' in page
    assert page.index("Needs your attention") < page.index('id="accounts-title"'), "above the fold"
    assert f"incident #{old}" not in page, "one incident pinned, not a list"


def test_an_acknowledged_incident_is_not_pinned(db, rules):
    account_id = _account(db, "alice-1")
    _alert(db, account_id, acked=True)
    page = _render_home(db, EMAIL, rules)
    assert "Needs your attention" not in page


def test_the_pinned_number_is_what_the_email_reference_names(db, rules):
    """The email says "This is incident #N" (plain text, decision 16); the
    portal's pinned block carries the same number - the two halves meet."""
    from sentinel.alerts.base import AlertMessage

    account_id = _account(db, "alice-1")
    alert_id = _alert(db, account_id)

    email_body = AlertMessage(
        account_label="alice-1", rule="withdrawal_initiated", severity="CRITICAL",
        confidence="high", message="raw", evidence={}, incident_id=alert_id,
    ).render_customer()

    assert f"This is incident #{alert_id}." in email_body
    assert "http" not in email_body.lower(), "a reference, never a link"
    assert f"incident #{alert_id}" in _render_home(db, EMAIL, rules)
