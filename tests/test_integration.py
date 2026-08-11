from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from sentinel.config import RulesConfig
from sentinel.onboarding import onboard_account
from sentinel.poller import AccountPoller, _update_poll_state
from tests.fixtures.ccxt_responses import account_info_response, trade_record, withdrawal_record


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


def test_stale_sale_does_not_explain_a_fresh_drop(db, cipher, monkeypatch):
    """G-7: a large legitimate sale from a week ago must not explain away a
    theft-sized balance drop that happened inside the last 45-second cycle.

    The balance drop is measured between two consecutive fast-loop snapshots,
    but `fetch_closed_orders` is called with `since=None`, so it returns the
    exchange's full default history. Every one of those old fills is then
    counted as an explanation, which lets a real drain pass silently."""
    onboarding_client = _mock_client(balance_total={"BTC": 1.0})
    monkeypatch.setattr("sentinel.onboarding.make_client", lambda *a, **k: onboarding_client)
    result = onboard_account(
        db, cipher, label="acct-1", topology="independent", api_key="k", secret="s", passphrase="p"
    )

    # Pretend the previous fast loop ran 45s ago. This is what the off-market
    # and fee detectors already use to window their input; the quantity-drop
    # path ignores it. Setting it keeps those detectors out of the way so this
    # test isolates the drop path.
    now = datetime.now(timezone.utc)
    _update_poll_state(
        db, result.account_id, last_fast_loop_at=(now - timedelta(seconds=45)).isoformat()
    )

    # A legitimate 0.5 BTC sale from seven days ago. It was already settled and
    # already reflected in the baseline snapshot taken at onboarding, so it
    # cannot account for any part of a drop measured from that snapshot.
    stale_sale = trade_record(symbol="BTC/USDT", side="sell", amount="0.5", cost="30000")
    stale_sale["timestamp"] = int((now - timedelta(days=7)).timestamp() * 1000)

    # Balance falls 1.0 -> 0.5 BTC inside this cycle, with no withdrawal and no
    # fill in the window: a theft-sized, wholly unexplained drop.
    poll_client = _mock_client(balance_total={"BTC": 0.5}, fills=[stale_sale])
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
    assert "unexplained_quantity_decrease" in fired_rules, (
        "a 0.5 BTC drop in the last 45s was explained away by a sale from 7 days ago"
    )


def test_recent_sale_still_explains_a_drop(db, cipher, monkeypatch):
    """Converse of the test above, and the reason it can't pass vacuously: a
    sale that really did happen inside the window must still suppress the
    alert. Without this, a window computed so badly that nothing ever explains
    a drop would look like a passing fix while alerting on every legitimate
    trade."""
    onboarding_client = _mock_client(balance_total={"BTC": 1.0})
    monkeypatch.setattr("sentinel.onboarding.make_client", lambda *a, **k: onboarding_client)
    result = onboard_account(
        db, cipher, label="acct-1", topology="independent", api_key="k", secret="s", passphrase="p"
    )

    now = datetime.now(timezone.utc)
    _update_poll_state(
        db, result.account_id, last_fast_loop_at=(now - timedelta(seconds=45)).isoformat()
    )

    # Same 0.5 BTC drop, but the customer genuinely sold 10 seconds ago.
    fresh_sale = trade_record(symbol="BTC/USDT", side="sell", amount="0.5", cost="30000")
    fresh_sale["timestamp"] = int((now - timedelta(seconds=10)).timestamp() * 1000)

    poll_client = _mock_client(balance_total={"BTC": 0.5}, fills=[fresh_sale])
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
    assert "unexplained_quantity_decrease" not in fired_rules, (
        "a sale from 10s ago should still explain the drop it caused"
    )
