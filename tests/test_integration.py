from __future__ import annotations

from unittest.mock import MagicMock

from sentinel.config import RulesConfig
from sentinel.onboarding import onboard_account
from sentinel.poller import AccountPoller
from tests.fixtures.ccxt_responses import account_info_response, withdrawal_record


class RecordingChannel:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


def _mock_client(deposits=None, withdrawals=None, fills=None, balance_total=None, authorities="spot_account_r"):
    client = MagicMock()
    client.fetch_deposits.return_value = deposits or []
    client.fetch_withdrawals.return_value = withdrawals or []
    client.fetch_closed_orders.return_value = fills or []
    client.fetch_balance.return_value = {"total": balance_total or {}}
    client.fetch_account_info.return_value = account_info_response(authorities=authorities)
    return client


def test_end_to_end_new_withdrawal_to_unseen_address_fires_critical(db, cipher, monkeypatch):
    """Onboards a fake account, then runs one fast-loop cycle where a brand
    new withdrawal to a never-seen address shows up - both the
    withdrawal-created and unseen-address detectors should fire, get
    logged to the append-only event log, and reach the alert channel."""
    onboarding_client = _mock_client(balance_total={"BTC": 1.0})
    monkeypatch.setattr("sentinel.onboarding.make_client", lambda *a, **k: onboarding_client)
    result = onboard_account(
        db, cipher, label="acct-1", topology="independent", api_key="k", secret="s", passphrase="p"
    )

    poll_client = _mock_client(
        balance_total={"BTC": 1.0},
        withdrawals=[withdrawal_record(order_id="wd-new", address="bc1qneveraddress", amount="0.1")],
    )
    monkeypatch.setattr("sentinel.poller.make_client", lambda *a, **k: poll_client)

    channel = RecordingChannel()
    rules = RulesConfig("config/rules.yaml")
    poller = AccountPoller(
        db, cipher, rules, {"CRITICAL": [channel], "HIGH": [channel], "MEDIUM": [channel], "LOW": [channel]}
    )

    with db.cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE id = ?", (result.account_id,))
        account_row = cur.fetchone()

    poller.run_fast_loop(account_row)

    fired_rules = {a.rule for a in channel.sent}
    assert "withdrawal_initiated" in fired_rules
    assert "withdrawal_to_unseen_address" in fired_rules

    with db.cursor() as cur:
        cur.execute("SELECT event_type FROM events WHERE account_id = ?", (result.account_id,))
        event_types = {r["event_type"] for r in cur.fetchall()}
    assert "withdrawal_initiated" in event_types
    assert "withdrawal_to_unseen_address" in event_types


def test_end_to_end_repeat_withdrawal_does_not_refire(db, cipher, monkeypatch):
    """A withdrawal already seen in a prior cycle must not fire again."""
    onboarding_client = _mock_client(balance_total={"BTC": 1.0})
    monkeypatch.setattr("sentinel.onboarding.make_client", lambda *a, **k: onboarding_client)
    result = onboard_account(
        db, cipher, label="acct-2", topology="independent", api_key="k", secret="s", passphrase="p"
    )

    existing_withdrawal = withdrawal_record(order_id="wd-existing", address="bc1qexistingaddress", amount="0.1")
    poll_client = _mock_client(balance_total={"BTC": 1.0}, withdrawals=[existing_withdrawal])
    monkeypatch.setattr("sentinel.poller.make_client", lambda *a, **k: poll_client)

    channel = RecordingChannel()
    rules = RulesConfig("config/rules.yaml")
    poller = AccountPoller(
        db, cipher, rules, {"CRITICAL": [channel], "HIGH": [channel], "MEDIUM": [channel], "LOW": [channel]}
    )

    with db.cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE id = ?", (result.account_id,))
        account_row = cur.fetchone()

    poller.run_fast_loop(account_row)  # first sighting -> fires
    poller.run_fast_loop(account_row)  # same withdrawal again -> must not refire

    withdrawal_alerts = [a for a in channel.sent if a.rule == "withdrawal_initiated"]
    assert len(withdrawal_alerts) == 1
