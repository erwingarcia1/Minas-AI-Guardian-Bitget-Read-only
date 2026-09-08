"""Severity by address history (item 2, 2026-09-08).

A withdrawal to an address THIS ACCOUNT HAS WITHDRAWN TO BEFORE is not a
CRITICAL. It is a receipt: money left, to a destination the customer chose
once already. Measured 2026-09-08: 60 of the 119 unacknowledged withdrawal
CRITICALs were to such addresses, on 18 account-address pairs.

Fail safe: only known_addresses rows with source = 'withdrawal_history'
count. A deposit-only address, a record with no address, or an address with
no history stays CRITICAL. The unseen-address and burst rules are untouched.
The customer is still told - in the digest, not by a page.
"""
from __future__ import annotations

import pytest

from sentinel.alerts.digest import build_digest, build_member_digest, set_digest_frequency
from sentinel.config import RulesConfig
from sentinel.detectors.base import Detection
from sentinel.detectors.withdrawal import (
    FAMILIAR_DESTINATION_NOTE,
    detect_new_withdrawals,
    reclassify_familiar_destinations,
)
from sentinel.onboarding import onboard_account
from sentinel.poller import AccountPoller
from sentinel.portal import set_member_password
from tests.test_alert_preferences import EMAIL, _RecEmail, _RecSms, _router
from tests.test_connection_broken import Recording, _client, account_info_response  # noqa: F401 - fixtures reused

FAMILIAR = "bc1q-familiar"
DEPOSIT_ONLY = "bc1q-deposit-only"
NEW = "bc1q-never-seen"


def _withdrawal(ref, address, amount=0.5):
    return {"id": ref, "currency": "BTC", "amount": amount, "status": "success", "address": address,
            "timestamp": 1_756_000_000_000}


# --- the function: exact, fail safe, scoped to one rule ---------------------------------

def test_a_familiar_destination_becomes_a_medium_receipt_with_the_words_on_it():
    det = detect_new_withdrawals([_withdrawal("w1", FAMILIAR)])
    out = reclassify_familiar_destinations(det, {FAMILIAR})
    assert out[0].severity == "MEDIUM" and out[0].receipt is True
    assert out[0].evidence["destination"] == FAMILIAR_DESTINATION_NOTE
    assert out[0].message.endswith(FAMILIAR_DESTINATION_NOTE)
    assert out[0].rule == "withdrawal_initiated", "the rule is the same; the severity moved"


@pytest.mark.parametrize("address, familiar", [
    (NEW, {FAMILIAR}),          # no withdrawal history for this address
    (None, {FAMILIAR}),         # no address at all
    ("", {FAMILIAR}),           # blank address
    (DEPOSIT_ONLY, set()),      # the caller built the set from withdrawal_history only: deposit-only is unknown
    (FAMILIAR, set()),          # no history at all
])
def test_anything_short_of_proven_familiar_stays_critical(address, familiar):
    det = detect_new_withdrawals([_withdrawal("w1", address)])
    out = reclassify_familiar_destinations(det, familiar)
    assert out[0].severity == "CRITICAL" and out[0].receipt is False
    assert "destination" not in out[0].evidence


def test_every_detection_in_the_batch_is_judged_on_its_own():
    """A non-familiar withdrawal, or another rule, ahead of a familiar one
    must not end the judging: each detection is its own decision. (The
    mutant this pins: `continue` -> `break`.)"""
    batch = [
        Detection(rule="withdrawal_burst", severity="HIGH", confidence="high", message="m", evidence={}),
        *detect_new_withdrawals([_withdrawal("w-new", NEW), _withdrawal("w-none", None), _withdrawal("w-fam", FAMILIAR)]),
    ]
    out = reclassify_familiar_destinations(batch, {FAMILIAR})
    assert [(d.rule, d.severity) for d in out] == [
        ("withdrawal_burst", "HIGH"), ("withdrawal_initiated", "CRITICAL"),
        ("withdrawal_initiated", "CRITICAL"), ("withdrawal_initiated", "MEDIUM")]


def test_other_rules_pass_through_untouched():
    others = [
        Detection(rule="withdrawal_to_unseen_address", severity="CRITICAL", confidence="high", message="m",
                  evidence={"address": FAMILIAR}),
        Detection(rule="withdrawal_burst", severity="HIGH", confidence="high", message="m", evidence={}),
    ]
    out = reclassify_familiar_destinations(others, {FAMILIAR})
    assert [(d.rule, d.severity, d.receipt) for d in out] == [
        ("withdrawal_to_unseen_address", "CRITICAL", False), ("withdrawal_burst", "HIGH", False)]


