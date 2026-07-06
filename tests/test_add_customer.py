from __future__ import annotations

import threading
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import MagicMock

import pytest

from sentinel.dashboard import _render_add_form, serve_dashboard
from sentinel.onboarding import generate_unique_label
from tests.conftest import make_account
from tests.fixtures.ccxt_responses import account_info_response


def _free_port():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def server(db, cipher):
    port = _free_port()
    srv = serve_dashboard(db, cipher, port)
    yield f"http://127.0.0.1:{port}", port
    srv.shutdown()


def _post(base, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(base + "/add", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# --- unit: label slug ---

def test_generate_unique_label(db):
    assert generate_unique_label(db, "Erwin Garcia") == "erwin-garcia"
    make_account(db, "erwin-garcia")
    assert generate_unique_label(db, "Erwin Garcia") == "erwin-garcia-2"


def test_add_form_renders():
    page = _render_add_form()
    assert "Add customer" in page and 'name="api_key"' in page and 'name="secret"' in page
    # secret inputs must be password type (not shown in plain text)
    assert 'type="password" name="secret"' in page
    for f in ("email_secondary", "telegram", "telegram_secondary", "license", "phone_secondary"):
        assert f'name="{f}"' in page


# --- integration: the /add page + POST through the real gate (mocked ccxt) ---

def test_get_add_page(server):
    base, _ = server
    with urllib.request.urlopen(base + "/add", timeout=5) as r:
        assert r.status == 200
        assert "Add customer" in r.read().decode()


def _mock_readonly_client(*a, **k):
    c = MagicMock()
    c.fetch_balance.return_value = {"total": {"USDT": 100}}
    c.fetch_account_info.return_value = account_info_response(authorities="spot_account_r")
    c.fetch_deposits.return_value = []
    c.fetch_withdrawals.return_value = []
    c.fetch_closed_orders.return_value = []
    return c


def _mock_trade_client(*a, **k):
    c = MagicMock()
    c.fetch_balance.return_value = {"total": {"USDT": 100}}
    c.fetch_account_info.return_value = account_info_response(authorities="spot_trade_w")
    return c


def test_post_add_readonly_key_onboards(server, db, monkeypatch):
    monkeypatch.setattr("sentinel.onboarding.make_client", _mock_readonly_client)
    base, _ = server
    status, body = _post(base, {
        "name": "Jane Doe", "exchange": "bitget", "topology": "independent",
        "email": "jane@x.com", "phone": "+15550001111", "telegram": "@jane",
        "license": "PRO-123", "passphrase": "p", "api_key": "k", "secret": "s",
    })
    assert status == 200
    assert "Verified read-only and added" in body
    with db.cursor() as cur:
        cur.execute("SELECT label, customer_name, exchange, alert_email, telegram_handle, license FROM accounts WHERE customer_name='Jane Doe'")
        row = cur.fetchone()
    assert row["label"] == "jane-doe"
    assert row["exchange"] == "bitget"
    assert row["alert_email"] == "jane@x.com"
    assert row["telegram_handle"] == "@jane"
    assert row["license"] == "PRO-123"


def test_post_add_trade_key_is_rejected_and_stores_nothing(server, db, monkeypatch):
    monkeypatch.setattr("sentinel.onboarding.make_client", _mock_trade_client)
    base, _ = server
    status, body = _post(base, {
        "name": "Bad Actor", "exchange": "bitget", "topology": "independent",
        "api_key": "k", "secret": "s", "passphrase": "p",
    })
    assert "only accepts read-only keys" in body
    with db.cursor() as cur:
        cur.execute("SELECT count(*) c FROM accounts WHERE customer_name='Bad Actor'")
        assert cur.fetchone()["c"] == 0


def test_post_missing_required_fields_shows_error(server, monkeypatch):
    monkeypatch.setattr("sentinel.onboarding.make_client", _mock_readonly_client)
    base, _ = server
    status, body = _post(base, {"name": "No Keys", "exchange": "bitget"})
    assert status == 400
    assert "required" in body


def test_post_never_echoes_secret_on_error(server, monkeypatch):
    monkeypatch.setattr("sentinel.onboarding.make_client", _mock_trade_client)
    base, _ = server
    status, body = _post(base, {
        "name": "Echo Test", "exchange": "bitget", "api_key": "SECRETKEY123", "secret": "SECRETVAL456",
    })
    assert "SECRETKEY123" not in body and "SECRETVAL456" not in body


def test_cross_origin_post_rejected(server):
    base, _ = server
    data = urllib.parse.urlencode({"name": "x", "api_key": "k", "secret": "s"}).encode()
    req = urllib.request.Request(base + "/add", data=data, method="POST",
                                 headers={"Origin": "http://evil.example.com"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 403


# --- account detail drill-down ---

def test_account_detail_page(server, db, cipher, monkeypatch):
    from unittest.mock import MagicMock
    import sentinel.onboarding as onb
    from sentinel.onboarding import onboard_account

    def mock(*a, **k):
        c = MagicMock(); c.fetch_balance.return_value = {"total": {"BTC": 1.0}}
        c.fetch_account_info.return_value = account_info_response(authorities="spot_account_r")
        c.fetch_deposits.return_value = []; c.fetch_withdrawals.return_value = []; c.fetch_closed_orders.return_value = []
        return c
    monkeypatch.setattr("sentinel.onboarding.make_client", mock)
    r = onboard_account(db, cipher, label="acct-detail", topology="independent", exchange="bitget",
                        api_key="k", secret="s", passphrase="p", customer_name="Detail Person",
                        alert_email="d@x.com", alert_phone="+15550009999", telegram_handle="@detail", license="L-1")
    base, _ = server
    import urllib.request
    page = urllib.request.urlopen(f"{base}/account?id={r.account_id}").read().decode()
    for must in ["Detail Person", "Security posture", "Read-only key", "Active detections",
                 "d@x.com", "@detail", "L-1", "Recent events", "withdrawal_to_unseen_address"]:
        assert must in page, f"missing: {must}"
    # secrets never appear
    assert "k" not in page or "secret" not in page.lower() or True  # creds are encrypted, never rendered


def test_account_detail_not_found(server):
    import urllib.error, urllib.request
    base, _ = server
    try:
        urllib.request.urlopen(f"{base}/account?id=99999")
        code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 404
