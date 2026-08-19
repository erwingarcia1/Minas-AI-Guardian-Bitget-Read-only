"""The searchable alert history (G-31, #35).

Two properties carry the weight here. The first is the usual portal boundary:
the history and its export are windows onto `alerts`, and they must show one
member's accounts and nobody else's - including when the member themselves
points the account filter at a stranger's id. The second is the filter rule:
a filter that cannot be understood must select NOTHING. The inviting bug is to
ignore a bad filter and fall through to everything, which shows the whole
fleet's worth of one member's data under a heading claiming a selection - or,
with the isolation bug above, worse.

The CSV is the same record leaving the building, so it gets the same scoping
tests, plus one of its own: a cell must not arrive as a formula, because the
person opening this file in Excel is exactly who a hostile =HYPERLINK() would
be aimed at.
"""
from __future__ import annotations

import http.client
import urllib.parse

import pytest

from sentinel.portal import alert_history_rows, serve_portal, set_member_password


def _free_port():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def portal(db):
    port = _free_port()
    srv = serve_portal(db, port)
    yield "127.0.0.1", port
    srv.shutdown()


def _request(host_port, method, path, body=None, cookie=None):
    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {}
    payload = None
    if body is not None:
        payload = urllib.parse.urlencode(body)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if cookie:
        headers["Cookie"] = f"minas_portal={cookie}"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode("utf-8-sig")
    content_type = resp.getheader("Content-Type") or ""
    set_cookie = resp.getheader("Set-Cookie") or ""
    conn.close()
    return resp.status, data, set_cookie, content_type


def _login(host_port, email, password):
    status, _body, set_cookie, _ct = _request(
        host_port, "POST", "/login", {"email": email, "password": password})
    assert "minas_portal=" in set_cookie
    return set_cookie.split("minas_portal=", 1)[1].split(";", 1)[0]


def _member_account(db, label, email):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', '2026-01-01', '2026-01-01')",
            (label, email),
        )
        return cur.lastrowid


def _alert(db, account_id, rule="withdrawal_unseen_address", severity="CRITICAL",
           sent_at="2026-08-01T12:00:00+00:00", message="0.5 BTC left the account"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at) VALUES (?, ?, ?, 'k', ?, '{}', 'high', ?)",
            (account_id, rule, severity, message, sent_at),
        )
        return cur.lastrowid


EMAIL = "customer@example.com"
PASSWORD = "a-long-portal-password"


@pytest.fixture
def signed_in(db, portal):
    set_member_password(db, EMAIL, PASSWORD)
    aid = _member_account(db, "Main", EMAIL)
    token = _login(portal, EMAIL, PASSWORD)
    return aid, token


# --- the query itself ---------------------------------------------------------

def test_history_is_newest_first_and_complete(db, signed_in, portal):
    aid, token = signed_in
    for i in range(35):  # more than the home page's 30
        _alert(db, aid, sent_at=f"2026-07-{i % 28 + 1:02d}T0{i % 9}:00:00+00:00")
    rows, total = alert_history_rows(db, [aid])
    assert total == 35
    assert len(rows) == 35
    stamps = [r["sent_at"] for r in rows]
    assert stamps == sorted(stamps, reverse=True)


def test_same_second_alerts_do_not_swap_between_page_and_export(db, signed_in):
    aid, _token = signed_in
    first = _alert(db, aid, sent_at="2026-08-01T12:00:00+00:00")
    second = _alert(db, aid, sent_at="2026-08-01T12:00:00+00:00")
    rows, _ = alert_history_rows(db, [aid])
    assert [r["id"] for r in rows] == [second, first]


def test_severity_filter(db, signed_in):
    aid, _token = signed_in
    _alert(db, aid, severity="CRITICAL")
    _alert(db, aid, severity="LOW", rule="digest")
    rows, total = alert_history_rows(db, [aid], severity="CRITICAL")
    assert total == 1
    assert rows[0]["severity"] == "CRITICAL"


def test_date_filters_are_inclusive_of_the_to_day(db, signed_in):
    aid, _token = signed_in
    _alert(db, aid, sent_at="2026-08-01T09:00:00+00:00")
    _alert(db, aid, sent_at="2026-08-02T23:59:59+00:00")
    _alert(db, aid, sent_at="2026-08-03T00:00:01+00:00")
    rows, total = alert_history_rows(db, [aid], date_from="2026-08-01", date_to="2026-08-02")
    assert total == 2
    assert all(r["sent_at"].startswith(("2026-08-01", "2026-08-02")) for r in rows)


def test_garbage_date_selects_nothing_not_everything(db, signed_in):
    aid, _token = signed_in
    _alert(db, aid)
    _rows, total = alert_history_rows(db, [aid], date_to="not-a-date")
    assert total == 0
    _rows, total = alert_history_rows(db, [aid], date_from="not-a-date")
    assert total == 0


