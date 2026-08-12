from __future__ import annotations

"""G-49 (#53): a paging route scoped to Minas rather than to a customer.

The gap was never "we can't reach ourselves" — `build_channels` already wires
our own Telegram, team group, SMS and email. It was that everything had to hang
off an account: `dispatch()` takes an `account_id`, `_dedup_key` is built from
it, and the `alerts` row carries it as a foreign key. So a product-level fault
had nowhere to be raised, and anything raised against a real account went
through `AccountPoller._emit` → `ContactRouter.send` and told that customer
about our problem.
"""

from unittest.mock import MagicMock

import pytest

from sentinel.alerts.operator import OperatorAlerter, recent_operator_pages
from sentinel.config import RulesConfig


class Recording:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


@pytest.fixture
def channels():
    return {"CRITICAL": [Recording()], "HIGH": [Recording()], "MEDIUM": [Recording()], "LOW": [Recording()]}


@pytest.fixture
def alerter(db, channels):
    return OperatorAlerter(db, channels, RulesConfig("config/rules.yaml"))


def _account(db, label="cust-1", email="customer@example.com"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, alert_phone, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, '+15550000000', 'active', "
            "'2026-01-01', '2026-01-01')",
            (label, email),
        )
        return cur.lastrowid


# --- a product-level page is a first-class thing -----------------------------

def test_a_page_can_be_raised_with_no_account(db, alerter, channels):
    """AC 1. The scheduler falling behind, an exchange dropping timestamps, a
    failed drill — none of these belong to a customer, and inventing a fake
    account row to carry them would pollute the dashboard, the digest and every
    per-account query."""
    page_id = alerter.page(
        rule="scheduler_behind",
        severity="CRITICAL",
        message="fast loop is 4 cycles behind",
        evidence={"lag_cycles": 4},
    )

    assert page_id is not None
    assert len(channels["CRITICAL"][0].sent) == 1
    assert channels["CRITICAL"][0].sent[0].rule == "scheduler_behind"


def test_a_page_with_no_account_is_stored_with_a_null_account(db, alerter):
    alerter.page(rule="scheduler_behind", severity="CRITICAL", message="behind")
    with db.cursor() as cur:
        cur.execute("SELECT account_id FROM operator_pages")
        assert cur.fetchone()["account_id"] is None


def test_pages_are_queryable_after_the_fact(db, alerter):
    """AC 4. "What broke last month" has to be answerable."""
    alerter.page(rule="scheduler_behind", severity="CRITICAL", message="behind")
    alerter.page(rule="exchange_format_change", severity="HIGH", message="bitget dropped timestamps")

    rules = [p["rule"] for p in recent_operator_pages(db)]
    assert sorted(rules) == ["exchange_format_change", "scheduler_behind"]


# --- never tells a customer --------------------------------------------------

def test_raising_a_page_never_notifies_a_customer(db, channels):
    """AC 2, and the reason this issue exists. `AccountPoller._emit` calls
    `dispatch()` *and* `ContactRouter.send()`, so anything raised against a real
    account reaches that customer's email and phone. An operator page must have
    no path to a customer at all — asserted by giving the alerter a router that
    fails the test if it is ever touched."""
    router = MagicMock()
    alerter = OperatorAlerter(db, channels, RulesConfig("config/rules.yaml"))

    account_id = _account(db)
    alerter.page(
        rule="account_unwatchable",
        severity="CRITICAL",
        message="key rejected by exchange - our egress IP may be blocked",
        account_id=account_id,
    )

    assert router.send.call_count == 0
    assert not hasattr(alerter, "_contact_router"), (
        "OperatorAlerter must not hold a customer contact router at all"
    )


def test_a_page_about_an_account_does_not_write_a_customer_alert(db, alerter):
    """The customer's own alert history must not fill up with our problems.
    Operator pages live in their own table."""
    account_id = _account(db)
    alerter.page(rule="account_unwatchable", severity="CRITICAL", message="ours not theirs",
                 account_id=account_id)

    with db.cursor() as cur:
        cur.execute("SELECT count(*) c FROM alerts")
        assert cur.fetchone()["c"] == 0
        cur.execute("SELECT count(*) c FROM operator_pages WHERE account_id = ?", (account_id,))
        assert cur.fetchone()["c"] == 1


def test_a_page_can_be_about_an_account(db, alerter):
    """AC 5, needed by G-6: a key breaks for a reason on *our* side — an
    exchange API change, our egress IP blocked — and telling the customer to go
    fix something that isn't theirs is worse than saying nothing."""
    account_id = _account(db)
    alerter.page(rule="account_unwatchable", severity="HIGH", message="ours", account_id=account_id)

    pages = recent_operator_pages(db)
    assert pages[0]["account_id"] == account_id
    assert pages[0]["account_label"] == "cust-1", "a page about an account should name it"


# --- separate dials ----------------------------------------------------------

