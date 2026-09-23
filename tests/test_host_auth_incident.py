"""A simultaneous failure across accounts is one cause, not N (2026-09-23).

On 2026-09-21 at 19:10 UTC the host's public address changed. Nine accounts
whose keys were allowlisted to the old address failed authentication in the
same second, and the product told nine customers, hourly, that their key may
have been tampered with - 164 alerts, one outside customer told 17 times.
Every accusation was wrong, and the evidence was in the refusal: Bitget
answered `{"code":"40018","msg":"Invalid IP,Current request IP 57.181.51.14"}`.
"""
from __future__ import annotations

from sentinel.alerts.fleet import (
    FLEET_RULE,
    HOST_AUTH_RULE,
    TAMPER_RULE,
    auth_failure_starts,
    fleet_incident,
    host_auth_incident,
    in_host_auth_incident,
    observed_request_ip,
    page_fleet_incident,
)
from sentinel.alerts.watch_alarms import WatchAlarms
from sentinel.config import RulesConfig
from sentinel.exchange import TamperSignal
from sentinel.poller import AccountPoller
from tests.test_operator_alerting import rules_yaml

BITGET = 'bitget {"code":"40018","msg":"Invalid IP,Current request IP 57.181.51.14","requestTime":1758481800000,"data":null}'
BYBIT = 'bybit {"retCode":10010,"retMsg":"Unmatched IP, please check your API key\'s bound IP addresses.","result":{}}'
REFUSALS = {"bitget": BITGET, "bybit": BYBIT, "okx": "okx {\"code\":\"50110\",\"msg\":\"Invalid IP\"}",
            "binance": 'binance {"code":-2015,"msg":"Invalid API-key, IP, or permissions for action."}'}


class _Recording:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


class _Pager:
    def __init__(self):
        self.pages = []

    def page(self, **kw):
        self.pages.append(kw)
        return len(self.pages)


class _Refusing:
    """An exchange client whose every read is refused with the exchange's words."""

    def __init__(self, text):
        self._text = text

    def __getattr__(self, name):
        def refused(*a, **k):
            raise TamperSignal(self._text)
        return refused


def _account(db, label, exchange):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES (?, ?, 'independent', 'x', 'y', 'z', ?, 'active', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (label, exchange, f"{label}@example.com"))
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
        cur.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
        return account_id, cur.fetchone()


def _fleet(db, monkeypatch, *, channel, pager, spec=None):
    """Accounts across four exchanges whose keys are refused, and a poller
    with the client loader pointed at the refusals."""
    spec = spec or [("erwin-garcia", "bitget"), ("erwin-garcia-2", "bitget"), ("erwin-garcia-3", "bybit"),
                    ("erwin-garcia-4", "bybit"), ("erwin-primary-2", "bitget"), ("ezriel-garcia", "bitget"),
                    ("jimrox-odezi", "bitget"), ("john-great", "okx"), ("john-great-2", "binance")]
    rows = [_account(db, label, exchange) for label, exchange in spec]
    monkeypatch.setattr("sentinel.poller._load_client", lambda cipher, row: _Refusing(REFUSALS[row["exchange"]]))
    poller = AccountPoller(db, None, RulesConfig(rules_yaml()),
                           {"CRITICAL": [channel], "HIGH": [channel], "MEDIUM": [channel], "LOW": [channel]},
                           operator_alerter=pager)
    return rows, poller


def _alerts(db, rule):
    with db.cursor() as cur:
        cur.execute("SELECT account_id FROM alerts WHERE rule = ? AND repeat_of IS NULL", (rule,))
        return [r["account_id"] for r in cur.fetchall()]


def _events(db, rule):
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM events WHERE event_type = ?", (rule,))
        return cur.fetchone()["n"]


# --- (b) the refusal names our address --------------------------------------------

def test_bitgets_refusal_names_the_requesting_address_and_bybits_does_not():
    assert observed_request_ip(BITGET) == "57.181.51.14"
    assert observed_request_ip(BYBIT) is None
    assert observed_request_ip("api key does not exist") is None
    assert observed_request_ip(None) is None
    assert observed_request_ip("Invalid IP,Current request IP 2406:da14:1f2:3::a1") == "2406:da14:1f2:3::a1"
    assert observed_request_ip("connect to 10.0.0.1 failed: Invalid IP") is None, "an address not named as ours is not ours"


# --- (a) fleet sight at the tamper decision ----------------------------------------