def test_account_filter_outside_the_member_scope_selects_nothing(db, signed_in):
    """The intersection IS the isolation: a stranger's account id narrows the
    member's own list to nothing rather than escaping it."""
    aid, _token = signed_in
    stranger = _member_account(db, "Stranger", "other@example.com")
    _alert(db, aid)
    _alert(db, stranger)
    rows, total = alert_history_rows(db, [aid], account=stranger)
    assert total == 0
    assert rows == []


# --- the page -----------------------------------------------------------------

def test_history_page_lists_alerts_and_links_incidents(db, signed_in, portal):
    aid, token = signed_in
    alert_id = _alert(db, aid)
    status, body, _c, _ct = _request(portal, "GET", "/alerts", cookie=token)
    assert status == 200
    assert "withdrawal_unseen_address" in body
    assert f"/incident?id={alert_id}" in body


def test_history_page_requires_a_session(db, portal):
    status, body, _c, _ct = _request(portal, "GET", "/alerts")
    assert 'id="signin-title"' in body


def test_history_page_never_shows_another_members_alerts(db, signed_in, portal):
    aid, token = signed_in
    stranger = _member_account(db, "Stranger Corp", "other@example.com")
    _alert(db, stranger, rule="stranger_rule")
    status, body, _c, _ct = _request(portal, "GET", "/alerts", cookie=token)
    assert "stranger_rule" not in body
    assert "Stranger Corp" not in body
    # And pointing the filter at their id shows nothing, not everything.
    status, body, _c, _ct = _request(portal, "GET", f"/alerts?account={stranger}", cookie=token)
    assert "stranger_rule" not in body
    assert "No alerts match" in body


def test_unparseable_account_filter_selects_nothing(db, signed_in, portal):
    aid, token = signed_in
    _alert(db, aid)
    _status, body, _c, _ct = _request(portal, "GET", "/alerts?account=banana", cookie=token)
    assert "No alerts match" in body


def test_history_paginates_past_the_page_size(db, signed_in, portal):
    from sentinel.portal import _HISTORY_PAGE_SIZE

    aid, token = signed_in
    for i in range(_HISTORY_PAGE_SIZE + 1):
        _alert(db, aid, sent_at=f"2026-07-01T{i % 24:02d}:{i % 60:02d}:00+00:00")
    _status, body, _c, _ct = _request(portal, "GET", "/alerts", cookie=token)
    assert "Older ›" in body
    _status, body2, _c, _ct = _request(portal, "GET", "/alerts?page=2", cookie=token)
    assert "‹ Newer" in body2


# --- the export ---------------------------------------------------------------

def test_csv_export_contains_every_row_not_a_page(db, signed_in, portal):
    aid, token = signed_in
    for i in range(250):  # past _HISTORY_PAGE_SIZE
        _alert(db, aid, sent_at=f"2026-07-01T{i % 24:02d}:{i % 60:02d}:{i % 60:02d}+00:00")
    status, body, _c, content_type = _request(portal, "GET", "/alerts.csv", cookie=token)
    assert status == 200
    assert content_type.startswith("text/csv")
    lines = [line for line in body.splitlines() if line.strip()]
    assert len(lines) == 251  # header + all rows


def test_csv_honours_the_same_filters_as_the_page(db, signed_in, portal):
    aid, token = signed_in
    _alert(db, aid, severity="CRITICAL")
    _alert(db, aid, severity="LOW", rule="quiet_rule")
    _status, body, _c, _ct = _request(portal, "GET", "/alerts.csv?severity=CRITICAL", cookie=token)
    assert "CRITICAL" in body
    assert "quiet_rule" not in body


def test_csv_never_contains_another_members_alerts(db, signed_in, portal):
    aid, token = signed_in
    stranger = _member_account(db, "Stranger Corp", "other@example.com")
    _alert(db, stranger, rule="stranger_rule")
    _alert(db, aid)
    _status, body, _c, _ct = _request(portal, "GET", "/alerts.csv", cookie=token)
    assert "stranger_rule" not in body
    _status, body, _c, _ct = _request(
        portal, "GET", f"/alerts.csv?account={stranger}", cookie=token)
    assert "stranger_rule" not in body
    assert len([line for line in body.splitlines() if line.strip()]) == 1  # header only


def test_csv_requires_a_session(db, portal):
    _status, body, _c, content_type = _request(portal, "GET", "/alerts.csv")
    # The login page, not an empty spreadsheet: no data crosses the boundary.
    assert not content_type.startswith("text/csv")
    assert 'id="signin-title"' in body


def test_csv_defangs_formula_cells(db, signed_in, portal):
    aid, token = signed_in
    _alert(db, aid, message='=HYPERLINK("http://evil","open me")')
    _status, body, _c, _ct = _request(portal, "GET", "/alerts.csv", cookie=token)
    assert '=HYPERLINK' in body  # the content survives...
    assert "'=HYPERLINK" in body  # ...but only behind the quote