def test_operator_cooldowns_are_independent_of_customer_severity_cooldowns(db, channels, tmp_path):
    """AC 3. Sharing `alerting.cooldown_minutes` would give "our product is
    broken" the 15 minutes chosen for a flapping detection on one customer's
    account. The two kinds of urgency need separate dials, and this proves
    turning one does not turn the other."""
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "alerting:\n"
        "  cooldown_minutes:\n"
        "    CRITICAL: '0'\n"          # customer side: never suppress
        "operator_alerting:\n"
        "  routing:\n"
        "    CRITICAL: [telegram]\n"
        "  cooldown_minutes:\n"
        "    CRITICAL: '600'\n"        # operator side: suppress hard
    )
    alerter = OperatorAlerter(db, channels, RulesConfig(rules_path))

    first = alerter.page(rule="scheduler_behind", severity="CRITICAL", message="behind")
    second = alerter.page(rule="scheduler_behind", severity="CRITICAL", message="behind")

    assert first is not None
    assert second is None, "the operator cooldown was not applied"
    assert len(channels["CRITICAL"][0].sent) == 1


def test_the_same_rule_on_different_accounts_pages_separately(db, alerter, channels):
    """Two accounts going unwatchable are two facts. Deduping them together
    would report the first and hide the rest of a fleet-wide outage."""
    a = _account(db, label="cust-a", email="a@example.com")
    b = _account(db, label="cust-b", email="b@example.com")

    alerter.page(rule="account_unwatchable", severity="HIGH", message="a", account_id=a)
    alerter.page(rule="account_unwatchable", severity="HIGH", message="b", account_id=b)

    assert len(channels["HIGH"][0].sent) == 2


def test_a_repeat_of_the_same_page_is_suppressed_within_the_cooldown(db, alerter, channels):
    alerter.page(rule="scheduler_behind", severity="CRITICAL", message="behind")
    suppressed = alerter.page(rule="scheduler_behind", severity="CRITICAL", message="behind")

    assert suppressed is None
    assert len(channels["CRITICAL"][0].sent) == 1


def test_a_page_with_no_configured_channel_is_still_recorded(db, tmp_path):
    """A page that can't be delivered must not also be a page that never
    happened. Recording it is what makes an undeliverable route discoverable
    afterwards instead of silently swallowed."""
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text("operator_alerting:\n  routing:\n    CRITICAL: [telegram]\n")
    alerter = OperatorAlerter(db, {}, RulesConfig(rules_path))

    page_id = alerter.page(rule="scheduler_behind", severity="CRITICAL", message="behind")

    assert page_id is not None
    assert len(recent_operator_pages(db)) == 1


def test_operator_routing_is_resolved_separately_from_customer_routing(monkeypatch, tmp_path):
    """AC 3, the destinations half. Retuning a customer's alert preferences
    must not change where we page ourselves about an outage."""
    from sentinel.cli import build_channels, build_operator_channels

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("TELEGRAM_TEAM_CHAT_ID", "2")

    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "alerting:\n"
        "  routing:\n"
        "    CRITICAL: [telegram]\n"
        "operator_alerting:\n"
        "  routing:\n"
        "    CRITICAL: [telegram, telegram_team]\n"
    )
    rules = RulesConfig(rules_path)

    assert len(build_channels(rules)["CRITICAL"]) == 1
    assert len(build_operator_channels(rules)["CRITICAL"]) == 2


def test_pages_are_shown_on_the_dashboard_apart_from_customer_alerts(db, alerter):
    """AC 4's other half — recorded *and* visible. Kept in its own section
    because an operator scanning the page has to tell "a customer may be being
    robbed" from "our product is broken" at a glance."""
    from sentinel.dashboard import _render

    assert "Operator pages" not in _render(db)

    alerter.page(rule="scheduler_behind", severity="CRITICAL", message="fast loop is behind")
    page = _render(db)

    assert "Operator pages" in page
    assert "scheduler_behind" in page


def test_an_undelivered_page_is_marked_as_such_on_the_dashboard(db, tmp_path):
    """A page nobody received should not read like one that was answered."""
    from sentinel.dashboard import _render

    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text("operator_alerting:\n  routing:\n    CRITICAL: [telegram]\n")
    OperatorAlerter(db, {}, RulesConfig(rules_path)).page(
        rule="scheduler_behind", severity="CRITICAL", message="behind"
    )

    assert "undelivered" in _render(db)


def test_acknowledging_a_page_records_it_once(db, alerter):
    from sentinel.alerts.operator import acknowledge_operator_page

    page_id = alerter.page(rule="scheduler_behind", severity="CRITICAL", message="behind")

    assert acknowledge_operator_page(db, page_id) is True
    assert acknowledge_operator_page(db, page_id) is False, "an ack must keep its original time"


def test_operator_alerting_config_is_accepted_by_the_schema():
    """The new section has to be declared in `rules_schema`, or G-12's
    validation rejects the very file that configures this."""
    rules = RulesConfig("config/rules.yaml")
    assert rules.is_valid
    assert rules.get("operator_alerting", "routing", "CRITICAL")
