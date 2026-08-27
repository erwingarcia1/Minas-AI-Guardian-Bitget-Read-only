"""Decision 6 (2026-08-27), the interim: 24 months, configurable, on every
unbounded table - until the lawyer answers the crypto-shredding question.

The mechanism (scripts: sentinel/retention.py, hourly prune from the engine,
periods in config) predates the decision; what the decision adds is the
NUMBER as a commitment. This pins it: the defaults and the shipped config
both say 730 days for every long-lived record, the two shorter windows are
the two the retention doc argues for by name, and pruning covers every
table that grows.
"""
from __future__ import annotations

from pathlib import Path

from sentinel.config import RulesConfig
from sentinel.retention import KEEP_LATEST_PER_ACCOUNT, PRUNABLE
from sentinel.rules_schema import DEFAULTS

ROOT = Path(__file__).resolve().parent.parent
INTERIM_DAYS = 730  # 24 months
SHORTER_BY_DESIGN = {"dedup_days": 365, "balance_snapshots_days": 7}


def _settings():
    return {setting for _, setting in PRUNABLE.values()} | {setting for _, setting in KEEP_LATEST_PER_ACCOUNT.values()}


def test_the_defaults_commit_to_24_months_for_every_long_lived_record():
    for setting in _settings():
        expected = SHORTER_BY_DESIGN.get(setting, INTERIM_DAYS)
        assert DEFAULTS["retention"][setting] == expected, setting


def test_the_shipped_config_says_the_same():
    rules = RulesConfig(str(ROOT / "config" / "rules.yaml"))
    for setting in _settings():
        expected = SHORTER_BY_DESIGN.get(setting, INTERIM_DAYS)
        assert rules.get_int("retention", setting) == expected, setting


def test_the_number_is_config_not_code():
    """The lawyer's answer lands as a config change, not a deploy of code:
    nothing in retention.py carries the number."""
    src = (ROOT / "sentinel" / "retention.py").read_text()
    assert "730" not in src


def test_the_customer_facing_retention_doc_states_the_interim():
    doc = (ROOT / "docs" / "data-retention.md").read_text()
    assert "730 days" in doc
    # Honest about its status until the lawyer signs it off.
    assert "not yet reviewed" in doc.lower()
