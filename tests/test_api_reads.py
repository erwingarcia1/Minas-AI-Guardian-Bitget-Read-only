"""What the app reads (decision 21d): the member's accounts, alerts, one
incident, the activity record and the limits page - each from the portal's
own query for the same page, and never one row past the member's scope.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sentinel import api as api_module
from sentinel.events import append_event
from sentinel.expected import mark_expected
from sentinel.portal import (
    LIMITS_CANNOT,
    LIMITS_DOES,
    incident_record,
    member_status,
    retention_summary,
    serve_portal,
)
from tests.conftest import PINNED_TOTP_NOW
from tests.helpers.api_client import ApiClient, add_account, add_alert, enrolled_member

ALICE = "alice@example.com"
BOB = "bob@example.com"
KIN = "kin@example.com"


@pytest.fixture
def server(db, cipher, rules, monkeypatch):
    monkeypatch.setattr(api_module, "send_new_device_email", lambda db, address, name: None)
    srv = serve_portal(db, 0, host="127.0.0.1", cipher=cipher, rules=rules)
    yield ApiClient(srv.server_address[1])
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def fleet(db):
    """Alice is primary on two accounts (one named by her) and Kin is the
    emergency contact on the first; Bob has one of his own."""
    a1 = add_account(db, ALICE, "alice-1", secondary=KIN)
    a2 = add_account(db, ALICE, "alice-2")
    b1 = add_account(db, BOB, "bob-1")
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET member_name = 'Savings' WHERE id = ?", (a2,))
        cur.execute("UPDATE poll_state SET last_success_at = ? WHERE account_id = ?",
                    (datetime.now(timezone.utc).isoformat(), a1))
    # Bob's own record, which Alice must never see.
    append_event(db, b1, "alert_contact_updated", "LOW",
                 {"email_from": None, "email_to": BOB}, actor="operator")
    alerts = {
        "a1_critical": add_alert(db, a1, sent_at="2026-08-28T10:00:00+00:00", message="Withdrawal initiated"),
        "a2_high": add_alert(db, a2, rule="withdrawal_burst", severity="HIGH",
                             sent_at="2026-08-29T10:00:00+00:00", message="Several withdrawals"),
        "a2_unsent": add_alert(db, a2, rule="off_market_execution", severity="HIGH", delivered_to=0,
                               sent_at="2026-08-30T10:00:00+00:00", message="Off-market fill"),
        "b1_critical": add_alert(db, b1, sent_at="2026-08-28T11:00:00+00:00", message="Bob's withdrawal"),
    }
    return {"a1": a1, "a2": a2, "b1": b1, **alerts}


def _sign_in(server, db, cipher, email):
    secret = enrolled_member(db, cipher, email)
    status, tokens, _ = server.sign_in(email, secret, at=PINNED_TOTP_NOW)
    assert status == 200, tokens
    return tokens["access_token"]


@pytest.fixture
def alice(server, db, cipher, fleet):
    return _sign_in(server, db, cipher, ALICE)


# --- accounts ----------------------------------------------------------------------

def test_accounts_are_the_members_own_with_watch_state_and_role(server, db, alice, fleet, rules):
    status, body, _ = server.request("GET", "/api/v1/accounts", token=alice)
    assert status == 200
    by_id = {a["id"]: a for a in body["accounts"]}
    assert set(by_id) == {fleet["a1"], fleet["a2"]}
    assert by_id[fleet["a1"]]["name"] == "alice-1" and by_id[fleet["a2"]]["name"] == "Savings"
    assert by_id[fleet["a1"]]["watched"] is True and by_id[fleet["a1"]]["reason"] is None
    assert by_id[fleet["a2"]]["watched"] is False
    assert by_id[fleet["a2"]]["reason"] == "this account has never completed a check"
    assert {a["role"] for a in body["accounts"]} == {"primary"}
    assert {a["exchange"] for a in body["accounts"]} == {"bitget"}
    assert body["overall"] == "degraded"


def test_accounts_say_what_the_status_page_says(server, db, alice, rules):
    _, body, _ = server.request("GET", "/api/v1/accounts", token=alice)
    page = member_status(db, ALICE, rules)
    assert body["overall"] == page["overall"]
    assert [(a["id"], a["watched"], a["reason"]) for a in body["accounts"]] == [
        (a["account_id"], a["watched"], a["reason"]) for a in page["accounts"]]


def test_an_emergency_contact_reads_only_the_account_they_cover_as_non_primary(server, db, cipher, fleet):
    kin = _sign_in(server, db, cipher, KIN)
    _, body, _ = server.request("GET", "/api/v1/accounts", token=kin)
    assert [(a["id"], a["role"]) for a in body["accounts"]] == [(fleet["a1"], "non-primary")]


# --- alerts ------------------------------------------------------------------------

def test_alerts_are_the_members_own_newest_first(server, alice, fleet):
    status, body, _ = server.request("GET", "/api/v1/alerts", token=alice)
    assert status == 200
    assert [a["id"] for a in body["alerts"]] == [fleet["a2_unsent"], fleet["a2_high"], fleet["a1_critical"]]
    assert body["total"] == 3 and body["offset"] == 0
    first = body["alerts"][0]
    assert first["account_id"] == fleet["a2"] and first["account_name"] == "Savings"
    assert first["delivered_to"] == 0  # reached nobody (#57), and says so


@pytest.mark.parametrize("query, expected", [
    ("severity=critical", ["a1_critical"]),
    ("severity=HIGH", ["a2_unsent", "a2_high"]),
    ("from=2026-08-29", ["a2_unsent", "a2_high"]),
    ("to=2026-08-28", ["a1_critical"]),
    ("from=2026-08-29&to=2026-08-29", ["a2_high"]),
    ("account={a1}", ["a1_critical"]),
    # Someone else's account intersects to nothing; it is never ignored.
    ("account={b1}", []),
    ("limit=1&offset=1", ["a2_high"]),
])
def test_alert_filters_narrow_within_the_members_scope(server, alice, fleet, query, expected):
    status, body, _ = server.request("GET", "/api/v1/alerts?" + query.format(**fleet), token=alice)
    assert status == 200
    assert [a["id"] for a in body["alerts"]] == [fleet[k] for k in expected]


@pytest.mark.parametrize("query", [
    "severity=BOGUS", "from=2026-13-40", "to=yesterday", "account=abc", "account=-1",
    "limit=0", "limit=201", "limit=ten", "offset=-1",
])
def test_a_filter_that_cannot_be_understood_is_refused_not_ignored(server, alice, query):
    status, body, _ = server.request("GET", "/api/v1/alerts?" + query, token=alice)
    assert status == 400 and body["error"] == "invalid_filter"


# --- one incident ------------------------------------------------------------------

def test_an_incident_is_the_incident_pages_own_record(server, db, alice, fleet):
    with db.cursor() as cur:
        for coin, qty, at in (("BTC", "1.50000000", "2026-08-28T09:59:00+00:00"),
                              ("BTC", "0.25000000", "2026-08-28T10:01:00+00:00")):
            cur.execute("INSERT INTO balance_snapshots (account_id, coin, quantity, snapshot_at) "
                        "VALUES (?, ?, ?, ?)", (fleet["a1"], coin, qty, at))
    mark_expected(db, fleet["a1_critical"], fleet["a1"], "withdrawal_initiated",
                  f"{fleet['a1']}:withdrawal_initiated:2026-08-28T10:00:00+00:00", ALICE, note="that was me")

    status, body, _ = server.request("GET", f"/api/v1/alerts/{fleet['a1_critical']}", token=alice)
    assert status == 200
    assert body == incident_record(db, ALICE, fleet["a1_critical"])
    # The steps the email and the page carry (tests/test_incident_advice.py
    # pins those two to one function; this pins the app to the page).
    assert body["advice"]["steps"]
    assert body["balances_before"] == [{"coin": "BTC", "quantity": "1.50000000",
                                        "snapshot_at": "2026-08-28T09:59:00+00:00"}]
    assert body["balances_after"][0]["quantity"] == "0.25000000"  # a string, never a float
    assert [m["note"] for m in body["expected_marks"]] == ["that was me"]
    assert body["account_name"] == "alice-1" and body["exchange"] == "bitget"


def test_an_incident_never_rounds_nobody_up_to_notified(server, alice, fleet):
    _, body, _ = server.request("GET", f"/api/v1/alerts/{fleet['a2_unsent']}", token=alice)
    assert body["delivered_to"] == 0


def test_a_rule_without_advice_carries_none(server, db, alice, fleet):
    quiet = add_alert(db, fleet["a1"], rule="password_changed", severity="LOW",
                      sent_at="2026-08-31T00:00:00+00:00", message="notice")
    _, body, _ = server.request("GET", f"/api/v1/alerts/{quiet}", token=alice)
    assert body["advice"] is None


def test_someone_elses_incident_is_indistinguishable_from_none(server, alice, fleet):
    theirs = server.request("GET", f"/api/v1/alerts/{fleet['b1_critical']}", token=alice)
    nothing = server.request("GET", "/api/v1/alerts/999999", token=alice)
    assert theirs[0] == nothing[0] == 404
    assert theirs[1] == nothing[1]


# --- activity ------------------------------------------------------------------------

def test_activity_is_the_members_own_record_in_plain_words(server, db, alice, fleet):
    status, body, _ = server.request("GET", "/api/v1/activity", token=alice)
    assert status == 200
    signed_in = [e for e in body["events"] if e["type"] == "member_app_signed_in"]
    assert {e["account_id"] for e in signed_in} == {fleet["a1"], fleet["a2"]}
    savings = [e for e in signed_in if e["account_id"] == fleet["a2"]][0]
    assert savings["account_name"] == "Savings"
    assert savings["title"] == "Phone app signed in"
    assert savings["detail"] == f"login {ALICE} · Test phone"  # plain text, not HTML
    assert savings["actor"] == ALICE
    assert all(e["account_id"] != fleet["b1"] for e in body["events"])
    assert all(BOB not in e["detail"] for e in body["events"])


def test_the_activity_page_names_a_connection_by_the_members_name(db, alice):
    """#8: the member's name for a connection is what every page shows. The
    activity page's query never fetched it, so it showed the slug."""
    from sentinel.portal import _render_activity

    assert "Savings" in _render_activity(db, ALICE)


