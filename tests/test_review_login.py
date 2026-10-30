"""The store reviewer's login (decision 28, the owner 2026-10-30: option D).

| Risk | Defence |
|---|---|
| A credential that skips two-factor opens real data | it opens only the invented demo account, checked at every sign-in |
| The demo account looks like a member's and fires real messages | no key (never polled), no contact (never messaged), alerts pre-escalated, "demo account" on every screen |
| The fixed code becomes a password-only web session | the website refuses the login; it cannot ask for a reset link or set up two-factor of its own |
| The credentials end up in the public repository | made by a command, printed once, stored hashed |
| Nobody notices it being used | every sign-in is an activity entry and a dashboard line; rotate and disable |
"""
from __future__ import annotations

import json

import pytest

from sentinel import api as api_module
from sentinel.review import (
    DEMO_REASON,
    ReviewLoginRefused,
    check_review_code,
    create_review_login,
    demo_account_id,
    disable_review_login,
    ensure_demo_account,
    review_sign_ins,
    rotate_review_login,
)
from tests.helpers.api_client import ApiClient, add_account
from tests.helpers.landing_redirect import exchange

REVIEW = "play-review@minas.example"


@pytest.fixture
def server(db, cipher, monkeypatch):
    from sentinel.portal import serve_portal

    monkeypatch.setattr(api_module, "send_new_device_email", lambda db, address, name: None)
    srv = serve_portal(db, 0, host="127.0.0.1", cipher=cipher)
    client = ApiClient(srv.server_address[1])
    client.host_port = ("127.0.0.1", srv.server_address[1])
    yield client
    srv.shutdown()
    srv.server_close()


def _sign_in(server, password, code):
    return server.request("POST", "/api/v1/session", {"email": REVIEW, "password": password, "code": code,
                                                      "device_name": "Google review"})


# --- the demo account -----------------------------------------------------------------

def test_the_demo_account_is_never_polled_and_has_nobody_to_message(db):
    account_id = ensure_demo_account(db)
    with db.cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
        row = cur.fetchone()
        cur.execute("SELECT id FROM accounts WHERE status = 'active'")
        active = [r["id"] for r in cur.fetchall()]
    assert row["is_demo"] == 1 and row["status"] == "disabled", "the engine polls active accounts only"
    assert account_id not in active
    assert row["alert_email"] is None and row["alert_email_secondary"] is None and row["alert_phone"] is None
    assert row["encrypted_api_key"] == "" and row["is_drill"] == 0
    assert ensure_demo_account(db) == account_id, "made once"


class _RecordingPager:
    def __init__(self):
        self.pages = []

    def page(self, *args, **kwargs):
        self.pages.append((args, kwargs))
        return len(self.pages)


@pytest.mark.parametrize("stale_after_hours", [4, 24 * 30])
def test_the_demo_alerts_never_escalate(db, stale_after_hours):
    """Already escalated, so the escalator skips them. Both of its paths:
    a sample older than the stale bound would be reported to Minas as a
    backlog (the default, 4 h), and one inside it would escalate."""
    from sentinel.alerts.escalation import Escalator
    from tests.test_alert_stack import RecordingChannel

    ensure_demo_account(db)
    channel, pager = RecordingChannel(), _RecordingPager()
    escalator = Escalator([channel], unacknowledged_minutes=1, high_unacknowledged_minutes=1,
                          operator_alerter=pager, stale_after_hours=stale_after_hours)
    assert escalator.check(db) == 0
    assert channel.sent == [] and pager.pages == []


def test_the_demo_account_has_something_to_show(db):
    account_id = ensure_demo_account(db)
    with db.cursor() as cur:
        cur.execute("SELECT rule, severity, acknowledged_at, evidence_json FROM alerts WHERE account_id = ?",
                    (account_id,))
        alerts = cur.fetchall()
        cur.execute("SELECT count(*) AS n FROM balance_snapshots WHERE account_id = ?", (account_id,))
        balances = cur.fetchone()["n"]
    assert {a["severity"] for a in alerts} == {"CRITICAL", "HIGH"}
    assert any(a["acknowledged_at"] is None for a in alerts), "something for the reviewer to acknowledge"
    assert all(json.loads(a["evidence_json"])["sample"] is True for a in alerts)
    assert balances >= 2


