"""Onboarding that cannot be left half-done, and keys locked to Minas's
address (decision 17's stage 1, 2026-10-28; the owner: keys are added only
by an admin, during onboarding).

| Risk | Defence |
|---|---|
| The exchange fails mid-seed: an active, half-seeded account with no `customer_added` entry, behind a form saying onboarding failed | the account and every row the seed wrote are taken back; the admin adds it again |
| A key open to every address: whoever obtains it reads the account from anywhere; Bybit lets it lapse after 90 days | the steps tell the customer to restrict it to Minas's address, from the setting; onboarding warns the admin |
| A refusal from a guess about an exchange's response shape | the check warns, never refuses |
"""
from __future__ import annotations

import urllib.parse
from unittest.mock import MagicMock

import pytest

from sentinel.egress import (
    ALSO_ELSEWHERE,
    ELSEWHERE,
    LOCKED,
    OPEN,
    UNCHECKED,
    egress_phrase,
    key_ip_lock,
)
from sentinel.keyinstructions import KEY_INSTRUCTIONS, instructions_for
from sentinel.onboarding import onboard_account
from tests.fixtures.ccxt_responses import account_info_response

MINAS = "57.181.51.14"


@pytest.fixture
def egress(monkeypatch):
    monkeypatch.setenv("SENTINEL_EGRESS_IPS", MINAS)
    return MINAS


def _client(ips=MINAS, *, fail=None):
    client = MagicMock()
    client.exchange_id = "bitget"
    client.fetch_balance.return_value = {"total": {"USDT": 100}}
    client.fetch_account_info.return_value = account_info_response(authorities="spot_account_r", ips=ips)
    client.fetch_deposits.return_value = [{"address": "addr-1", "currency": "USDT"}]
    client.fetch_withdrawals.return_value = []
    client.fetch_closed_orders.return_value = []
    client.fetch_transfers.return_value = []
    if fail in ("fetch_balance", "fetch_account_info"):
        # The read-only check calls these first, before anything is written;
        # the seed's call is the second.
        first = getattr(client, fail).return_value
        getattr(client, fail).side_effect = [first, RuntimeError("the exchange went away")]
    elif fail:
        getattr(client, fail).side_effect = RuntimeError("the exchange went away")
    return client


def _onboard(db, cipher, monkeypatch, client, label="jane"):
    monkeypatch.setattr("sentinel.onboarding.make_client", lambda *a, **k: client)
    return onboard_account(db, cipher, label=label, topology="independent", api_key="k", secret="s",
                           passphrase="p", exchange="bitget", alert_email="jane@example.com", actor="erwin")


def _count(db, table, account_id=None):
    with db.cursor() as cur:
        if account_id is None:
            cur.execute(f"SELECT count(*) AS n FROM {table}")
        else:
            column = "id" if table == "accounts" else "account_id"
            cur.execute(f"SELECT count(*) AS n FROM {table} WHERE {column} = ?", (account_id,))
        return cur.fetchone()["n"]


# --- a seed that fails takes the account back ----------------------------------------

@pytest.mark.parametrize("fail", ["fetch_balance", "fetch_deposits", "fetch_withdrawals", "fetch_account_info"])
def test_a_seed_the_exchange_breaks_leaves_nothing_behind(db, cipher, monkeypatch, fail):
    with pytest.raises(RuntimeError):
        _onboard(db, cipher, monkeypatch, _client(fail=fail))
    for table in ("accounts", "poll_state", "balance_snapshots", "known_addresses", "seen_withdrawals",
                  "permission_snapshots", "events"):
        assert _count(db, table) == 0, table


def test_the_account_can_be_added_again_after_a_failed_seed(db, cipher, monkeypatch):
    with pytest.raises(RuntimeError):
        _onboard(db, cipher, monkeypatch, _client(fail="fetch_withdrawals"))
    result = _onboard(db, cipher, monkeypatch, _client())
    assert _count(db, "accounts") == 1 and _count(db, "known_addresses", result.account_id) == 1
    with db.cursor() as cur:
        cur.execute("SELECT event_type FROM events WHERE account_id = ?", (result.account_id,))
        assert "customer_added" in [r["event_type"] for r in cur.fetchall()]


def test_a_best_effort_seed_gap_still_onboards(db, cipher, monkeypatch):
    """The order history stays best-effort, as before: a gap is recorded and
    the account kept."""
    result = _onboard(db, cipher, monkeypatch, _client(fail="fetch_closed_orders"))
    assert _count(db, "accounts", result.account_id) == 1
    with db.cursor() as cur:
        cur.execute("SELECT event_type FROM events WHERE account_id = ?", (result.account_id,))
        assert "baseline_seed_incomplete" in [r["event_type"] for r in cur.fetchall()]


@pytest.fixture
def dashboard(db, cipher):
    from sentinel.dashboard import serve_dashboard
    from tests.test_member_account_grants import _sign_in_ops

    srv = serve_dashboard(db, cipher, 0)
    port = srv.server_address[1]
    yield port, _sign_in_ops(db, cipher, port)
    srv.shutdown()
    srv.server_close()