# --- limits -----------------------------------------------------------------------

def test_limits_are_the_pages_sentences_and_the_running_numbers(server, db, alice, fleet, rules):
    from sentinel.portal import _render_limits

    status, body, _ = server.request("GET", "/api/v1/limits", token=alice)
    assert status == 200
    page = _render_limits(db, ALICE, rules)
    for item in body["does"] + body["cannot"]:
        assert item["title"] in page
    assert [(i["title"], i["text"]) for i in body["does"]] == list(LIMITS_DOES)
    assert [(i["title"], i["text"], i["see_status"]) for i in body["cannot"]] == list(LIMITS_CANNOT)
    assert body["headline"]["title"] in page and body["headline"]["text"] in page
    assert body["timing"] == {"balances_every_seconds": rules.get_int("polling", "fast_loop_seconds"),
                              "permissions_every_seconds": rules.get_int("polling", "slow_loop_seconds")}
    assert [(r["what"], r["how_long"]) for r in body["retention"]] == retention_summary(rules)
    assert body["retention_reviewed"] is False
    assert "not yet reviewed policy" in page
    assert [s["account_id"] for s in body["sensitivity"]] == [fleet["a1"], fleet["a2"]]
    first = body["sensitivity"][0]["thresholds"][0]
    assert first["title"] in page and first["phrase"] in page
    assert first["set_for_this_account"] is False


