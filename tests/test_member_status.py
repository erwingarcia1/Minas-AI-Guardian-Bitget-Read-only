"""G-34 (#38): the customer can tell "nothing happened" from "nobody watched".

The product's claim is that no news is good news. That claim is worth nothing
unless silence can be told apart from a dead poller, and until this page the
customer had no way to tell — the same failure this codebase keeps closing on
its own behalf, pointed at the person paying for it.

`member_status` is tested as data rather than through the rendered page: the
judgement is the part that must be right, and a test that greps HTML passes on
a page that says the opposite in a sentence next to the word it matched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sentinel.config import RulesConfig
from sentinel.portal import _render_status, _stale_after_seconds, member_status

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def rules():
    return RulesConfig("config/rules.yaml")


def _account(db, label, email, *, last_ok=NOW, status="active", auth_failures=0, failures=0):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, ?, '2026-01-01', '2026-01-01')",
            (label, email, status),
        )
        account_id = cur.lastrowid
        cur.execute(
            "INSERT INTO poll_state (account_id, last_success_at, consecutive_auth_failures, "
            "consecutive_failures) VALUES (?, ?, ?, ?)",
            (account_id, last_ok.isoformat() if last_ok else None, auth_failures, failures),
        )
        return account_id


# --- the window is derived, not typed in ------------------------------------

def test_the_staleness_window_matches_the_detector_not_a_literal(rules):
    """If this number and the heartbeat detector's disagree, the page and the
    alert disagree about whether an account is watched — and the page is the
    one the customer believes."""
    expected = (
        rules.get_int("detectors", "heartbeat", "consecutive_failures_threshold")
        * rules.get_int("polling", "fast_loop_seconds")
    )
    assert _stale_after_seconds(rules) == expected


def test_the_window_survives_having_no_config(rules):
    """Falls back to the declared defaults rather than to zero, which would
    mark every account unwatched the instant it was polled."""
    assert _stale_after_seconds(None) > 0


# --- the judgement -----------------------------------------------------------

def test_a_freshly_polled_account_is_watched(db, rules):
    _account(db, "alice-1", "alice@example.com", last_ok=NOW - timedelta(seconds=10))

    status = member_status(db, "alice@example.com", rules, now=NOW)

    assert status["overall"] == "watching"
    assert status["problems"] == []
    assert status["accounts"][0]["watched"] is True


def test_a_silent_account_is_not_watched(db, rules):
    _account(db, "alice-1", "alice@example.com", last_ok=NOW - timedelta(hours=3))

    status = member_status(db, "alice@example.com", rules, now=NOW)

    assert status["overall"] == "not_watching"
    assert "unreachable" in status["problems"][0]["reason"]


def test_a_rejected_key_is_reported_as_the_customer_s_to_fix(db, rules):
    """Ordered by what the customer can do about it. Telling somebody to
    re-issue a working key is wrong advice, so a rejected key and an
    unreachable exchange must not produce the same sentence (G-6)."""
    _account(db, "alice-1", "alice@example.com", last_ok=NOW - timedelta(hours=3),
             auth_failures=4)

    status = member_status(db, "alice@example.com", rules, now=NOW)

    assert "key is being rejected" in status["problems"][0]["reason"]


def test_a_rejected_key_beats_staleness_even_when_recently_polled(db, rules):
    """An auth failure with a fresh timestamp is still not being watched. The
    poll ran; it just could not see anything."""
    _account(db, "alice-1", "alice@example.com", last_ok=NOW - timedelta(seconds=5),
             auth_failures=2)

    status = member_status(db, "alice@example.com", rules, now=NOW)

    assert status["overall"] == "not_watching"
    assert status["accounts"][0]["watched"] is False


def test_some_watched_and_some_not_is_degraded_not_healthy(db, rules):
    """The failure mode this exists to prevent: one healthy account making the
    page say everything is fine while another is dark."""
    _account(db, "alice-ok", "alice@example.com", last_ok=NOW - timedelta(seconds=10))
    _account(db, "alice-dark", "alice@example.com", last_ok=NOW - timedelta(hours=6))

    status = member_status(db, "alice@example.com", rules, now=NOW)

    assert status["overall"] == "degraded"
    assert [p["label"] for p in status["problems"]] == ["alice-dark"]


def test_an_account_that_never_checked_is_not_quietly_counted_as_watched(db, rules):
    _account(db, "alice-new", "alice@example.com", last_ok=None)

    status = member_status(db, "alice@example.com", rules, now=NOW)

    assert status["overall"] == "not_watching"
    assert "never completed a check" in status["problems"][0]["reason"]


def test_a_disabled_account_says_so_rather_than_looking_unreachable(db, rules):
    _account(db, "alice-off", "alice@example.com", last_ok=NOW - timedelta(seconds=5),
             status="disabled")

    status = member_status(db, "alice@example.com", rules, now=NOW)

    assert "switched off" in status["problems"][0]["reason"]


def test_a_tamper_flagged_account_is_reported_as_unwatched_in_plain_words(db, rules):
    """The alarming one, and the reason this page matters. Only `active`
    accounts are polled, so flagging an account for suspected key tampering
    stops it being checked — the account we most suspect is being interfered
    with is the one we stop looking at. The customer is told, in words rather
    than by printing our column value at them."""
    _account(db, "alice-tamper", "alice@example.com", last_ok=NOW - timedelta(seconds=5),
             status="tamper_suspected")

    status = member_status(db, "alice@example.com", rules, now=NOW)

    assert status["overall"] == "not_watching"
    reason = status["problems"][0]["reason"]
    assert "tamper_suspected" not in reason, "our jargon leaked to the customer"
    assert "suspected interference" in reason


def test_a_member_with_no_accounts_is_not_told_everything_is_fine(db, rules):
    """Nothing is being watched. That is the truthful answer, and 'watching'
    would be a claim about a set that is empty."""
    status = member_status(db, "nobody@example.com", rules, now=NOW)

    assert status["overall"] == "not_watching"
    assert status["accounts"] == []


def test_the_isolation_boundary_holds(db, rules):
    """Status goes through `member_accounts` like every other portal query, so
    it cannot become the one page that leaks somebody else's account."""
    _account(db, "alice-1", "alice@example.com")
    _account(db, "bob-1", "bob@example.com")

    status = member_status(db, "alice@example.com", rules, now=NOW)

    assert [a["label"] for a in status["accounts"]] == ["alice-1"]