def test_the_2026_09_21_shape_is_one_host_page_and_zero_tamper_accusations(db, monkeypatch, caplog):
    channel, pager = _Recording(), _Pager()
    rows, poller = _fleet(db, monkeypatch, channel=channel, pager=pager)
    for _ in range(2):  # the tamper threshold
        for _, row in rows:
            poller.run_fast_loop(row)

    assert _alerts(db, TAMPER_RULE) == [], "no customer is told their key was tampered with"
    assert [a.rule for a in channel.sent if a.rule == TAMPER_RULE] == []
    assert _events(db, TAMPER_RULE) == 9, "the detections are recorded, not sent"
    host = [p for p in pager.pages if p["rule"] == HOST_AUTH_RULE]
    assert len(host) == 1 and len(pager.pages) == 1
    page = host[0]
    assert page["evidence"]["count"] == 9 and page["evidence"]["observed_request_ips"] == "57.181.51.14"
    assert page["evidence"]["exchanges"] == "binance, bitget, bybit, okx"
    assert "57.181.51.14" in page["message"] and "IP allowlist" in page["message"]
    assert "one cause, not 9" in page["message"]
    assert "No customer has been told" in page["message"]
    assert all(label in page["message"] for label, _ in [(r["label"], None) for _, r in rows])
    assert page["evidence"]["refusals"]["bybit"].startswith("bybit ") and "bitget" in page["evidence"]["refusals"]
    assert any("address 57.181.51.14" in r.getMessage() for r in caplog.records if r.name == "sentinel.alerts.fleet"), (
        "the operator log names the observed address")


def test_the_page_is_sent_once_and_the_coverage_notices_still_go_out_without_a_second_page(db, monkeypatch):
    channel, pager = _Recording(), _Pager()
    rows, poller = _fleet(db, monkeypatch, channel=channel, pager=pager)
    rules = RulesConfig(rules_yaml())
    for _ in range(6):  # past the coverage threshold of five, and then some
        for _, row in rows:
            poller.run_fast_loop(row)
    assert len([p for p in pager.pages if p["rule"] == HOST_AUTH_RULE]) == 1
    assert sorted(_alerts(db, "account_unreachable")) == sorted(a for a, _ in rows), (
        "each customer is still told once that their monitoring stopped")
    assert _alerts(db, TAMPER_RULE) == []
    # The watch alarms see the coverage incident; its page is subsumed by the
    # host page. A fresh account keeps the sweep out of its "whole engine
    # behind" branch, which would return before the fleet code is reached.
    from datetime import datetime, timezone

    fine, _ = _account(db, "fine", "bitget")
    with db.cursor() as cur:
        cur.execute("UPDATE poll_state SET last_success_at = ? WHERE account_id = ?",
                    (datetime.now(timezone.utc).isoformat(), fine))
    assert fleet_incident(db, rules) is not None, "the coverage incident has formed"
    WatchAlarms(db, rules, pager).evaluate()
    assert [p["rule"] for p in pager.pages if p["rule"] in (FLEET_RULE, HOST_AUTH_RULE)] == [HOST_AUTH_RULE]
    assert [p["rule"] for p in pager.pages if p["rule"] == "checks_behind_schedule"] == []
    assert [p for p in pager.pages if p["rule"] == "account_unwatchable_too_long"] == [], "members are held"
    # Recorded as paged: a second sweep does not page it either.
    WatchAlarms(db, rules, pager).evaluate()
    assert [p["rule"] for p in pager.pages if p["rule"] == FLEET_RULE] == []


def test_an_isolated_refusal_still_accuses(db, monkeypatch):
    """The per-account tamper rule stays for the genuinely isolated case."""
    channel, pager = _Recording(), _Pager()
    rows, poller = _fleet(db, monkeypatch, channel=channel, pager=pager,
                          spec=[("alone", "bitget"), ("fine-1", "bybit"), ("fine-2", "okx")])
    alone_id, alone = rows[0]
    for _ in range(2):
        poller.run_fast_loop(alone)
    assert _alerts(db, TAMPER_RULE) == [alone_id]
    assert pager.pages == []


def test_two_together_are_below_the_bar_and_both_are_accused(db, monkeypatch):
    channel, pager = _Recording(), _Pager()
    rows, poller = _fleet(db, monkeypatch, channel=channel, pager=pager,
                          spec=[("a", "bitget"), ("b", "bybit"), ("c", "okx")])
    for _ in range(2):
        for _, row in rows[:2]:
            poller.run_fast_loop(row)
    assert sorted(_alerts(db, TAMPER_RULE)) == sorted(a for a, _ in rows[:2])
    assert host_auth_incident(db, RulesConfig(rules_yaml())) is None