def _add(port, cookie):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/add", urllib.parse.urlencode({
        "name": "Jane Doe", "exchange": "bitget", "topology": "independent", "email": "jane@example.com",
        "passphrase": "p", "api_key": "k", "secret": "s"}),
        {"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie})
    response = conn.getresponse()
    body = response.read().decode()
    conn.close()
    return response.status, body


def test_a_failed_seed_on_the_add_form_says_nothing_was_added(db, dashboard, monkeypatch):
    monkeypatch.setattr("sentinel.onboarding.make_client", lambda *a, **k: _client(fail="fetch_withdrawals"))
    status, body = _add(*dashboard)
    assert status == 500 and "Nothing was added: the account can be added again." in body
    assert _count(db, "accounts") == 0


# --- the IP lock -------------------------------------------------------------------------

def test_the_phrase_comes_from_the_setting(monkeypatch):
    monkeypatch.delenv("SENTINEL_EGRESS_IPS", raising=False)
    assert egress_phrase() == "the address Minas gives you"
    monkeypatch.setenv("SENTINEL_EGRESS_IPS", f" {MINAS} , 203.0.113.9 ,{MINAS}")
    assert egress_phrase() == f"{MINAS} or 203.0.113.9"


@pytest.mark.parametrize("exchange, authorities, ips, state", [
    ("bitget", "spot_account_r", MINAS, LOCKED),
    ("bitget", "spot_account_r", "", OPEN),
    ("bitget", "spot_account_r", None, OPEN),
    ("bitget", "spot_account_r", f"{MINAS},198.51.100.7", ALSO_ELSEWHERE),
    ("bitget", "spot_account_r", "198.51.100.7", ELSEWHERE),
    ("okx", "read_only", MINAS, LOCKED),
    ("bybit", ["ContractTrade"], [MINAS], LOCKED),
    ("bybit", ["ContractTrade"], ["*"], OPEN),
    ("bybit", ["ContractTrade"], [], OPEN),
    ("binance", ["enableReading", "ipRestrict"], None, UNCHECKED),
    ("binance", ["enableReading"], None, OPEN),
])
def test_each_exchanges_allowlist_reads_as_one_state(egress, exchange, authorities, ips, state):
    got, sentence = key_ip_lock(exchange, authorities, ips)
    assert got == state and sentence


def test_an_open_bybit_key_is_told_it_lapses(egress):
    assert "90 days" in key_ip_lock("bybit", [], [])[1]
    assert "90 days" not in key_ip_lock("bitget", "", "")[1]


def test_without_the_setting_an_allowlist_is_unchecked_but_an_open_key_is_still_open(monkeypatch):
    monkeypatch.delenv("SENTINEL_EGRESS_IPS", raising=False)
    assert key_ip_lock("bitget", "spot_account_r", MINAS)[0] == UNCHECKED
    state, sentence = key_ip_lock("bitget", "spot_account_r", "")
    assert state == OPEN and "the address Minas gives you" in sentence


def test_onboarding_reports_the_lock_from_the_keys_own_answer(db, cipher, monkeypatch, egress):
    assert _onboard(db, cipher, monkeypatch, _client(ips=MINAS)).ip_lock == LOCKED
    open_key = _onboard(db, cipher, monkeypatch, _client(ips=""), label="open")
    assert open_key.ip_lock == OPEN and MINAS in open_key.ip_lock_note


def test_an_open_key_is_added_with_a_warning_on_the_page(db, dashboard, monkeypatch, egress):
    monkeypatch.setattr("sentinel.onboarding.make_client", lambda *a, **k: _client(ips=""))
    status, body = _add(*dashboard)
    assert status == 200 and "Verified read-only and added" in body
    assert '<div class="banner err">⚠ This key is not restricted to an IP address' in body
    assert _count(db, "accounts") == 1, "a warning, never a refusal"


def test_a_locked_key_gets_no_warning(db, dashboard, monkeypatch, egress):
    monkeypatch.setattr("sentinel.onboarding.make_client", lambda *a, **k: _client(ips=MINAS))
    _, body = _add(*dashboard)
    assert "⚠" not in body


# --- the key instructions -----------------------------------------------------------------

def test_every_exchanges_steps_restrict_the_key_to_minass_address(egress):
    for exchange in KEY_INSTRUCTIONS:
        steps = instructions_for(exchange)["steps"]
        assert any(f"enter Minas's address, {MINAS}, as the only one" in step for step in steps), exchange
        assert not any("{egress}" in step for step in steps), exchange


def test_without_the_setting_the_steps_send_the_customer_to_minas(monkeypatch):
    monkeypatch.delenv("SENTINEL_EGRESS_IPS", raising=False)
    steps = instructions_for("bybit")["steps"]
    assert any("enter Minas's address, the address Minas gives you" in step for step in steps)


def test_the_stored_instructions_keep_the_placeholder():
    """Filled at reading time, so a change of address is a change of setting."""
    for entry in KEY_INSTRUCTIONS.values():
        assert any("{egress}" in step for step in entry["steps"])
