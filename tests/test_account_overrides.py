"""#17 (G-13): per-account sensitivity - any threshold overridable for one
customer, defaults for everyone else, and the effective values shown to
the member in plain terms.

The overlay is the whole design: `AccountRules` answers with the account's
override when one exists and falls through to the global config
otherwise, through the same read API - so a detector call site cannot
tell which layer answered, and an account with no overrides behaves
exactly as before. A registration test holds that every threshold read in
the poller goes through the overlay, because one direct read is one
customer whose override silently doesn't apply.
"""
from __future__ import annotations

import argparse
import re
from decimal import Decimal
from pathlib import Path

import pytest

from sentinel.config import RulesConfig
from sentinel.overrides import (
    AccountRules,
    clear_override,
    overrides_for,
    set_override,
    validate_setting,
)

ROOT = Path(__file__).resolve().parent.parent
EMAIL = "alice@example.com"


def _account(db, label="acct-o1", email=EMAIL):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', "
            "'2026-01-01', '2026-01-01')", (label, email))
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
        return account_id


@pytest.fixture
def rules():
    return RulesConfig("config/rules.yaml")


# --- criteria 1 + 2: override wins, everyone else gets the defaults -----------

def test_an_override_wins_and_its_absence_falls_through(db, rules):
    overridden = _account(db, "tuned")
    untouched = _account(db, "standard")
    set_override(db, "tuned", "detectors.withdrawal_burst.min_count", "9", None)

    assert AccountRules(rules, db, overridden).get_int(
        "detectors", "withdrawal_burst", "min_count", default=3) == 9
    assert AccountRules(rules, db, untouched).get_int(
        "detectors", "withdrawal_burst", "min_count", default=3) == rules.get_int(
        "detectors", "withdrawal_burst", "min_count", default=3), (
        "an account without an override must be indistinguishable from before")


def test_decimal_and_seconds_reads_coerce_the_stored_text(db, rules):
    account_id = _account(db)
    set_override(db, "acct-o1", "detectors.off_market.max_deviation_pct", "2.5", None)
    ar = AccountRules(rules, db, account_id)
    assert ar.get_decimal("detectors", "off_market", "max_deviation_pct",
                          default=Decimal("10")) == Decimal("2.5")


def test_a_corrupt_override_falls_back_to_the_global_value_not_to_nothing(db, rules):
    account_id = _account(db)
    set_override(db, "acct-o1", "detectors.withdrawal_burst.min_count", "3", None)
    with db.cursor() as cur:
        cur.execute("UPDATE account_threshold_overrides SET value = 'garbage'")
    got = AccountRules(rules, db, account_id).get_int(
        "detectors", "withdrawal_burst", "min_count", default=3)
    assert got == rules.get_int("detectors", "withdrawal_burst", "min_count", default=3), (
        "corruption must weaken sensitivity to the DEFAULT, never disable the read")


# --- the write path -----------------------------------------------------------

def test_only_schema_declared_scalars_are_overridable(db):
    _account(db)
    with pytest.raises(ValueError, match="no place in the schema"):
        set_override(db, "acct-o1", "detectors.imaginary.threshold", "1", None)
    with pytest.raises(ValueError, match="not a threshold"):
        set_override(db, "acct-o1", "alerting.routing.CRITICAL", "x", None)
    with pytest.raises(ValueError, match="dotted"):
        validate_setting("nonsense")


def test_changes_are_evented_per_diff_and_clearing_is_recorded(db):
    import json

    account_id = _account(db)
    set_override(db, "acct-o1", "detectors.withdrawal_burst.min_count", "9", "erwin")
    set_override(db, "acct-o1", "detectors.withdrawal_burst.min_count", "9", "erwin")  # no-op
    clear_override(db, "acct-o1", "detectors.withdrawal_burst.min_count", "erwin")
    assert clear_override(db, "acct-o1", "detectors.withdrawal_burst.min_count", "erwin") is False

    with db.cursor() as cur:
        cur.execute("SELECT event_type, payload_json, actor FROM events "
                    "WHERE account_id = ? ORDER BY id", (account_id,))
        events = [(r["event_type"], json.loads(r["payload_json"]), r["actor"]) for r in cur]
    assert [e[0] for e in events] == ["threshold_override_changed"] * 2, (
        "one event per actual change - the no-op and the second clear record nothing")
    assert events[0][1]["to"] == "9" and events[0][2] == "erwin"
    assert events[1][1]["to"] is None, "clearing is the change a customer reads /activity to find"
    assert overrides_for(db, account_id) == {}


def test_the_cli_reaches_the_same_path_attributed(db, monkeypatch):
    from sentinel.cli import cmd_set_threshold

    account_id = _account(db)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    ns = argparse.Namespace(setting="detectors.withdrawal_burst.min_count", value="7",
                            account="acct-o1", clear=False, as_operator=None)
    assert cmd_set_threshold(ns) == 0
    assert overrides_for(db, account_id) == {"detectors.withdrawal_burst.min_count": "7"}

    ns = argparse.Namespace(setting="detectors.withdrawal_burst.min_count", value=None,
                            account="acct-o1", clear=True, as_operator=None)
    assert cmd_set_threshold(ns) == 0
    assert overrides_for(db, account_id) == {}

    ns = argparse.Namespace(setting="detectors.nope.x", value="1",
                            account="acct-o1", clear=False, as_operator=None)
    assert cmd_set_threshold(ns) == 2


# --- the poller reads through the overlay, everywhere -------------------------

def test_every_poller_threshold_read_goes_through_the_overlay():
    """Registration: one `self._rules.get` left in the poller is one customer
    whose override silently doesn't apply. The single allowed reference is
    the assignment the overlay itself wraps."""
    source = (ROOT / "sentinel" / "poller.py").read_text()
    reads = re.findall(r"self\._rules\.get\w*\(", source)
    assert reads == [], f"direct threshold reads bypass the overlay: {reads}"
    assert "self._account_rules(account_id)" in source


def test_an_override_changes_detection_for_that_account_only(db, cipher, rules):
    """The criterion end to end: the heartbeat threshold overridden to 2
    fires the unreachable alert at 2 consecutive failures for the tuned
    account, while the standard account stays quiet at the same count."""
    from sentinel.poller import AccountPoller

    tuned = _account(db, "tuned2")
    standard = _account(db, "standard2")
    set_override(db, "tuned2", "detectors.heartbeat.consecutive_failures_threshold", "2", None)

    poller = AccountPoller(db, cipher, rules, {})
    poller._handle_generic_failure(tuned, "tuned2", {"consecutive_failures": 1}, RuntimeError("x"))
    poller._handle_generic_failure(standard, "standard2", {"consecutive_failures": 1}, RuntimeError("x"))

    with db.cursor() as cur:
        cur.execute("SELECT account_id FROM alerts WHERE rule = 'account_unreachable'")
        alerted = {r["account_id"] for r in cur.fetchall()}
    assert tuned in alerted, "the tuned account must alert at its own threshold"
    assert standard not in alerted, "the standard account keeps the default threshold"


# --- criterion 3: the member sees their effective sensitivity ------------------

def test_the_limits_page_shows_effective_values_with_the_custom_marker(db, rules):
    from sentinel.portal import _render_limits

    _account(db, "tuned3")
    set_override(db, "tuned3", "detectors.off_market.max_deviation_pct", "2.5", None)
    page = _render_limits(db, EMAIL, rules)

    assert "Your alert sensitivity" in page
    assert "more than 2.5% away" in page
    assert "set for this account" in page
    assert "standard" in page, "un-overridden rows say so"
