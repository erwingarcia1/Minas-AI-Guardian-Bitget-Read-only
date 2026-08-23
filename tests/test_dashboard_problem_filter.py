"""#39's last criterion: sort and filter by problem state, server-side (the
#82 decision - query parameters and re-render, no table JS), so trouble
surfaces first.

The state is derived from exactly the judgements the page already renders
(the status pill, `_watch_cell`'s counter threshold) - a grouping of what
each row already says, never a third opinion.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sentinel.dashboard import _problem_state, _render


def _account(db, label, *, status="active", failures=0, auth_failures=0, last_ok=True):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, '2026-01-01', '2026-01-01')",
            (label, status))
        account_id = cur.lastrowid
        cur.execute(
            "INSERT INTO poll_state (account_id, last_success_at, consecutive_failures, "
            "consecutive_auth_failures) VALUES (?, ?, ?, ?)",
            (account_id, datetime.now(timezone.utc).isoformat() if last_ok else None,
             failures, auth_failures))
        return account_id


def _fleet(db):
    _account(db, "zz-watching")
    _account(db, "aa-trouble", failures=99)
    _account(db, "mm-paused", status="disabled")


# --- the derivation groups what the row already says ---------------------------

def test_problem_state_mirrors_the_pages_own_judgements():
    row = {"status": "disabled", "consecutive_failures": 0, "consecutive_auth_failures": 0}
    assert _problem_state(row, 5) == "paused"
    row = {"status": "tamper_suspected", "consecutive_failures": 0, "consecutive_auth_failures": 0}
    assert _problem_state(row, 5) == "trouble"
    row = {"status": "active", "consecutive_failures": 3, "consecutive_auth_failures": 2}
    assert _problem_state(row, 5) == "trouble", "the threshold is _watch_cell's, summed the same way"
    row = {"status": "active", "consecutive_failures": 3, "consecutive_auth_failures": 1}
    assert _problem_state(row, 5) == "watching"


# --- trouble surfaces first ----------------------------------------------------

def test_the_default_order_puts_trouble_first_and_paused_last(db):
    _fleet(db)
    page = _render(db)
    assert page.index("aa-trouble") < page.index("zz-watching") < page.index("mm-paused"), (
        "trouble first, deliberate stillness last - regardless of names")


def test_sort_by_label_is_available_and_alphabetical(db):
    _fleet(db)
    page = _render(db, qs={"sort": ["label"]})
    assert page.index("aa-trouble") < page.index("mm-paused") < page.index("zz-watching")
    assert "sorted by name" in page


# --- the filter -----------------------------------------------------------------

def test_filtering_to_trouble_shows_only_trouble(db):
    _fleet(db)
    page = _render(db, qs={"state": ["trouble"]})
    assert "aa-trouble" in page
    assert "zz-watching" not in page and "mm-paused" not in page
    assert "1 shown · 3 total" in page


def test_the_filter_links_carry_the_counts(db):
    _fleet(db)
    page = _render(db)
    assert "Trouble (1)" in page
    assert "Watching (1)" in page
    assert "Paused (1)" in page
    assert "All (3)" in page


def test_an_unrecognised_filter_selects_nothing_never_everything(db):
    """The house rule, applied here: a filter that cannot be understood must
    not show the whole fleet under a heading that claims a selection."""
    _fleet(db)
    page = _render(db, qs={"state": ["garbage"]})
    assert "No accounts match this filter" in page
    for label in ("aa-trouble", "zz-watching", "mm-paused"):
        assert label not in page.split("No accounts match")[1].split("</table>")[0]
    assert "0 shown · 3 total" in page
