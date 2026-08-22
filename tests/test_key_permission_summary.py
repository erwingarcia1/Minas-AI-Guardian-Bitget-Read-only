"""#9's last criterion: the customer can SEE that the read-only guarantee
held - "This key can read balances and history. It cannot trade or
withdraw." - on the account page, stated as the outcome of verification,
with the re-check timestamp coming from the snapshots the slow loop
actually takes. The refusals themselves (trade keys, withdraw keys,
unknown permissions) were built long ago and are tested in
test_onboarding.py; this is the missing piece: showing that it worked.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sentinel.portal import _render_member_account

EMAIL = "alice@example.com"


def _account(db):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES ('acct-k', 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', "
            "'2026-01-01', '2026-01-01')", (EMAIL,))
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id, last_success_at) VALUES (?, ?)",
                    (account_id, datetime.now(timezone.utc).isoformat()))
        return account_id


def test_the_account_page_states_the_plain_summary(db):
    account_id = _account(db)
    page = _render_member_account(db, EMAIL, account_id)
    assert "This key can read balances and history. It cannot trade or withdraw." in page
    assert "would have been refused, not accepted with a warning" in page, (
        "the summary is the verification's outcome, not a marketing promise")


def test_the_recheck_time_comes_from_the_real_snapshots(db):
    from sentinel.baseline import record_permission_snapshot

    account_id = _account(db)
    record_permission_snapshot(db, account_id, ["spot_account_r"], [])
    page = _render_member_account(db, EMAIL, account_id)
    assert "last re-checked" in page
    assert "not re-checked since connection yet" not in page


def test_no_snapshot_is_said_plainly_not_papered_over(db):
    account_id = _account(db)
    page = _render_member_account(db, EMAIL, account_id)
    assert "not re-checked since connection yet" in page, (
        "an unchecked claim must read as unchecked, never as verified")


def test_a_change_raising_an_alert_is_only_claimed_because_the_detector_exists(db):
    """The sentence 'any change raises an alert' is load-bearing: it may only
    be rendered while the permission-drift detector actually exists."""
    from pathlib import Path

    detector = Path(__file__).parent.parent / "sentinel" / "detectors" / "new_api_key.py"
    assert "account_permission_state_changed" in detector.read_text(), (
        "the detector this page's claim rests on is gone - remove the claim "
        "or restore the detector")
    account_id = _account(db)
    assert "any change raises an alert" in _render_member_account(db, EMAIL, account_id)


# --- #45: key last-used and the real kill switch ------------------------------

def test_the_page_says_when_the_key_was_last_used(db):
    account_id = _account(db)
    page = _render_member_account(db, EMAIL, account_id)
    assert "It was last used" in page


def test_a_key_never_used_is_said_plainly(db):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES ('acct-k2', 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', "
            "'2026-01-01', '2026-01-01')", (EMAIL,))
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))
    page = _render_member_account(db, EMAIL, account_id)
    assert "never — no check has completed yet" in page


def test_revocation_points_at_the_exchange_not_at_a_minas_button(db):
    """The kill switch the customer actually holds is at the exchange - a
    read-only key cannot delete itself, and a Minas 'revoke' button would be
    theatre in front of it (and, as a monitoring kill switch, decision 15's
    attacker-button shape). The page says where the real switch is and what
    revoking does."""
    account_id = _account(db)
    page = _render_member_account(db, EMAIL, account_id)
    assert "You hold the kill switch." in page
    assert "Revoke this key in your Bitget" in page
    assert "Revoking ends monitoring until a new key is connected" in page
    assert 'action="/revoke"' not in page, "no Minas-side revoke endpoint exists or is offered"
