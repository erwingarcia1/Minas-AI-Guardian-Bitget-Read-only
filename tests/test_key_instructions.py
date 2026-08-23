"""#8 / decision 14: text-only key-creation steps, dated and interface-named
so staleness is disclosed rather than discovered - and rejections that name
the exact permission plus the fix, with the drift question that keeps the
text current.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sentinel.exchange import SUPPORTED_EXCHANGES
from sentinel.keyinstructions import KEY_INSTRUCTIONS, instructions_for, provenance_line

EMAIL = "alice@example.com"


# --- coverage and the honesty line --------------------------------------------

def test_every_supported_exchange_has_dated_interface_named_steps():
    """The registration half: an exchange the product supports without
    instructions is a customer sent to search the web mid-incident."""
    for exchange in SUPPORTED_EXCHANGES:
        entry = KEY_INSTRUCTIONS.get(exchange)
        assert entry, f"no key-creation instructions for supported exchange '{exchange}'"
        assert entry["written"], f"{exchange}: decision 14 requires a date"
        assert entry["interface"], f"{exchange}: decision 14 requires the interface name"
        assert len(entry["steps"]) >= 3
        assert entry["fix"], f"{exchange}: the rejection copy needs the one-line fix"


def test_the_provenance_line_is_customer_visible_disclosure():
    line = provenance_line(KEY_INSTRUCTIONS["bitget"])
    assert "Written against the Bitget web app" in line
    assert "2026-08" in line
    assert "tell us" in line, "the drift-report invitation is the maintenance mechanism"


def test_an_unknown_exchange_gets_an_honest_fallback_not_a_blank():
    entry = instructions_for("kraken")
    assert "have not written steps" in entry["steps"][0]
    assert provenance_line(entry) == "", "no date may be claimed for steps that don't exist"


def test_steps_never_mention_screenshots_or_images():
    """Decision 14 is text-only; an image reference would be a broken promise."""
    for exchange, entry in KEY_INSTRUCTIONS.items():
        joined = " ".join(entry["steps"]).lower()
        for word in ("screenshot", "image", "picture", "see figure"):
            assert word not in joined, f"{exchange}: {word!r} in text-only steps"


# --- the portal page ----------------------------------------------------------

def _account(db, exchange="bybit"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, exchange_uid, status, onboarded_at, updated_at) "
            "VALUES ('acct-i', ?, 'independent', 'x', 'y', 'z', ?, 'uid-1', 'active', "
            "'2026-01-01', '2026-01-01')", (exchange, EMAIL))
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id, last_success_at) VALUES (?, ?)",
                    (account_id, datetime.now(timezone.utc).isoformat()))
        return account_id


def test_the_key_page_shows_this_exchanges_steps_with_the_dated_line(db):
    from sentinel.portal import _render_replace_key

    account_id = _account(db, exchange="bybit")
    page = _render_replace_key(db, EMAIL, account_id)
    assert "Creating a read-only key on Bybit" in page
    assert "choose Read-Only" in page
    assert "Written against the Bybit web app" in page
    assert "2026-08" in page
    assert "Bitget web app" not in page, "the page shows THIS exchange's steps, not all four"


# --- the rejection names the permission and asks the drift question -----------

def test_a_rejected_key_names_the_permission_the_fix_and_the_drift_question(db, cipher, monkeypatch):
    # A client whose account-info carries a write authority, so
    # verify_read_only refuses and names it.
    from unittest.mock import MagicMock

    from sentinel.keyreplacement import replace_monitoring_key
    from tests.test_onboarding import account_info_response

    def _make_client(exchange, api_key, secret, passphrase):
        client = MagicMock()
        client.fetch_balance.return_value = {"total": {"USDT": 100}}
        info = account_info_response(authorities="withdraw")
        info["data"]["userId"] = "uid-1"
        client.fetch_account_info.return_value = info
        return client

    monkeypatch.setattr("sentinel.keyreplacement.make_client", _make_client)
    account_id = _account(db, exchange="bitget")

    outcome = replace_monitoring_key(
        db, cipher, account_id=account_id, api_key="k", secret="s",
        passphrase="p", actor=EMAIL)

    assert not outcome.ok
    assert "'withdraw'" in outcome.message, (
        "the customer must be told exactly which permission caused the refusal")
    assert "How to fix it" in outcome.message
    assert "Read-only permission only" in outcome.message
    assert "did the steps on this page match what your exchange showed you" in outcome.message, (
        "decision 14's drift question is the maintenance mechanism")
