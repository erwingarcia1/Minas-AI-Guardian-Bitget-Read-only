"""G-6 (#11): the customer is told when the Guardian loses access to them.

The detector existed; what it did with the finding didn't work.

`account_unreachable` fired at MEDIUM, and MEDIUM routes to `digest` only — so
"your account has stopped being watched" waited for a daily digest sent by a
host crontab that `docs/deployment.md` itself warns "fails silently every day
and the digest never sends". An account could stop being monitored and the
customer's first hint would be a digest that never arrived.

That is the fourth criterion inverted: silence looking exactly like safety.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sentinel.config import RulesConfig
from sentinel.detectors.heartbeat import detect_unreachable
from sentinel.onboarding import onboard_account
from sentinel.poller import AccountPoller
from tests.fixtures.ccxt_responses import account_info_response


class Recording:
    def __init__(self, name="chan"):
        self.name = name
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


def _client():
    c = MagicMock()
    c.fetch_deposits.return_value = []
    c.fetch_withdrawals.return_value = []
    c.fetch_closed_orders.return_value = []
    c.fetch_balance.return_value = {"total": {"BTC": 1.0}}
    c.fetch_account_info.return_value = account_info_response()
    c.fetch_tickers.return_value = {}
    c.fetch_transfers.return_value = []
    return c


@pytest.fixture
def account(db, cipher, monkeypatch):
    monkeypatch.setattr("sentinel.onboarding.make_client", lambda *a, **k: _client())
    result = onboard_account(db, cipher, label="conn", topology="independent",
                             api_key="k", secret="s", passphrase="p")
    with db.cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE id = ?", (result.account_id,))
        return result.account_id, cur.fetchone()


def _break_the_connection(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("sentinel.poller.make_client", broken)


# --- criterion 1: alerted within 15 minutes ----------------------------------

def test_an_unwatched_account_is_not_a_digest_item(db, cipher, monkeypatch, account):
    """The shipped routing sends MEDIUM to `digest` and nothing else. An
    account that has stopped being watched cannot wait for tomorrow."""
    _, row = account
    critical, high, digest_only = Recording("critical"), Recording("high"), Recording("digest")
    poller = AccountPoller(db, cipher, RulesConfig("config/rules.yaml"),
                           {"CRITICAL": [critical], "HIGH": [high],
                            "MEDIUM": [digest_only], "LOW": [digest_only]})
    _break_the_connection(monkeypatch)
    for _ in range(5):
        poller.run_fast_loop(row)

    delivered = {a.rule for a in critical.sent + high.sent}
    assert "account_unreachable" in delivered, (
        "the alert went only to the digest - the customer is not told for a day"
    )


def test_five_failed_polls_is_well_inside_fifteen_minutes():
    """The shipped threshold is 5 consecutive failures on a 45s fast loop -
    under four minutes. This pins the arithmetic behind criterion 1 so a later
    threshold change has to notice it."""
    rules = RulesConfig("config/rules.yaml")
    threshold = rules.get_int("detectors", "heartbeat", "consecutive_failures_threshold")
    interval = rules.get_seconds("polling", "fast_loop_seconds")
    assert threshold * interval <= 15 * 60


# --- criterion 2: the alert says what to do ----------------------------------

def test_the_alert_tells_the_customer_what_to_do():
    """Both variants must state the situation *and* the action. For a network
    failure the honest action is "nothing from you yet" - telling a customer to
    re-issue a working key because the exchange is down would be wrong advice,
    which is why this asserts an action rather than particular words."""
    for auth in (0, 5):
        message = detect_unreachable(5, 5, auth_failures=auth)[0].message.lower()
        assert "not being watched" in message, message
        assert ("re-issue" in message or "nothing is needed" in message), message


def test_an_auth_failure_says_something_different_from_a_network_failure():
    """"Your key was rejected" and "we can't reach the exchange" need different
    actions from the customer. One is theirs to fix; the other isn't."""
    auth = detect_unreachable(5, 5, auth_failures=5)[0].message.lower()
    network = detect_unreachable(5, 5, auth_failures=0)[0].message.lower()

    assert auth != network
    assert "key" in auth
    assert "exchange" in network


# --- criterion 4: repeats daily until fixed ----------------------------------