def test_a_limit_never_claims_to_prevent_anything():
    """#48, for the app's copy too: the same sentences, so the same rule."""
    for title, text, *_ in (*LIMITS_DOES, *LIMITS_CANNOT):
        for word in ("protect", "prevent", "secure your", "stop the"):
            assert word not in (title + text).lower(), (word, title)


# --- every read is scoped ------------------------------------------------------------

def _account_ids(node, inside_accounts=False):
    """Every account id anywhere in a response: `account_id` fields, and `id`
    on the entries of an `accounts` list."""
    found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "account_id" or (key == "id" and inside_accounts):
                found.add(value)
            found |= _account_ids(value, key == "accounts")
    elif isinstance(node, list):
        for item in node:
            found |= _account_ids(item, inside_accounts)
    return found


@pytest.mark.parametrize("path", ["/api/v1/accounts", "/api/v1/alerts", "/api/v1/activity", "/api/v1/limits"])
def test_no_read_shows_another_members_account(server, alice, fleet, path):
    _, body, _ = server.request("GET", path, token=alice)
    ids = _account_ids(body)
    assert ids, "the scan found no account ids, so it proves nothing"
    assert ids <= {fleet["a1"], fleet["a2"]}
    assert "bob-1" not in str(body) and "Bob's withdrawal" not in str(body)


def test_a_stale_access_token_reads_nothing(server, alice, monkeypatch):
    import time

    base = time.time()
    monkeypatch.setattr(api_module, "_clock", lambda: base + 3600)
    assert server.request("GET", "/api/v1/accounts", token=alice)[0] == 401
