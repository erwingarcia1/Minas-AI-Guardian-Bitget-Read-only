"""#7's last criterion: onboarding a customer is recorded against an
operator identity, not anonymously.

The dashboard's add form already attributes via its operator session; the
gap was `sentinel onboard`, which recorded None. Same `--as` gate as every
attributed command - mandatory the moment operator accounts exist, and
checked BEFORE the secret prompts, so a refusal never costs a pasted key.
"""
from __future__ import annotations

import argparse
import json

from sentinel.cli import cmd_onboard
from sentinel.operators import create_operator


def _args(**kw):
    ns = argparse.Namespace(
        label="acct-a1", exchange="bitget", topology="independent",
        api_key="k", secret="s", passphrase="p",
        alert_email="owner@example.com", alert_phone=None, as_operator=None,  # decision 4: email required
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _fake_exchange(monkeypatch):
    from cryptography.fernet import Fernet

    from tests.test_onboarding import _client_with, account_info_response

    # cmd_onboard builds its cipher from the environment, which conftest
    # deliberately clears (#68) - hand it a throwaway key.
    monkeypatch.setenv("SENTINEL_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(
        "sentinel.onboarding.make_client",
        lambda *a, **kw: _client_with(
            account_info_result=account_info_response(authorities="spot_account_r")),
    )


def test_once_operators_exist_an_unattributed_onboard_is_refused(db, cipher, monkeypatch):
    _fake_exchange(monkeypatch)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    create_operator(db, "erwin", "a-long-enough-password-1")

    assert cmd_onboard(_args()) == 2
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM accounts")
        assert cur.fetchone()["n"] == 0, "the refusal must onboard nothing"


def test_an_attributed_onboard_carries_the_name_into_the_founding_event(db, cipher, monkeypatch):
    _fake_exchange(monkeypatch)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    create_operator(db, "erwin", "a-long-enough-password-1")

    assert cmd_onboard(_args(as_operator="erwin")) == 0
    with db.cursor() as cur:
        cur.execute("SELECT payload_json, actor FROM events WHERE event_type = 'customer_added'")
        row = cur.fetchone()
    assert row is not None
    assert row["actor"] == "erwin", "the founding entry must carry the name"
    assert json.loads(row["payload_json"])["label"] == "acct-a1"


def test_before_operators_exist_the_unattributed_path_still_works(db, cipher, monkeypatch):
    """The upgrade path every gate here follows: a deployment with no
    operator accounts keeps working, and None is the honest actor."""
    _fake_exchange(monkeypatch)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))

    assert cmd_onboard(_args()) == 0
    with db.cursor() as cur:
        cur.execute("SELECT actor FROM events WHERE event_type = 'customer_added'")
        assert cur.fetchone()["actor"] is None


def test_a_named_actor_with_no_operator_accounts_is_refused(db, cipher, monkeypatch):
    _fake_exchange(monkeypatch)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    assert cmd_onboard(_args(as_operator="ghost")) == 2