def test_it_repeats_while_the_connection_is_still_broken(db, cipher, monkeypatch, account):
    """"Silence must never look like safety." A single alert that scrolls out
    of a chat window leaves an unwatched account looking fine forever."""
    from sentinel.alerts.dispatch import dispatch

    _, row = account
    channel = Recording()
    account_id = row["id"]
    detection = detect_unreachable(5, 5)[0]
    cooldowns = {"CRITICAL": 15, "HIGH": 30, "MEDIUM": 720, "LOW": 1440}
    rule_cooldowns = {"account_unreachable": 1440}

    first = dispatch(db, {detection.severity: [channel]}, account_id, "conn",
                     detection, cooldowns, rule_cooldowns=rule_cooldowns)
    assert first is not None

    # Two hours later. Inside the daily cadence, well outside HIGH's 30 minutes -
    # so this distinguishes the two, which an immediate repeat could not.
    from datetime import datetime, timedelta, timezone
    backdated = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with db.cursor() as cur:
        cur.execute("UPDATE alerts SET sent_at = ? WHERE id = ?", (backdated, first))

    second = dispatch(db, {detection.severity: [channel]}, account_id, "conn",
                      detection, cooldowns, rule_cooldowns=rule_cooldowns)
    assert second is None, (
        "re-alerted two hours in - it is using HIGH's 30-minute cooldown, not the daily cadence"
    )


def test_the_repeat_cadence_is_daily_not_the_severity_default(db, cipher):
    """A HIGH cooldown of 30 minutes would page every half hour for as long as
    a key stays broken, which trains the customer to ignore it. Daily is the
    criterion."""
    rules = RulesConfig("config/rules.yaml")
    assert rules.get_int("alerting", "unreachable_repeat_minutes") == 1440


def test_the_poller_applies_the_daily_cadence(db, cipher, monkeypatch, account):
    """Wiring, not just the setting existing."""
    _, row = account
    channel = Recording()
    poller = AccountPoller(db, cipher, RulesConfig("config/rules.yaml"),
                           {"CRITICAL": [channel], "HIGH": [channel],
                            "MEDIUM": [channel], "LOW": [channel]})
    _break_the_connection(monkeypatch)
    for _ in range(12):
        poller.run_fast_loop(row)

    unreachable = [a for a in channel.sent if a.rule == "account_unreachable"]
    assert len(unreachable) == 1, (
        f"fired {len(unreachable)} times in twelve polls - the daily cadence is not applied"
    )


# --- criterion 3: shown as not watched wherever it appears -------------------

def test_the_dashboard_shows_the_account_as_not_watched(db, cipher, monkeypatch, account):
    from sentinel.dashboard import _render

    _, row = account
    poller = AccountPoller(db, cipher, RulesConfig("config/rules.yaml"),
                           {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []})
    _break_the_connection(monkeypatch)
    for _ in range(5):
        poller.run_fast_loop(row)

    page = _render(db).lower()
    assert "not being watched" in page or "not watched" in page


def test_the_member_portal_shows_the_account_as_not_watched(db, cipher, monkeypatch, account):
    """"Everywhere it appears" includes the page the customer looks at."""
    from sentinel.portal import _render_member_account

    account_id, row = account
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = 'c@example.com' WHERE id = ?", (account_id,))
    poller = AccountPoller(db, cipher, RulesConfig("config/rules.yaml"),
                           {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []})
    _break_the_connection(monkeypatch)
    for _ in range(5):
        poller.run_fast_loop(row)

    page = (_render_member_account(db, "c@example.com", account_id) or "").lower()
    assert "not being watched" in page or "not watched" in page


def test_a_rejected_key_produces_the_actionable_coverage_alert(db, cipher, monkeypatch, account):
    """G-6's actual case: "an expired or deleted key".

    Auth failures go through `_handle_auth_failure`, which fires the *tamper*
    signal - "you may be under attack" - and used not to check reachability at
    all. So the softer, actionable message this issue exists for could never
    fire from the path that produces it.

    Both should fire. They say different things and neither replaces the other:
    tamper is about attribution, this is about coverage."""
    from sentinel.exchange import TamperSignal

    _, row = account
    channel = Recording()
    poller = AccountPoller(db, cipher, RulesConfig("config/rules.yaml"),
                           {"CRITICAL": [channel], "HIGH": [channel],
                            "MEDIUM": [channel], "LOW": [channel]})

    def rejected(*a, **k):
        raise TamperSignal("api key does not exist")

    monkeypatch.setattr("sentinel.poller.make_client", rejected)
    for _ in range(5):
        poller.run_fast_loop(row)

    rules = {a.rule for a in channel.sent}
    assert "monitoring_key_tamper_suspected" in rules, "the tamper signal must still fire"
    assert "account_unreachable" in rules, (
        "a rejected key never produced the coverage alert - the case G-6 is about"
    )

    unreachable = next(a for a in channel.sent if a.rule == "account_unreachable")
    assert "re-issue" in unreachable.message.lower(), (
        "the customer was not told the actionable thing: their key needs replacing"
    )
