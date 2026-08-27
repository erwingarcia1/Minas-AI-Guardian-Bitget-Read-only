"""G-43 (#47): what we keep is published where a customer can read it.

The policy document is flagged for legal review and stays that way — that
criterion needs a lawyer, not a commit. What engineering can close is the gap
between practice and disclosure: retention runs hourly against config, and
until now nothing a customer could reach said what is kept or for how long.

The page derives from the same config the pruner enforces, so the two cannot
disagree. These tests pin the derivation and the honesty — the disclosure must
say the numbers are practice awaiting review, not reviewed policy, for the
same reason the limits page must not claim prevention (#48): an overstated
guarantee is worse than a stated limitation.
"""

from __future__ import annotations

import pytest

from sentinel.config import RulesConfig
from sentinel.portal import _render_limits, retention_summary


@pytest.fixture
def rules():
    return RulesConfig("config/rules.yaml")


def test_the_summary_reads_the_live_config_not_a_literal(tmp_path, rules):
    """Change the config, the page changes. A disclosure that keeps saying
    two years after an operator shortens the window to one is a disclosure
    that lies in exactly the way this page exists to prevent."""
    from pathlib import Path

    custom = tmp_path / "rules.yaml"
    base = Path("config/rules.yaml").read_text()
    custom.write_text(base.replace('events_days: "730"', 'events_days: "365"')
                          .replace('alerts_days: "730"', 'alerts_days: "365"'))
    changed = RulesConfig(str(custom))

    default_line = dict(retention_summary(rules))["Activity records and alert history"]
    changed_line = dict(retention_summary(changed))["Activity records and alert history"]

    assert "2 years" in default_line
    assert "1 year" in changed_line


def test_zero_means_kept_indefinitely_not_zero_days(rules):
    """`0` is the config's documented "keep forever". Rendering it as
    "0 days" would tell a customer their history vanishes immediately."""
    from unittest.mock import patch

    with patch.object(RulesConfig, "get_int", return_value=0):
        lines = dict(retention_summary(rules))
    assert "kept indefinitely" in lines["Activity records and alert history"]


def test_every_pruned_table_is_represented_in_customer_terms():
    """The pin that keeps the grouping honest. Every setting the pruner
    enforces must be behind one of the customer-facing lines, so a table
    added to PRUNABLE without a disclosure decision fails here rather than
    silently not being mentioned.

    The mapping is deliberately coarse — customers get "activity records",
    not table names — so this asserts coverage of the *settings*, which is
    what actually controls deletion.
    """
    from sentinel.retention import KEEP_LATEST_PER_ACCOUNT, PRUNABLE

    # Which retention settings each disclosure line covers. A new table
    # reusing a covered setting (as alert_triage reuses alerts_days) is
    # covered automatically; a new *setting* must be added here and to the
    # page together.
    disclosed_settings = {
        "events_days", "alerts_days", "operator_pages_days",
        "sms_deliveries_days", "expected_activity_days", "config_changes_days",
        "password_resets_days", "snoozes_days",       # activity records line
        "balance_snapshots_days", "permission_snapshots_days",  # snapshots line
        "dedup_days",                                  # dedup line
        "drills_days",                                 # fire-drill results line (#37)
    }
    enforced = {setting for _, setting in {**PRUNABLE, **KEEP_LATEST_PER_ACCOUNT}.values()}

    undisclosed = enforced - disclosed_settings
    assert undisclosed == set(), (
        f"retention settings the customer page says nothing about: {sorted(undisclosed)}. "
        "Add the table's data to a disclosure line in retention_summary (or a new "
        "line), then add its setting here."
    )


def test_the_page_says_these_are_practice_not_reviewed_policy(db, rules):
    """The legal review has not happened (#47's open criterion). Publishing
    the numbers without saying so would convert engineering defaults into an
    implied promise — the overstatement this page family exists to avoid."""
    page = _render_limits(db, "alice@example.com", rules)

    assert "What we keep, and for how long" in page
    assert "not yet reviewed policy" in page
    assert "reads the same configuration the deletion job runs on" in page


def test_the_page_shows_the_windows(db, rules):
    page = _render_limits(db, "alice@example.com", rules)

    assert "2 years" in page          # events/alerts at the shipped 730
    assert "7 days" in page           # balance snapshots
    assert "until your account is deleted" in page