# --- the poller: withdrawal history only, and never the withdrawal itself ---------------

@pytest.fixture
def watched(db, cipher, monkeypatch):
    client = _client()
    monkeypatch.setattr("sentinel.onboarding.make_client", lambda *a, **k: client)
    monkeypatch.setattr("sentinel.poller.make_client", lambda *a, **k: client)
    result = onboard_account(db, cipher, label="fam", topology="independent",
                             api_key="k", secret="s", passphrase="p")
    with db.cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE id = ?", (result.account_id,))
        row = cur.fetchone()
        cur.execute("INSERT INTO known_addresses (account_id, address, coin, source, first_seen_at) VALUES (?, ?, 'BTC', ?, ?)",
                    (result.account_id, FAMILIAR, "withdrawal_history", "2026-01-01T00:00:00+00:00"))
        cur.execute("INSERT INTO known_addresses (account_id, address, coin, source, first_seen_at) VALUES (?, ?, 'BTC', ?, ?)",
                    (result.account_id, DEPOSIT_ONLY, "deposit_history", "2026-01-01T00:00:00+00:00"))
    return result.account_id, row, client


def _poller(db, cipher, chans):
    return AccountPoller(db, cipher, RulesConfig("config/rules.yaml"), chans)


def _alerts(db, account_id):
    with db.cursor() as cur:
        cur.execute("SELECT rule, severity, evidence_json FROM alerts WHERE account_id = ? ORDER BY id", (account_id,))
        return [(r["rule"], r["severity"]) for r in cur.fetchall()]


def test_the_poller_judges_against_withdrawal_history_only(db, cipher, watched):
    account_id, row, client = watched
    client.fetch_withdrawals.return_value = [
        _withdrawal("w-fam", FAMILIAR), _withdrawal("w-dep", DEPOSIT_ONLY), _withdrawal("w-new", NEW),
        _withdrawal("w-none", None),
    ]
    critical, digest = Recording("critical"), Recording("digest")
    _poller(db, cipher, {"CRITICAL": [critical], "HIGH": [critical], "MEDIUM": [digest], "LOW": [digest]}).run_fast_loop(row)
    got = _alerts(db, account_id)
    assert ("withdrawal_initiated", "MEDIUM") in got, "the familiar destination is a receipt"
    assert got.count(("withdrawal_initiated", "CRITICAL")) == 3, "deposit-only, never-seen and no-address stay CRITICAL"
    # The unseen-address rule is untouched: NEW is unseen (CRITICAL), the
    # deposit-only address is known to it (as before), no address is the LOW gap.
    assert ("withdrawal_to_unseen_address", "CRITICAL") in got
    assert ("unseen_address_check_degraded", "LOW") in got
    unseen = [r for r in got if r[0] == "withdrawal_to_unseen_address"]
    assert len(unseen) == 1
    # The receipt went to the digest channel, not the CRITICAL set.
    assert [a.rule for a in digest.sent].count("withdrawal_initiated") == 1
    assert all(a.severity != "MEDIUM" for a in critical.sent)


def test_a_withdrawal_is_never_familiar_because_of_itself(db, cipher, watched):
    """First withdrawal to a fresh address: CRITICAL, and it enters the
    history. The NEXT withdrawal to it is the receipt."""
    account_id, row, client = watched
    chans = {"CRITICAL": [Recording()], "HIGH": [Recording()], "MEDIUM": [Recording()], "LOW": [Recording()]}
    client.fetch_withdrawals.return_value = [_withdrawal("w-1", NEW)]
    _poller(db, cipher, chans).run_fast_loop(row)
    assert ("withdrawal_initiated", "CRITICAL") in _alerts(db, account_id)
    client.fetch_withdrawals.return_value = [_withdrawal("w-1", NEW), _withdrawal("w-2", NEW, amount=0.7)]
    _poller(db, cipher, chans).run_fast_loop(row)
    got = _alerts(db, account_id)
    assert got.count(("withdrawal_initiated", "CRITICAL")) == 1
    assert got.count(("withdrawal_initiated", "MEDIUM")) == 1


# --- delivery: a receipt is a digest line, never a page -------------------------------

def _receipt():
    det = detect_new_withdrawals([_withdrawal("w-r", FAMILIAR)])
    return reclassify_familiar_destinations(det, {FAMILIAR})[0]


