"""scripts/dev-server.py - the demo server the owner tests the app against
(decision 21) - keeps working as the schema moves, and keeps its one safety
property: nothing it runs can reach the outside world."""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
from pathlib import Path

import pytest

from sentinel import api as api_module
from sentinel.portal import member_status, serve_portal
from sentinel.totp import totp_at
from tests.conftest import PINNED_TOTP_NOW

ROOT = Path(__file__).resolve().parent.parent


def _load(monkeypatch):
    # Prove the script sets the guard itself rather than inheriting it.
    monkeypatch.delenv("SENTINEL_ENV", raising=False)
    spec = importlib.util.spec_from_file_location("dev_server", ROOT / "scripts" / "dev-server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_loading_the_demo_server_turns_the_outbound_guard_on(monkeypatch):
    _load(monkeypatch)
    from sentinel import net

    assert os.environ["SENTINEL_ENV"] == "test"
    with pytest.raises(net.NetworkRefused):
        net.post("https://example.com/never")


@pytest.fixture
def demo(db, cipher, rules, monkeypatch):
    dev = _load(monkeypatch)
    ids = dev.seed(db, cipher)
    monkeypatch.setattr(api_module, "send_new_device_email", lambda db, address, name: None)
    srv = serve_portal(db, 0, host="127.0.0.1", cipher=cipher, rules=rules)
    yield dev, ids, srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _call(port, method, path, body=None, token=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, (json.loads(raw) if raw else None)


def test_the_demo_world_is_what_the_app_needs_to_show(demo, db, rules):
    dev, ids, port = demo
    assert member_status(db, dev.DEMO_EMAIL, rules)["overall"] == "degraded"  # one watched, one refused

    code = totp_at(dev.DEMO_TOTP_SECRET, PINNED_TOTP_NOW)
    status, tokens = _call(port, "POST", "/api/v1/session",
                           {"email": dev.DEMO_EMAIL, "password": dev.DEMO_PASSWORD, "code": code})
    assert status == 200, tokens

    token = tokens["access_token"]
    status, alerts = _call(port, "GET", "/api/v1/alerts", token=token)
    assert status == 200 and {a["severity"] for a in alerts["alerts"]} == {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
    assert any(a["delivered_to"] == 0 for a in alerts["alerts"]), "the not-delivered state is demonstrable"
    assert any(a["acknowledged_at"] for a in alerts["alerts"]), "the acknowledged state is demonstrable"

    status, incident = _call(port, "GET", f"/api/v1/alerts/{ids['theft']}", token=token)
    assert status == 200 and incident["advice"] and incident["balances_before"] and incident["balances_after"]

    status, activity = _call(port, "GET", "/api/v1/activity", token=token)
    assert status == 200 and len(activity["events"]) >= 4
