"""#87 / decision 19: two-factor on the ops dashboard is MANDATORY.

The dashboard is the higher-privilege surface - it adds customers, reads
every contact, attributes operator actions - and it had a password-only
login while the member portal carried #32's TOTP. Now: the password alone
mints no session; an enrolled operator must present a code (or a recovery
code); an unenrolled one must enrol before the first session is issued; the
only thing that removes an operator's second factor is a host-only
break-glass that is recorded with who ran it.
"""
from __future__ import annotations

import http.client
import urllib.parse

import pytest

from sentinel.operators import (
    create_operator,
    make_pending_token,
    make_session_token,
    operator_events,
    record_operator_event,
    reset_second_factor,
    session_secret,
    verify_pending_token,
    verify_session_token,
)
from sentinel.totp import STEP_SECONDS, totp_at
from sentinel.twofactor import OPERATOR, begin_enrolment, confirm_enrolment, is_enabled
from tests.conftest import PINNED_TOTP_NOW

PASSWORD = "correct-horse-battery"


def _code(secret, offset=0):
    return totp_at(secret, PINNED_TOTP_NOW + offset)


def _serve(db, cipher):
    from sentinel.dashboard import serve_dashboard

    server = serve_dashboard(db, cipher, 0)
    return server, server.server_address[1]