def test_the_demo_account_cannot_be_switched_on(db):
    from sentinel.onboarding import set_account_status

    ensure_demo_account(db)
    with pytest.raises(ValueError, match="demo account"):
        set_account_status(db, "demo-account", "active", "erwin")
    with db.cursor() as cur:
        cur.execute("SELECT status FROM accounts WHERE is_demo = 1")
        assert cur.fetchone()["status"] == "disabled"


# --- making the login -----------------------------------------------------------------------

def test_the_login_opens_the_demo_account_and_nothing_else(db):
    from sentinel.portal import is_scoped, member_accounts

    add_account(db, "alice@example.com", "alice-1")
    password, code = create_review_login(db, REVIEW, actor="erwin")
    assert len(code) == 6 and code.isdigit() and len(password) >= 20
    assert is_scoped(db, REVIEW)
    assert [int(r["id"]) for r in member_accounts(db, REVIEW)] == [demo_account_id(db)]


def test_the_credentials_are_stored_only_hashed(db):
    password, code = create_review_login(db, REVIEW, actor="erwin")
    with db.cursor() as cur:
        cur.execute("SELECT * FROM portal_members WHERE email = ?", (REVIEW,))
        row = " ".join(str(v) for v in dict(cur.fetchone()).values())
    assert password not in row and code not in row


@pytest.mark.parametrize("why", ["second", "has_login", "is_contact"])
def test_a_review_login_is_refused_where_it_would_touch_a_real_one(db, why):
    from sentinel.portal import set_member_password

    if why == "second":
        create_review_login(db, "first-review@minas.example")
    elif why == "has_login":
        set_member_password(db, REVIEW, "a-long-enough-password")
    else:
        add_account(db, REVIEW, "someone-1")
    with pytest.raises(ReviewLoginRefused):
        create_review_login(db, REVIEW)


# --- signing in -----------------------------------------------------------------------------

def test_the_app_signs_in_with_the_password_and_the_fixed_code(server, db):
    password, code = create_review_login(db, REVIEW, actor="erwin")
    status, tokens, _ = _sign_in(server, password, code)
    assert status == 200, tokens
    status, accounts, _ = server.request("GET", "/api/v1/accounts", token=tokens["access_token"])
    assert status == 200
    [account] = accounts["accounts"]
    assert account["name"] == "Demo account (sample data)" and account["watched"] is False
    assert account["reason"] == DEMO_REASON
    info = review_sign_ins(db)
    assert info["sign_ins"] == 1 and info["phones"] == 1


def test_the_reviewers_settings_say_two_factor_is_on(server, db):
    """Its sign-in needs the fixed code. "Off" would say in red that the
    phone cannot sign in again, which is false for this login."""
    password, code = create_review_login(db, REVIEW)
    _, tokens, _ = _sign_in(server, password, code)
    status, body, _ = server.request("GET", "/api/v1/security", token=tokens["access_token"])
    assert status == 200 and body["two_factor_enabled"] is True and body["recovery_codes_left"] == 0


def test_a_wrong_code_is_refused_like_any_other(server, db):
    password, code = create_review_login(db, REVIEW)
    wrong = "000000" if code != "000000" else "111111"
    status, body, _ = _sign_in(server, password, wrong)
    assert status == 401 and body["error"] == "invalid_credentials"
    status, _, _ = _sign_in(server, "not-the-password", code)
    assert status == 401


def test_the_code_stops_working_the_moment_the_login_opens_a_real_account(server, db):
    """Checked at every sign-in, not only when the code was set."""
    from sentinel.portal import grant_account

    password, code = create_review_login(db, REVIEW)
    real = add_account(db, "alice@example.com", "alice-1")
    grant_account(db, REVIEW, real, "someone")
    assert check_review_code(db, REVIEW, code) is False
    status, _, _ = _sign_in(server, password, code)
    assert status == 401