def test_the_refusal_and_the_runs_start_are_recorded_and_a_success_ends_the_run(db, monkeypatch):
    channel, pager = _Recording(), _Pager()
    rows, poller = _fleet(db, monkeypatch, channel=channel, pager=pager, spec=[("a", "bitget")])
    account_id, row = rows[0]
    poller.run_fast_loop(row)
    with db.cursor() as cur:
        cur.execute("SELECT auth_failing_since, last_auth_error, last_auth_error_at FROM poll_state WHERE account_id = ?",
                    (account_id,))
        first = dict(cur.fetchone())
    assert first["auth_failing_since"] and first["last_auth_error"] == BITGET[:300] and first["last_auth_error_at"]
    poller.run_fast_loop(row)
    with db.cursor() as cur:
        cur.execute("SELECT auth_failing_since FROM poll_state WHERE account_id = ?", (account_id,))
        assert cur.fetchone()["auth_failing_since"] == first["auth_failing_since"], "the start of the run is kept"
    assert [r["id"] for r in auth_failure_starts(db)] == [account_id]

    class _Fine:
        def fetch_deposits(self, *a, **k):
            return []

        fetch_withdrawals = fetch_closed_orders = fetch_transfers = fetch_deposits

        def fetch_balance(self, *a, **k):
            return {"total": {}, "free": {}, "used": {}}

    monkeypatch.setattr("sentinel.poller._load_client", lambda cipher, row: _Fine())
    poller.run_fast_loop(row)
    assert auth_failure_starts(db) == [], "a successful check ends the run"


def test_a_member_that_recovers_leaves_the_incident_and_the_rest_can_be_accused_when_isolated(db, monkeypatch):
    channel, pager = _Recording(), _Pager()
    rows, poller = _fleet(db, monkeypatch, channel=channel, pager=pager,
                          spec=[("a", "bitget"), ("b", "bybit"), ("c", "okx")])
    rules = RulesConfig(rules_yaml())
    for _ in range(2):
        for _, row in rows:
            poller.run_fast_loop(row)
    assert _alerts(db, TAMPER_RULE) == [] and all(in_host_auth_incident(db, rules, a) for a, _ in rows)
    # b and c are fixed (their allowlists updated); a is still refused - now on its own.
    with db.cursor() as cur:
        cur.execute("UPDATE poll_state SET auth_failing_since = NULL, consecutive_auth_failures = 0 "
                    "WHERE account_id IN (?, ?)", (rows[1][0], rows[2][0]))
    assert host_auth_incident(db, rules) is None
    poller.run_fast_loop(rows[0][1])
    assert _alerts(db, TAMPER_RULE) == [rows[0][0]], "alone again, the tamper rule applies"


def test_the_page_names_customers_accused_before_the_cluster_formed(db, monkeypatch):
    """Failures that span sweeps can accuse the first account before three
    have failed; the page says who, so the operator can put it right."""
    channel, pager = _Recording(), _Pager()
    rows, poller = _fleet(db, monkeypatch, channel=channel, pager=pager,
                          spec=[("first", "bitget"), ("second", "bybit"), ("third", "okx")])
    for _ in range(2):
        poller.run_fast_loop(rows[0][1])  # first reaches the threshold alone
    assert _alerts(db, TAMPER_RULE) == [rows[0][0]]
    for _ in range(2):
        for _, row in rows[1:]:
            poller.run_fast_loop(row)
    page = [p for p in pager.pages if p["rule"] == HOST_AUTH_RULE][0]
    assert page["evidence"]["already_accused"] == "first" and "already sent" in page["message"]
    assert _alerts(db, TAMPER_RULE) == [rows[0][0]], "second and third are not accused"


def test_a_bybit_only_incident_says_no_address_was_named(db, monkeypatch, caplog):
    channel, pager = _Recording(), _Pager()
    rows, poller = _fleet(db, monkeypatch, channel=channel, pager=pager,
                          spec=[("a", "bybit"), ("b", "bybit"), ("c", "bybit")])
    for _ in range(2):
        for _, row in rows:
            poller.run_fast_loop(row)
    page = [p for p in pager.pages if p["rule"] == HOST_AUTH_RULE][0]
    assert page["evidence"]["observed_request_ips"] == "" and "No exchange named the requesting address" in page["message"]
    assert any("address unnamed" in r.getMessage() for r in caplog.records if r.name == "sentinel.alerts.fleet"), caplog.text