def _post(port, path, fields, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if cookie:
        headers["Cookie"] = cookie
    conn.request("POST", path, urllib.parse.urlencode(fields), headers)
    response = conn.getresponse()
    body = response.read().decode()
    return response.status, response.getheader("Set-Cookie"), body


def _get(port, path, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers={"Cookie": cookie} if cookie else {})
    response = conn.getresponse()
    return response.status, response.read().decode()


def _pending_from(body):
    marker = 'name="pending" value="'
    start = body.index(marker) + len(marker)
    return body[start:body.index('"', start)]


def _session_cookie(set_cookie):
    return set_cookie.split(";")[0] if set_cookie and set_cookie.startswith("ops_session=") else None


@pytest.fixture
def enrolled(db, cipher):
    create_operator(db, "erwin", PASSWORD)
    secret, _ = begin_enrolment(db, cipher, OPERATOR, "erwin")
    codes = confirm_enrolment(db, cipher, OPERATOR, "erwin", _code(secret))
    assert codes
    return secret, codes


@pytest.fixture
def server(db, cipher):
    srv, port = _serve(db, cipher)
    yield port
    srv.shutdown()
    srv.server_close()


# --- the password alone is not a sign-in ------------------------------------------

def test_a_correct_password_mints_no_session_only_a_code_prompt(server, enrolled):
    status, set_cookie, body = _post(server, "/login", {"username": "erwin", "password": PASSWORD})
    assert status == 200
    assert _session_cookie(set_cookie) is None, "the password step must not issue a session"
    assert "Second factor" in body and 'action="/login/code"' in body
    assert "Accounts" not in body


def test_a_code_completes_the_sign_in(server, enrolled):
    secret, _ = enrolled
    _, _, body = _post(server, "/login", {"username": "erwin", "password": PASSWORD})
    pending = _pending_from(body)
    # One step later than the code that confirmed enrolment: a code is accepted once.
    status, set_cookie, _ = _post(server, "/login/code", {"pending": pending, "code": _code(secret, offset=STEP_SECONDS)})
    cookie = _session_cookie(set_cookie)
    assert status == 303 and cookie, "a correct code signs in"
    status, page = _get(server, "/", cookie)
    assert status == 200 and "Accounts" in page


def test_a_wrong_code_is_refused_and_counted(server, enrolled):
    _, _, body = _post(server, "/login", {"username": "erwin", "password": PASSWORD})
    pending = _pending_from(body)
    status, set_cookie, page = _post(server, "/login/code", {"pending": pending, "code": "000000"})
    assert status == 401 and _session_cookie(set_cookie) is None
    assert "not accepted" in page
    # Guessing is throttled on the same limiter as passwords.
    for _ in range(12):
        status, _, _ = _post(server, "/login/code", {"pending": pending, "code": "000000"})
    assert status == 429


def test_a_recovery_code_signs_in_once(server, enrolled):
    _, codes = enrolled
    _, _, body = _post(server, "/login", {"username": "erwin", "password": PASSWORD})
    pending = _pending_from(body)
    status, set_cookie, _ = _post(server, "/login/code", {"pending": pending, "code": codes[0]})
    assert status == 303 and _session_cookie(set_cookie)
    _, _, body = _post(server, "/login", {"username": "erwin", "password": PASSWORD})
    status, set_cookie, _ = _post(server, "/login/code", {"pending": _pending_from(body), "code": codes[0]})
    assert status == 401 and _session_cookie(set_cookie) is None


def test_a_wrong_password_never_reaches_the_code_step(server, enrolled):
    status, set_cookie, body = _post(server, "/login", {"username": "erwin", "password": "nope"})
    assert status == 401 and "pending" not in body and _session_cookie(set_cookie) is None


# --- the pending token is proof of a recent password and nothing else ----------------

def test_a_pending_token_is_not_a_session(db, cipher, enrolled, server):
    secret = session_secret(db)
    pending = make_pending_token(secret, "erwin")
    assert verify_session_token(secret, pending) is None
    status, page = _get(server, "/", f"ops_session={pending}")
    assert "Accounts" not in page and "password" in page.lower()


def test_a_session_token_is_not_a_pending_token(db):
    secret = session_secret(db)
    assert verify_pending_token(secret, make_session_token(secret, "erwin")) is None


def test_a_pending_token_expires_in_five_minutes(db):
    secret = session_secret(db)
    token = make_pending_token(secret, "erwin", now=1_000_000)
    assert verify_pending_token(secret, token, now=1_000_000 + 299) == "erwin"
    assert verify_pending_token(secret, token, now=1_000_000 + 301) is None


def test_a_forged_or_stale_pending_token_restarts_the_sign_in(server, enrolled, db):
    status, set_cookie, page = _post(server, "/login/code", {"pending": "ops-pending.x.1.deadbeef", "code": "123456"})
    assert status == 401 and _session_cookie(set_cookie) is None and "start again" in page
    stale = make_pending_token(session_secret(db), "erwin", now=1)
    status, set_cookie, _ = _post(server, "/login/code", {"pending": stale, "code": "123456"})
    assert status == 401 and _session_cookie(set_cookie) is None


# --- first-login enrolment: mandatory, and it cannot lock anyone out ----------------

def test_an_unenrolled_operator_must_enrol_before_any_session(server, db, cipher):
    create_operator(db, "newbie", PASSWORD)
    status, set_cookie, body = _post(server, "/login", {"username": "newbie", "password": PASSWORD})
    assert status == 200 and _session_cookie(set_cookie) is None
    assert "Set up two-factor" in body and 'action="/login/enrol"' in body and "totp-secret" in body
    assert not is_enabled(db, OPERATOR, "newbie"), "not in force until a code verifies"


def test_confirming_enrolment_shows_recovery_codes_and_signs_in(server, db, cipher):
    from sentinel.twofactor import pending_secret

    create_operator(db, "newbie", PASSWORD)
    _, _, body = _post(server, "/login", {"username": "newbie", "password": PASSWORD})
    pending = _pending_from(body)
    secret = pending_secret(db, cipher, OPERATOR, "newbie")
    status, set_cookie, page = _post(server, "/login/enrol", {"pending": pending, "code": _code(secret)})
    cookie = _session_cookie(set_cookie)
    assert status == 200 and cookie, "enrolment confirmed signs in"
    assert "recovery codes" in page.lower()
    assert is_enabled(db, OPERATOR, "newbie")
    status, page = _get(server, "/", cookie)
    assert "Accounts" in page
    events = operator_events(db, "newbie")
    assert [e["event_type"] for e in events] == ["two_factor_enrolled"]
    assert events[0]["actor"] == "newbie"


def test_a_wrong_enrolment_code_changes_nothing_and_shows_the_same_secret(server, db, cipher):
    from sentinel.twofactor import pending_secret

    create_operator(db, "newbie", PASSWORD)
    _, _, body = _post(server, "/login", {"username": "newbie", "password": PASSWORD})
    pending = _pending_from(body)
    before = pending_secret(db, cipher, OPERATOR, "newbie")
    status, set_cookie, page = _post(server, "/login/enrol", {"pending": pending, "code": "000000"})
    assert status == 401 and _session_cookie(set_cookie) is None
    assert before in page, "the same secret is shown again - nothing has changed"
    assert not is_enabled(db, OPERATOR, "newbie")
    assert pending_secret(db, cipher, OPERATOR, "newbie") == before


def test_without_a_master_key_the_sign_in_is_refused_not_downgraded(db):
    create_operator(db, "erwin", PASSWORD)
    from sentinel.dashboard import serve_dashboard

    srv = serve_dashboard(db, None, 0)
    port = srv.server_address[1]
    try:
        status, set_cookie, body = _post(port, "/login", {"username": "erwin", "password": PASSWORD})
    finally:
        srv.shutdown()
        srv.server_close()
    assert status == 503 and _session_cookie(set_cookie) is None
    assert "two-factor is unavailable" in body


# --- the only way off: the host break-glass, recorded ----------------------------------

def test_the_host_reset_removes_the_second_factor_and_records_who(db, cipher, enrolled):
    assert reset_second_factor(db, "erwin", actor="erwin") is True
    assert not is_enabled(db, OPERATOR, "erwin")
    events = operator_events(db, "erwin")
    assert events[-1]["event_type"] == "two_factor_reset" and events[-1]["actor"] == "erwin"
    assert "break-glass" in events[-1]["detail"]


def test_after_a_reset_the_next_sign_in_re_enrols(server, db, cipher, enrolled):
    reset_second_factor(db, "erwin", actor="erwin")
    status, set_cookie, body = _post(server, "/login", {"username": "erwin", "password": PASSWORD})
    assert status == 200 and _session_cookie(set_cookie) is None and 'action="/login/enrol"' in body


def test_the_reset_needs_a_name(db, enrolled):
    with pytest.raises(ValueError):
        record_operator_event(db, "two_factor_reset", "erwin", actor="")
    with pytest.raises(ValueError):
        reset_second_factor(db, "erwin", actor="  ")


def test_the_reset_of_an_unknown_operator_is_reported(db):
    assert reset_second_factor(db, "nobody", actor="erwin") is False


def _cli_operator(db, **fields):
    import argparse
    import os

    from sentinel.cli import cmd_operator

    os.environ["SENTINEL_DB_PATH"] = str(db._path)
    ns = argparse.Namespace(password=None, name=None, enrol_later=True, as_operator=None)
    for k, v in fields.items():
        setattr(ns, k, v)
    return cmd_operator(ns)


def test_the_cli_reset_requires_as(db, capsys, enrolled):
    db.close()
    assert _cli_operator(db, operator_command="reset-2fa", username="erwin") == 2
    assert "--as" in capsys.readouterr().err


def test_the_cli_reset_removes_the_factor_and_records_the_name(db, capsys, enrolled):
    db.close()
    assert _cli_operator(db, operator_command="reset-2fa", username="erwin", as_operator="erwin") == 0
    assert "recorded" in capsys.readouterr().out
    from sentinel.db import Database

    fresh = Database(db._path)
    try:
        assert not is_enabled(fresh, OPERATOR, "erwin")
        events = operator_events(fresh, "erwin")
        assert events[-1]["event_type"] == "two_factor_reset" and events[-1]["actor"] == "erwin"
    finally:
        fresh.close()


def test_operator_add_without_a_tty_defers_enrolment_to_first_sign_in(db, capsys):
    db.close()
    assert _cli_operator(db, operator_command="add", username="newbie", password="a-long-enough-password") == 0
    out = capsys.readouterr().out
    assert "first sign-in" in out
    from sentinel.db import Database

    fresh = Database(db._path)
    try:
        assert not is_enabled(fresh, OPERATOR, "newbie")
    finally:
        fresh.close()


def test_operator_list_shows_who_has_no_second_factor_yet(db, capsys, enrolled):
    create_operator(db, "newbie", PASSWORD)
    db.close()
    assert _cli_operator(db, operator_command="list", username=None) == 0
    out = capsys.readouterr().out
    assert "NOT YET" in out and "on" in out


def test_no_dashboard_route_can_disable_operator_two_factor():
    """The portal has /security/2fa/disable; the dashboard must have no
    equivalent. The reset is a shell command on the host, nowhere else."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "sentinel" / "dashboard.py").read_text()
    assert "2fa/disable" not in src and "disable_2fa" not in src
    assert "reset_second_factor" not in src