def test_the_website_refuses_the_review_login(server, db):
    password, _code = create_review_login(db, REVIEW)
    status, message, body = exchange(server.host_port, "POST", "/login", {"email": REVIEW, "password": password})
    assert status == 403 and "This login signs in only in the app." in body
    assert not (message.get("Set-Cookie") or "").startswith("minas_portal=")


def test_the_review_login_gets_no_reset_link(db):
    from sentinel.portal import create_reset_token

    create_review_login(db, REVIEW)
    assert create_reset_token(db, REVIEW) is None


def test_the_review_login_cannot_set_up_two_factor_of_its_own(server, db):
    password, _code = create_review_login(db, REVIEW)
    status, body, _ = server.request("POST", "/api/v1/two-factor/start", {"email": REVIEW, "password": password})
    assert status == 409 and body["error"] == "two_factor_already_on"


def test_the_reviewer_can_acknowledge_a_sample_alert(server, db):
    password, code = create_review_login(db, REVIEW)
    _, tokens, _ = _sign_in(server, password, code)
    demo = demo_account_id(db)  # outside the cursor: a nested one deadlocks
    with db.cursor() as cur:
        cur.execute("SELECT id FROM alerts WHERE account_id = ? AND acknowledged_at IS NULL", (demo,))
        alert_id = cur.fetchone()["id"]
    status, body, _ = server.request("POST", f"/api/v1/alerts/{alert_id}/acknowledge", token=tokens["access_token"])
    assert status == 200 and body["newly_acknowledged"] is True


# --- rotating and removing ---------------------------------------------------------------------

def test_rotating_retires_the_old_password_code_and_phones(server, db):
    password, code = create_review_login(db, REVIEW)
    _, tokens, _ = _sign_in(server, password, code)
    new_password, new_code = rotate_review_login(db, actor="erwin")
    assert (new_password, new_code) != (password, code)
    assert server.request("GET", "/api/v1/accounts", token=tokens["access_token"])[0] == 401
    assert _sign_in(server, password, code)[0] == 401
    assert _sign_in(server, new_password, new_code)[0] == 200


def test_disabling_removes_the_login_and_keeps_the_demo(db):
    from sentinel.portal import member_exists

    create_review_login(db, REVIEW)
    demo = demo_account_id(db)
    assert disable_review_login(db) == REVIEW
    assert not member_exists(db, REVIEW) and review_sign_ins(db) is None
    assert demo_account_id(db) == demo


def test_the_dashboard_lists_the_review_login(db):
    from sentinel.dashboard import _review_login_section

    assert _review_login_section(db) == ""
    create_review_login(db, REVIEW)
    section = _review_login_section(db)
    assert "Store review login" in section and REVIEW in section and "0 app sign-in(s)" in section


def _cli(monkeypatch, *argv) -> int:
    import sys

    from sentinel import cli

    monkeypatch.setattr(sys, "argv", ["sentinel", *argv])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    return exit_info.value.code


def test_the_command_prints_the_credentials_once_and_refuses_a_second(db, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SENTINEL_DB_PATH", str(tmp_path / "cli.db"))
    assert _cli(monkeypatch, "review-login", "create", REVIEW, "--as", "erwin") == 0
    out = capsys.readouterr().out
    assert "password:" in out and "code:" in out and "Play Console" in out
    assert _cli(monkeypatch, "review-login", "create", "other@minas.example") == 1
    assert _cli(monkeypatch, "review-login", "status") == 0
    assert REVIEW in capsys.readouterr().out


def test_the_command_is_recorded_against_an_active_operator(db, tmp_path, monkeypatch):
    from sentinel.db import Database
    from sentinel.operators import create_operator

    path = tmp_path / "cli.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(path))
    create_operator(Database(str(path)), "erwin", "a-long-operator-password")
    assert _cli(monkeypatch, "review-login", "create", REVIEW) == 2
    assert _cli(monkeypatch, "review-login", "create", REVIEW, "--as", "nobody") == 2
    assert _cli(monkeypatch, "review-login", "create", REVIEW, "--as", "erwin") == 0