def test_the_window_edge_and_the_bar_are_the_coverage_incidents(db):
    """Same two settings, same clustering: three starts exactly a window
    apart are inside it, two are not enough."""
    rules = RulesConfig(rules_yaml())
    ids = [_account(db, f"acct-{i}", "bitget")[0] for i in range(3)]
    stamps = ["2026-09-21T19:10:00+00:00", "2026-09-21T19:15:00+00:00", "2026-09-21T19:20:00+00:00"]
    with db.cursor() as cur:
        for account_id, stamp in zip(ids, stamps, strict=True):
            cur.execute("UPDATE poll_state SET auth_failing_since = ?, last_auth_error = ? WHERE account_id = ?",
                        (stamp, BITGET, account_id))
    incident = host_auth_incident(db, rules)
    assert incident and incident["count"] == 3 and incident["key"] == "host-auth:2026-09-21T19:10"
    assert incident["observed_request_ips"] == ["57.181.51.14"]
    with db.cursor() as cur:
        cur.execute("UPDATE poll_state SET auth_failing_since = '2026-09-21T19:20:01+00:00' WHERE account_id = ?", (ids[2],))
    assert host_auth_incident(db, rules) is None, "one second past the window, two remain: not an incident"


def test_a_coverage_page_is_sent_when_its_members_are_not_a_paged_host_incident(db):
    """The subsumption is exact: an unrelated coverage incident still pages."""
    pager = _Pager()
    incident = {"key": "2026-09-21T19:14", "started_at": "2026-09-21T19:14:00+00:00", "account_ids": [1, 2, 3],
                "labels": ["a", "b", "c"], "count": 3, "window_seconds": 600.0, "min_accounts": 3}
    assert page_fleet_incident(db, pager, incident, rules=RulesConfig(rules_yaml())) is not None
    assert [p["rule"] for p in pager.pages] == [FLEET_RULE]


def test_membership_is_exact_a_stranger_is_not_in_an_open_incident(db):
    rules = RulesConfig(rules_yaml())
    ids = [_account(db, f"acct-{i}", "bitget")[0] for i in range(3)]
    stranger, _ = _account(db, "stranger", "okx")
    with db.cursor() as cur:
        for account_id in ids:
            cur.execute("UPDATE poll_state SET auth_failing_since = '2026-09-21T19:10:00+00:00', last_auth_error = ? "
                        "WHERE account_id = ?", (BITGET, account_id))
    assert all(in_host_auth_incident(db, rules, a) for a in ids)
    assert in_host_auth_incident(db, rules, stranger) is False
    with db.cursor() as cur:
        cur.execute("UPDATE poll_state SET auth_failing_since = NULL")
    assert in_host_auth_incident(db, rules, ids[0]) is False, "no incident, no member"


def test_the_refusal_sample_per_exchange_is_only_ever_the_exchanges_words(db):
    """One sample per exchange, and an exchange whose refusal text was not
    kept is absent - never the string 'None'."""
    rules = RulesConfig(rules_yaml())
    a, _ = _account(db, "a", "bitget")
    b, _ = _account(db, "b", "bitget")
    c, _ = _account(db, "c", "okx")
    with db.cursor() as cur:
        cur.execute("UPDATE poll_state SET auth_failing_since = '2026-09-21T19:10:00+00:00'")
        cur.execute("UPDATE poll_state SET last_auth_error = ? WHERE account_id IN (?, ?)", (BITGET, a, b))
    incident = host_auth_incident(db, rules)
    assert incident["refusals"] == {"bitget": BITGET[:160]}
    assert "None" not in str(incident["refusals"])


def _paged_host(db, rules, ids):
    from sentinel.alerts.fleet import page_host_auth_incident

    with db.cursor() as cur:
        for account_id in ids:
            cur.execute("UPDATE poll_state SET auth_failing_since = '2026-09-21T19:10:00+00:00', last_auth_error = ? "
                        "WHERE account_id = ?", (BITGET, account_id))
    incident = host_auth_incident(db, rules)
    assert incident and page_host_auth_incident(db, _Pager(), incident) is not None
    return incident