def test_an_unparseable_timestamp_is_not_read_as_fresh(db, rules):
    """A corrupt value must fail towards 'not watched'. Reading it as fresh
    would be the page claiming coverage it cannot support."""
    account_id = _account(db, "alice-1", "alice@example.com")
    with db.cursor() as cur:
        cur.execute("UPDATE poll_state SET last_success_at = 'not-a-date' WHERE account_id = ?",
                    (account_id,))

    status = member_status(db, "alice@example.com", rules, now=NOW)

    assert status["overall"] == "not_watching"


# --- the page ----------------------------------------------------------------

def test_the_page_states_the_limit_of_what_it_proves(db, rules):
    """The honest part. The page is served by the Guardian, so it cannot
    report the Guardian being down — a dead host looks exactly like a closed
    laptop. Claiming otherwise would make it the alarm it is not (#63)."""
    _account(db, "alice-1", "alice@example.com")

    page = _render_status(db, "alice@example.com", rules)

    assert "cannot tell you" in page
    assert "would not load at all" in page


def test_the_page_names_the_accounts_that_are_dark(db, rules):
    _account(db, "alice-dark", "alice@example.com", last_ok=NOW - timedelta(days=1))

    page = _render_status(db, "alice@example.com", rules)

    assert "alice-dark" in page
    assert "not watched" in page


def test_the_page_offers_the_key_replacement_only_where_it_is_the_answer(db, rules):
    """The replace-key link belongs on a rejected key and nowhere else —
    offering it for an unreachable exchange tells the customer to fix
    something that is not broken."""
    _account(db, "alice-authfail", "alice@example.com", auth_failures=3)
    unreachable = _render_status(db, "nobody@example.com", rules)
    page = _render_status(db, "alice@example.com", rules)

    assert "/key?id=" in page
    assert "/key?id=" not in unreachable


def test_the_page_escapes_account_labels(db, rules):
    _account(db, "<script>x</script>", "alice@example.com", last_ok=NOW - timedelta(days=1))

    page = _render_status(db, "alice@example.com", rules)

    assert "<script>x</script>" not in page
    assert "&lt;" in page
