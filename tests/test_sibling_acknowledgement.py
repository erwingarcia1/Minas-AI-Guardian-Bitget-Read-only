"""#99: acknowledging an alert acknowledges its siblings - the other
detectors' views of the same customer action - through the same writers,
so closing notes, first-claim rules and the escalation stop fire for each.

The definition under test is deliberately conservative (same account +
same non-empty order_id + 24h): the measured case is #369/#370 sharing
order 424427676 seven seconds apart, one acknowledged and one left
escalating about a handled incident.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sentinel.alerts.siblings import sibling_alert_ids
from sentinel.operators import acknowledge_alert
from sentinel.portal import acknowledge_alert_as_member
from tests.conftest import make_account

NOW = datetime.now(timezone.utc)


def _alert(db, account_id, *, rule="withdrawal_initiated", order_id="424427676",
           severity="CRITICAL", offset_s=0, acked=False):
    import json

    when = (NOW + timedelta(seconds=offset_s)).isoformat()
    evidence = {"order_id": order_id} if order_id is not None else {}
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at, delivered_to, acknowledged_at, acknowledged_by) "
            "VALUES (?, ?, ?, ?, 'm', ?, 'high', ?, 1, ?, ?)",
            (account_id, rule, severity, f"k{rule}{offset_s}", json.dumps(evidence), when,
             when if acked else None, "earlier" if acked else None))
        return cur.lastrowid


def _ack_state(db, alert_id):
    with db.cursor() as cur:
        cur.execute("SELECT acknowledged_at, acknowledged_by FROM alerts WHERE id = ?", (alert_id,))
        return dict(cur.fetchone())


@pytest.fixture
def notes(monkeypatch):
    """Every closing note fired, by alert id - the decision-1 loop must
    close for each co-acknowledged CRITICAL."""
    fired = []
    monkeypatch.setattr("sentinel.alerts.escalation.send_critical_ack_closing_note",
                        lambda db, alert_id, **kw: fired.append(alert_id))
    return fired


# --- the measured case, replayed ----------------------------------------------

def test_the_jaypee_shape_acknowledges_both_views_of_one_withdrawal(db, notes):
    account_id = make_account(db)
    initiated = _alert(db, account_id, rule="withdrawal_initiated")
    unseen = _alert(db, account_id, rule="withdrawal_to_unseen_address", offset_s=7)

    assert acknowledge_alert(db, unseen, "erwin") is True

    assert _ack_state(db, initiated)["acknowledged_at"] is not None, (
        "the sibling stays open and escalates about a handled incident - the bug")
    assert _ack_state(db, initiated)["acknowledged_by"] == "erwin", (
        "one claim, one name, both views")
    assert sorted(notes) == sorted([unseen, initiated]), (
        "decision 1's closing note fires for every co-acknowledged CRITICAL")


# --- the definition's edges: false negatives are the safe direction -----------

def test_a_different_order_is_never_co_acknowledged(db, notes):
    account_id = make_account(db)
    a = _alert(db, account_id, order_id="111")
    b = _alert(db, account_id, order_id="222", offset_s=3)
    acknowledge_alert(db, a, "erwin")
    assert _ack_state(db, b)["acknowledged_at"] is None


def test_no_order_reference_claims_no_sameness(db, notes):
    account_id = make_account(db)
    a = _alert(db, account_id, order_id=None)
    b = _alert(db, account_id, order_id=None, offset_s=3)
    acknowledge_alert(db, a, "erwin")
    assert _ack_state(db, b)["acknowledged_at"] is None
    assert sibling_alert_ids(db, a) == []


def test_another_accounts_alert_is_untouchable_even_with_the_same_order_id(db, notes):
    mine = make_account(db, "acct-mine")
    theirs = make_account(db, "acct-theirs")
    a = _alert(db, mine)
    b = _alert(db, theirs, offset_s=3)
    acknowledge_alert(db, a, "erwin")
    assert _ack_state(db, b)["acknowledged_at"] is None


def test_outside_the_24h_window_is_not_a_sibling(db, notes):
    account_id = make_account(db)
    a = _alert(db, account_id)
    b = _alert(db, account_id, offset_s=25 * 3600)
    assert sibling_alert_ids(db, a) == []
    acknowledge_alert(db, a, "erwin")
    assert _ack_state(db, b)["acknowledged_at"] is None


def test_an_already_acknowledged_sibling_keeps_its_first_claim(db, notes):
    account_id = make_account(db)
    a = _alert(db, account_id)
    b = _alert(db, account_id, offset_s=3, acked=True)
    acknowledge_alert(db, a, "erwin")
    assert _ack_state(db, b)["acknowledged_by"] == "earlier", (
        "the second person to open the incident does not become the first")


def test_numeric_and_string_order_ids_still_match(db, notes):
    """Exchanges have sent the reference both ways; a type-strict compare
    would silently split real siblings."""
    import json

    account_id = make_account(db)
    a = _alert(db, account_id)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at, delivered_to) VALUES (?, 'r2', 'HIGH', 'kn', 'm', ?, 'high', ?, 1)",
            (account_id, json.dumps({"order_id": 424427676}), NOW.isoformat()))
        numeric = cur.lastrowid
    assert sibling_alert_ids(db, a) == [numeric]


# --- the member writer co-acks through its own scoped statement ----------------

def test_member_acknowledgement_carries_the_siblings_in_scope(db, notes):
    account_id = make_account(db)
    a = _alert(db, account_id)
    b = _alert(db, account_id, rule="withdrawal_to_unseen_address", offset_s=7)

    assert acknowledge_alert_as_member(db, a, "alice@example.com", [account_id]) is True

    state = _ack_state(db, b)
    assert state["acknowledged_at"] is not None
    assert state["acknowledged_by"] == "alice@example.com"
    assert sorted(notes) == sorted([a, b])


def test_exactly_24h_apart_is_still_inside_the_window(db, notes):
    """The window is inclusive at its edge: `> window` skips, `>= window`
    would not, and the difference is one alert left open."""
    account_id = make_account(db)
    a = _alert(db, account_id)
    b = _alert(db, account_id, offset_s=24 * 3600)
    assert sibling_alert_ids(db, a) == [b]


def test_an_out_of_window_candidate_does_not_end_the_scan(db, notes):
    """A `break` where a `continue` belongs would let one old alert hide
    every sibling inserted after it."""
    account_id = make_account(db)
    a = _alert(db, account_id)
    _alert(db, account_id, offset_s=-30 * 3600)
    c = _alert(db, account_id, offset_s=5)
    assert sibling_alert_ids(db, a) == [c]


def test_a_malformed_candidate_does_not_end_the_scan(db, notes):
    account_id = make_account(db)
    a = _alert(db, account_id)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at, delivered_to) VALUES (?, 'r', 'HIGH', 'kbad', 'm', ?, 'high', ?, 1)",
            (account_id, "not json", NOW.isoformat()))
    c = _alert(db, account_id, offset_s=5)
    assert sibling_alert_ids(db, a) == [c]


def test_an_empty_order_reference_claims_no_sameness_either(db, notes):
    """`""` is as absent as None. Treating it as an identifier would make
    every alert with a blank order_id a sibling of every other."""
    account_id = make_account(db)
    a = _alert(db, account_id, order_id="")
    b = _alert(db, account_id, order_id="", offset_s=3)
    assert sibling_alert_ids(db, a) == []
    acknowledge_alert(db, a, "erwin")
    assert _ack_state(db, b)["acknowledged_at"] is None