def _coverage(ids, key="2026-09-21T19:14"):
    return {"key": key, "started_at": f"{key}:00+00:00", "account_ids": list(ids),
            "labels": [f"acct-{i}" for i in ids], "count": len(ids), "window_seconds": 600.0, "min_accounts": 3}


def test_a_coverage_incident_of_exactly_the_paged_host_members_is_subsumed(db):
    rules = RulesConfig(rules_yaml())
    ids = [_account(db, f"acct-{i}", "bitget")[0] for i in range(3)]
    _paged_host(db, rules, ids)
    pager = _Pager()
    assert page_fleet_incident(db, pager, _coverage(ids), rules=rules) is None
    assert pager.pages == [], "equal member sets: the shared cause was already paged"
    assert page_fleet_incident(db, pager, _coverage(ids), rules=rules) is None, "recorded as paged"


def test_a_host_incident_not_yet_paged_does_not_subsume_the_coverage_page(db):
    rules = RulesConfig(rules_yaml())
    ids = [_account(db, f"acct-{i}", "bitget")[0] for i in range(3)]
    with db.cursor() as cur:
        for account_id in ids:
            cur.execute("UPDATE poll_state SET auth_failing_since = '2026-09-21T19:10:00+00:00', last_auth_error = ? "
                        "WHERE account_id = ?", (BITGET, account_id))
    assert host_auth_incident(db, rules) is not None
    pager = _Pager()
    assert page_fleet_incident(db, pager, _coverage(ids), rules=rules) is not None
    assert [p["rule"] for p in pager.pages] == [FLEET_RULE], "nothing has said the cause yet: the coverage page goes"


def test_without_rules_the_coverage_page_cannot_know_about_the_host_and_is_sent(db):
    rules = RulesConfig(rules_yaml())
    ids = [_account(db, f"acct-{i}", "bitget")[0] for i in range(3)]
    _paged_host(db, rules, ids)
    pager = _Pager()
    assert page_fleet_incident(db, pager, _coverage(ids)) is not None
    assert [p["rule"] for p in pager.pages] == [FLEET_RULE]


def test_a_coverage_incident_with_a_member_outside_the_host_incident_is_not_subsumed(db):
    rules = RulesConfig(rules_yaml())
    ids = [_account(db, f"acct-{i}", "bitget")[0] for i in range(3)]
    other, _ = _account(db, "other", "okx")
    _paged_host(db, rules, ids)
    pager = _Pager()
    assert page_fleet_incident(db, pager, _coverage(ids + [other]), rules=rules) is not None
    assert [p["rule"] for p in pager.pages] == [FLEET_RULE]


def test_members_coverage_notices_say_the_cause_is_ours_and_ask_for_nothing(db, monkeypatch):
    """The sentence the customer receives during a host-level incident: the
    key is not the problem, do nothing. Their copy carries the our-side
    advice, never 're-issue a read-only API key'."""
    from sentinel.alerts.base import CAUSE_KEY

    channel, pager = _Recording(), _Pager()
    rows, poller = _fleet(db, monkeypatch, channel=channel, pager=pager)
    for _ in range(5):
        for _, row in rows:
            poller.run_fast_loop(row)
    notices = [a for a in channel.sent if a.rule == "account_unreachable"]
    assert len(notices) == 9
    for alert in notices:
        assert alert.evidence[CAUSE_KEY] == HOST_AUTH_RULE
        assert alert.message.startswith("Your account is NOT being watched. The exchange is refusing our read-only key "
                                        "because of a change on our side")
        assert "Nothing is needed from you: leave your key exactly as it is." in alert.message
        assert "Re-issue" not in alert.message and "re-issue" not in alert.message
        body = alert.render_customer()
        assert "Leave your API key exactly as it is" in body and "Put a new read-only key" not in body
    with db.cursor() as cur:
        cur.execute("SELECT evidence_json FROM alerts WHERE rule = 'account_unreachable'")
        assert all(HOST_AUTH_RULE in (r["evidence_json"] or "") for r in cur.fetchall()), "the cause is stored"


def test_an_isolated_refusal_keeps_the_rules_own_coverage_advice(db, monkeypatch):
    channel, pager = _Recording(), _Pager()
    rows, poller = _fleet(db, monkeypatch, channel=channel, pager=pager, spec=[("alone", "bitget")])
    for _ in range(5):
        poller.run_fast_loop(rows[0][1])
    notice = [a for a in channel.sent if a.rule == "account_unreachable"][0]
    assert "cause" not in notice.evidence and "Re-issue a read-only API key" in notice.message
