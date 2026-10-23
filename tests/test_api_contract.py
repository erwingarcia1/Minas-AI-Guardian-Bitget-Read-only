"""The server keeps api/openapi.yaml, the contract the apps are generated
from (decision 21f).

Three halves. The routes in the file are exactly sentinel/api.py's ROUTES,
and only the sign-in routes go without a bearer token. Every response the
server gives, success and error, on every route, validates against the
file's schema for its status - objects are closed, so an undocumented field
fails as surely as a missing one. And the validator itself is shown to
reject what it should, so the contract cannot pass by checking nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sentinel import api as api_module
from sentinel.api import PRE_AUTH, ROUTES
from sentinel.expected import mark_expected
from sentinel.pause import request_pause
from sentinel.portal import serve_portal, set_member_password
from tests.conftest import PINNED_TOTP_NOW
from tests.helpers.api_client import PASSWORD, ApiClient, add_account, add_alert, enrolled_member

SPEC = yaml.safe_load((Path(__file__).resolve().parent.parent / "api" / "openapi.yaml").read_text())
METHODS = ("get", "post", "put", "patch", "delete")
ALICE = "alice@example.com"
BOB = "bob@example.com"


# --- a closed-world validator for the JSON Schema this file uses ------------------------

def _resolve(schema: dict) -> dict:
    while "$ref" in schema:
        node = SPEC
        for part in schema["$ref"].removeprefix("#/").split("/"):
            node = node[part]
        schema = node
    return schema


def _is(value, kind: str) -> bool:
    return {
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "null": value is None,
    }[kind]


def validate(value, schema: dict, where: str = "$") -> None:
    schema = _resolve(schema)
    if "oneOf" in schema:
        matched, reasons = 0, []
        for option in schema["oneOf"]:
            try:
                validate(value, option, where)
                matched += 1
            except AssertionError as exc:
                reasons.append(str(exc))
        assert matched == 1, f"{where}: matched {matched} oneOf options: {reasons}"
        return
    if "type" in schema:
        kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        assert any(_is(value, k) for k in kinds), f"{where}: {value!r} is not {kinds}"
    if "enum" in schema:
        assert value in schema["enum"], f"{where}: {value!r} not in {schema['enum']}"
    if isinstance(value, dict):
        props = schema.get("properties", {})
        missing = [k for k in schema.get("required", []) if k not in value]
        assert not missing, f"{where}: missing {missing}"
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(props))
            assert not extra, f"{where}: undocumented {extra}"
        for key, item in value.items():
            if key in props:
                validate(item, props[key], f"{where}.{key}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            validate(item, schema["items"], f"{where}[{i}]")


def _operation(method: str, template: str) -> dict:
    return SPEC["paths"][template][method.lower()]


def check(method: str, template: str, status: int, body) -> None:
    responses = _operation(method, template)["responses"]
    assert str(status) in responses, f"{method} {template} answered {status}, which the contract does not document"
    response = _resolve(responses[str(status)])
    content = response.get("content")
    if content is None:
        assert body is None, f"{method} {template} {status}: the contract says no body, the server sent {body!r}"
        return
    validate(body, content["application/json"]["schema"], f"{method} {template} {status}")


# --- the routes ------------------------------------------------------------------------

def test_the_contract_names_exactly_the_servers_routes():
    documented = {(m.upper(), path) for path, ops in SPEC["paths"].items() for m in ops if m in METHODS}
    assert documented == ROUTES, (
        f"served but undocumented: {sorted(ROUTES - documented)}; "
        f"documented but not served: {sorted(documented - ROUTES)}")


def test_only_the_sign_in_routes_go_without_a_token():
    assert SPEC["security"] == [{"bearer": []}]
    for method, template in ROUTES:
        op = _operation(method, template)
        if (method, template) in PRE_AUTH:
            assert op.get("security") == [], (method, template)
        else:
            assert op.get("security", SPEC["security"]) == [{"bearer": []}], (method, template)


# --- the validator is not checking nothing ---------------------------------------------

@pytest.mark.parametrize("bad", [
    {"error": "unauthorized"},                                   # a required field missing
    {"error": "unauthorized", "message": "m", "extra": 1},       # an undocumented field
    {"error": "no_such_code", "message": "m"},                   # outside the enum
    {"error": "unauthorized", "message": None},                  # null where a string is required
    ["not", "an", "object"],
])
def test_the_validator_rejects_a_wrong_error(bad):
    with pytest.raises(AssertionError):
        validate(bad, {"$ref": "#/components/schemas/Error"})


def test_the_validator_rejects_a_float_quantity_and_a_bool_id():
    with pytest.raises(AssertionError):
        validate({"coin": "BTC", "quantity": 1.5, "snapshot_at": "t"}, {"$ref": "#/components/schemas/Balance"})
    with pytest.raises(AssertionError):
        validate({"id": True, "name": "n", "signed_in_at": "t", "last_refreshed_at": None, "current": True},
                 {"$ref": "#/components/schemas/Device"})


def test_the_validator_follows_oneof_and_nullable():
    validate(None, _resolve({"$ref": "#/components/schemas/Incident"})["properties"]["advice"])
    with pytest.raises(AssertionError):
        validate({"lead": "x"}, _resolve({"$ref": "#/components/schemas/Incident"})["properties"]["advice"])


# --- every route, every answer ---------------------------------------------------------

@pytest.fixture
def server(db, cipher, rules, monkeypatch):
    monkeypatch.setattr(api_module, "send_new_device_email", lambda db, address, name: None)
    srv = serve_portal(db, 0, host="127.0.0.1", cipher=cipher, rules=rules)
    yield ApiClient(srv.server_address[1])
    srv.shutdown()
    srv.server_close()


def test_every_operation_documents_the_catch_all_500():
    """Api.handle answers any handler that breaks with a 500 in the error
    shape, on every route - so every route says so."""
    missing = [(m, p) for p, item in SPEC["paths"].items() for m, op in item.items()
               if m in METHODS and "500" not in op["responses"]]
    assert not missing


def test_every_answer_the_server_gives_is_the_contracts(server, db, cipher, monkeypatch):
    a1 = add_account(db, ALICE, "alice-1")
    add_account(db, BOB, "bob-1")
    critical = add_alert(db, a1, sent_at="2026-08-28T10:00:00+00:00")
    notice = add_alert(db, a1, rule="password_changed", severity="LOW", sent_at="2026-08-29T10:00:00+00:00",
                       message="notice", evidence='{"source": "test", "count": 2}', delivered_to=0)
    with db.cursor() as cur:
        cur.execute("INSERT INTO balance_snapshots (account_id, coin, quantity, snapshot_at) VALUES "
                    "(?, 'BTC', '1.5', '2026-08-28T09:00:00+00:00')", (a1,))
    mark_expected(db, critical, a1, "withdrawal_initiated", "k", ALICE, note=None)
    secret = enrolled_member(db, cipher, ALICE)
    set_member_password(db, BOB, PASSWORD, notify=None)
    seen: set[tuple[str, str, int]] = set()

    def call(method, template, path=None, body=None, token=None):
        status, data, _ = server.request(method, path or template, body, token=token)
        check(method, template, status, data)
        seen.add((method, template, status))
        return status, data

    session, signin = "/api/v1/session", ("POST", "/api/v1/session")
    status, tokens = call(*signin, body={"email": ALICE, "password": PASSWORD, "code": "000000"})
    assert status == 401
    assert call(*signin, body={"email": BOB, "password": PASSWORD})[0] == 403
    assert call("POST", session, body=b"nope")[0] == 400
    status, tokens, _ = server.sign_in(ALICE, secret, at=PINNED_TOTP_NOW)
    check("POST", session, status, tokens)
    seen.add(("POST", session, status))
    token = tokens["access_token"]

    refresh = "/api/v1/session/refresh"
    status, tokens = call("POST", refresh, body={"refresh_token": tokens["refresh_token"]})
    assert status == 200
    token = tokens["access_token"]
    assert call("POST", refresh, body={"refresh_token": "made-up"})[0] == 401
    assert call("POST", refresh, body=b"[]")[0] == 400

    request_pause(db, a1, ALICE)  # so `pending_pauses` has an entry to check
    status, accounts = call("GET", "/api/v1/accounts", token=token)
    assert status == 200 and accounts["attention"] is not None and accounts["pending_pauses"]
    assert call("GET", "/api/v1/accounts")[0] == 401
    with monkeypatch.context() as broken:
        broken.setattr("sentinel.portal.member_status", lambda *a, **k: 1 / 0)
        assert call("GET", "/api/v1/accounts", token=token)[0] == 500
    assert call("GET", "/api/v1/alerts", path="/api/v1/alerts?severity=CRITICAL&limit=5", token=token)[0] == 200
    assert call("GET", "/api/v1/alerts", path="/api/v1/alerts?severity=nope", token=token)[0] == 400

    incident = "/api/v1/alerts/{id}"
    status, body = call("GET", incident, path=f"/api/v1/alerts/{critical}", token=token)
    assert status == 200 and body["advice"] is not None and body["balances_before"]
    status, body = call("GET", incident, path=f"/api/v1/alerts/{notice}", token=token)
    assert status == 200 and body["advice"] is None and body["evidence"] == {"source": "test", "count": 2}
    assert call("GET", incident, path="/api/v1/alerts/999999", token=token)[0] == 404

    ack = "/api/v1/alerts/{id}/acknowledge"
    assert call("POST", ack, path=f"/api/v1/alerts/{critical}/acknowledge", token=token)[0] == 200
    assert call("POST", ack, path="/api/v1/alerts/999999/acknowledge", token=token)[0] == 404

    assert call("GET", "/api/v1/activity", token=token)[0] == 200
    assert call("GET", "/api/v1/limits", token=token)[0] == 200
    status, security = call("GET", "/api/v1/security", token=token)
    assert status == 200 and security["devices"]

    second = server.sign_in(ALICE, secret, at=PINNED_TOTP_NOW + 30)[1]
    device = "/api/v1/devices/{id}"
    assert call("DELETE", device, path=f"/api/v1/devices/{second['device_id']}", token=token)[0] == 204
    assert call("DELETE", device, path="/api/v1/devices/999999", token=token)[0] == 404
    assert call("DELETE", session, token=token)[0] == 204

    for _ in range(5):
        server.request("POST", session, {"email": ALICE, "password": "wrong-wrong-wrong", "code": "1"})
    assert call(*signin, body={"email": ALICE, "password": PASSWORD, "code": "1"})[0] == 429

    succeeded = {(m, t) for m, t, s in seen if s in (200, 204)}
    assert succeeded == ROUTES, f"routes never answered successfully here: {sorted(ROUTES - succeeded)}"