def _account_for(db, email, phone="+15550001111"):
    from tests.conftest import make_account

    account_id = make_account(db)
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET alert_email = ?, alert_phone = ? WHERE id = ?", (email, phone, account_id))
    return account_id


def _stored(db, account_id, det):
    from sentinel.alerts.dispatch import dispatch

    return dispatch(db, {}, account_id, "acct-1", det, {"CRITICAL": 15, "HIGH": 30, "MEDIUM": 720, "LOW": 1440})


def _legs(db, alert_id):
    with db.cursor() as cur:
        cur.execute("SELECT channel, status, detail FROM deliveries WHERE alert_id = ? ORDER BY id", (alert_id,))
        return [(r["channel"], r["status"], r["detail"] or "") for r in cur.fetchall()]


def test_a_member_with_a_digest_reads_the_receipt_there_and_is_not_pushed(db, monkeypatch):
    set_member_password(db, EMAIL, "a-long-enough-password-1", notify=None)
    account_id = _account_for(db, EMAIL)
    det = _receipt()
    alert_id = _stored(db, account_id, det)
    router = _router(db, monkeypatch)
    router.send(det, "acct-1", [EMAIL], ["+15550001111"], account_id=account_id, alert_id=alert_id)
    assert _RecEmail.instances == [] and _RecSms.instances == [], "a receipt is never pushed"
    legs = _legs(db, alert_id)
    assert ("email", "skipped", "routine receipt: delivered in the member digest") in legs
    assert any(ch == "sms" and st == "skipped" and "receipt" in d for ch, st, d in legs)
    body, _ = build_member_digest(db, EMAIL)
    assert "Withdrawals to destinations you have used before: 1." in body
    assert "acct-1: withdrawal at" in body and "withdrawn to before" in body
    for forbidden in ("0.5", "BTC", FAMILIAR, "withdrawal_initiated"):
        assert forbidden not in body, f"customer copy carries {forbidden!r}"


def test_a_member_whose_digest_is_off_gets_the_plain_email_instead(db, monkeypatch):
    set_member_password(db, EMAIL, "a-long-enough-password-1", notify=None)
    assert set_digest_frequency(db, EMAIL, "off")
    account_id = _account_for(db, EMAIL)
    det = _receipt()
    alert_id = _stored(db, account_id, det)
    _router(db, monkeypatch).send(det, "acct-1", [EMAIL], ["+15550001111"], account_id=account_id, alert_id=alert_id)
    assert len(_RecEmail.instances) == 1 and _RecEmail.instances[0].recipients == [EMAIL]
    assert _RecSms.instances == [], "still no text: a receipt never wakes anyone"
    sent = _RecEmail.instances[0].sent
    assert sent.severity == "MEDIUM" and not sent.customer_subject().startswith(("Urgent", "Please check"))
    assert "Revoke" not in sent.render_customer(), "MEDIUM carries no runbook"


def test_an_address_with_no_portal_login_gets_the_plain_email(db, monkeypatch):
    stranger = "nologin@example.com"
    account_id = _account_for(db, stranger, phone=None)
    det = _receipt()
    alert_id = _stored(db, account_id, det)
    _router(db, monkeypatch).send(det, "acct-1", [stranger], [], account_id=account_id, alert_id=alert_id)
    assert len(_RecEmail.instances) == 1 and _RecEmail.instances[0].recipients == [stranger]


def test_the_ops_digest_carries_the_receipt(db):
    account_id = _account_for(db, EMAIL)
    _stored(db, account_id, _receipt())
    body = build_digest(db)
    assert body is not None and "withdrawal_initiated" in body and FAMILIAR_DESTINATION_NOTE in body


def test_a_receipt_never_escalates(db):
    from sentinel.alerts.escalation import Escalator
    from tests.test_escalation_reach import SpyAlerter, SpyRouter

    account_id = _account_for(db, EMAIL)
    alert_id = _stored(db, account_id, _receipt())
    with db.cursor() as cur:
        cur.execute("UPDATE alerts SET sent_at = '2026-01-01T00:00:00+00:00' WHERE id = ?", (alert_id,))
    router, pager = SpyRouter(), SpyAlerter()
    assert Escalator([Recording()], unacknowledged_minutes=15, operator_alerter=pager, contact_router=router,
                     stale_after_hours=100000).check(db) == 0
    assert router.calls == [] and pager.calls == []
