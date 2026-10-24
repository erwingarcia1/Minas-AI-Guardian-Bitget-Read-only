""""That was me" (G-24, #28).

A customer marking an alert as their own activity is the only signal this
product gets about whether its detectors are right, from the only person who
can know. Three things have to hold, and each has a comfortable way of being
wrong:

**It must not suppress.** The tempting implementation makes "that was me" also
stop the alert - it reads as helpful and it is how a customer would describe
what they want. It would also mean one reassuring click quietly turning a
detector off, on the account of somebody who has just told us they are not
worried. `test_marking_does_not_suppress_anything` is the guard.

**It must not inflate.** The pattern an operator reads is a count, so a double
click, a refresh or a second person marking the same alert must not each become
another "this rule is wrong".

**It must not claim precision it does not have.** An unmarked alert is
unjudged, not correct. Any figure computed over all alerts would turn a quiet
week of nobody looking into a detector improving, which is the exact shape of
bug this project keeps finding elsewhere.
"""
from __future__ import annotations

import http.client
import urllib.parse

import pytest

from sentinel.expected import (
    MAX_NOTE_LENGTH,
    judged_counts,
    mark_expected,
    marked_alert_ids,
    marks_for_alert,
    patterns,
)
from sentinel.portal import serve_portal, set_member_password
from tests.helpers.landing_redirect import assert_sent_to_the_landing_page


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
    data = resp.read().decode()
    conn.close()
    return resp.status, data


def _login(host_port, email, password):
    host, port = host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("POST", "/login", body=urllib.parse.urlencode(
        {"email": email, "password": password}),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    resp.read()
    set_cookie = resp.getheader("Set-Cookie") or ""
    conn.close()
    assert "minas_portal=" in set_cookie
    return set_cookie.split("minas_portal=", 1)[1].split(";", 1)[0]


def _account(db, label, email):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
            "encrypted_passphrase, alert_email, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, 'active', '2026-01-01', '2026-01-01')",
            (label, email),
        )
        return cur.lastrowid


def _alert(db, account_id, rule="withdrawal_unseen_address", severity="CRITICAL",
           dedup_key="k1", sent_at="2026-08-01T12:00:00+00:00"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at) VALUES (?, ?, ?, ?, 'x moved', '{}', 'high', ?)",
            (account_id, rule, severity, dedup_key, sent_at),
        )
        return cur.lastrowid


EMAIL = "customer@example.com"
PASSWORD = "a-long-portal-password"


@pytest.fixture
def signed_in(db, portal):
    aid = _account(db, "Main", EMAIL)
    set_member_password(db, EMAIL, PASSWORD)
    return aid, _login(portal, EMAIL, PASSWORD)


# --- recording ----------------------------------------------------------------

def test_marking_records_who_when_and_the_note(db, signed_in):
    aid, _token = signed_in
    alert_id = _alert(db, aid)
    assert mark_expected(db, alert_id, aid, "withdrawal_unseen_address", "k1",
                         marked_by="Customer@Example.com", note="  paying my builder  ") is True
    marks = marks_for_alert(db, alert_id)
    assert len(marks) == 1
    assert marks[0]["marked_by"] == EMAIL  # normalised
    assert marks[0]["note"] == "paying my builder"  # trimmed
    assert marks[0]["created_at"]


def test_a_second_marking_by_the_same_person_is_not_counted_twice(db, signed_in):
    aid, _token = signed_in
    alert_id = _alert(db, aid)
    assert mark_expected(db, alert_id, aid, "r", "k1", marked_by=EMAIL) is True
    assert mark_expected(db, alert_id, aid, "r", "k1", marked_by=EMAIL) is False
    assert len(marks_for_alert(db, alert_id)) == 1


def test_a_second_contact_marking_the_same_alert_is_recorded_separately(db, signed_in):
    """Two people on one account are two judgements, not one repeated."""
    aid, _token = signed_in
    alert_id = _alert(db, aid)
    mark_expected(db, alert_id, aid, "r", "k1", marked_by=EMAIL)
    mark_expected(db, alert_id, aid, "r", "k1", marked_by="second@example.com")
    assert len(marks_for_alert(db, alert_id)) == 2


def test_an_anonymous_marking_is_refused(db, signed_in):
    """"Somebody said this was expected" is not a fact an operator can act
    on, and this table exists to be acted on."""
    aid, _token = signed_in
    alert_id = _alert(db, aid)
    with pytest.raises(ValueError):
        mark_expected(db, alert_id, aid, "r", "k1", marked_by="   ")


def test_a_long_note_is_bounded(db, signed_in):
    aid, _token = signed_in
    alert_id = _alert(db, aid)
    mark_expected(db, alert_id, aid, "r", "k1", marked_by=EMAIL, note="x" * 5000)
    assert len(marks_for_alert(db, alert_id)[0]["note"]) == MAX_NOTE_LENGTH


# --- the property that matters most -------------------------------------------

def test_marking_does_not_suppress_anything(db, signed_in):
    """Marking is a claim about an alert that already fired. It must not
    silence the rule, the account, or the next alert - that is what a snooze
    is for, and a snooze expires."""
    from sentinel.snooze import active_snooze

    aid, _token = signed_in
    alert_id = _alert(db, aid)
    mark_expected(db, alert_id, aid, "withdrawal_unseen_address", "k1", marked_by=EMAIL)

    assert active_snooze(db, aid, "k1") is None
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM snoozes")
        assert cur.fetchone()["n"] == 0
        # And the alert itself is untouched: still delivered, still recorded.
        cur.execute("SELECT snoozed_until, acknowledged_at FROM alerts WHERE id = ?", (alert_id,))
        row = cur.fetchone()
    assert row["snoozed_until"] is None
    assert row["acknowledged_at"] is None


# --- the portal route ---------------------------------------------------------

def test_a_member_can_mark_from_the_portal(db, signed_in, portal):
    aid, token = signed_in
    alert_id = _alert(db, aid)
    status, _body = _request(portal, "POST", "/expected",
                             body={"alert_id": str(alert_id), "note": "my own transfer"},
                             cookie=token)
    assert status == 303
    marks = marks_for_alert(db, alert_id)
    assert [m["marked_by"] for m in marks] == [EMAIL]
    assert marks[0]["note"] == "my own transfer"


def test_marking_another_members_alert_is_not_found(db, signed_in, portal):
    """The same boundary as acknowledge and snooze: "that isn't yours" and
    "that doesn't exist" must be one answer, or alert ids can be mapped one at
    a time."""
    aid, token = signed_in
    stranger = _account(db, "Stranger", "other@example.com")
    stranger_alert = _alert(db, stranger)
    status, _body = _request(portal, "POST", "/expected",
                             body={"alert_id": str(stranger_alert)}, cookie=token)
    assert status == 404
    assert marks_for_alert(db, stranger_alert) == []


def test_marking_requires_a_session(db, signed_in, portal):
    aid, _token = signed_in
    alert_id = _alert(db, aid)
    assert_sent_to_the_landing_page(portal, "POST", "/expected", body={"alert_id": str(alert_id)})
    assert marks_for_alert(db, alert_id) == []


def test_the_control_is_offered_then_replaced_by_the_answer(db, signed_in, portal):
    aid, token = signed_in
    alert_id = _alert(db, aid)
    _status, before = _request(portal, "GET", f"/account?id={aid}", cookie=token)
    assert "That was me" in before

    _request(portal, "POST", "/expected", body={"alert_id": str(alert_id)}, cookie=token)

    _status, after = _request(portal, "GET", f"/account?id={aid}", cookie=token)
    assert "You marked this as your own activity" in after
    assert "That was me" not in after


def test_marking_is_still_offered_after_acknowledgement(db, signed_in, portal):
    """People acknowledge in a hurry and recognise their own activity a minute
    later. The recognition is the half worth recording."""
    aid, token = signed_in
    alert_id = _alert(db, aid)
    _request(portal, "POST", "/acknowledge", body={"alert_id": str(alert_id)}, cookie=token)
    _status, body = _request(portal, "GET", f"/account?id={aid}", cookie=token)
    assert "That was me" in body


def test_the_mark_appears_on_the_incident_page(db, signed_in, portal):
    """The incident page is the document handed to an exchange. A theft the
    owner has already called their own trading must not be described as a
    theft."""
    aid, token = signed_in
    alert_id = _alert(db, aid)
    _request(portal, "POST", "/expected",
             body={"alert_id": str(alert_id), "note": "moving to my cold wallet"}, cookie=token)
    _status, body = _request(portal, "GET", f"/incident?id={alert_id}", cookie=token)
    assert "Marked as expected activity" in body
    assert "moving to my cold wallet" in body


def test_marked_alert_ids_is_scoped_to_the_accounts_asked_for(db, signed_in):
    aid, _token = signed_in
    stranger = _account(db, "Stranger", "other@example.com")
    mine = _alert(db, aid)
    theirs = _alert(db, stranger)
    mark_expected(db, mine, aid, "r", "k1", marked_by=EMAIL)
    mark_expected(db, theirs, stranger, "r", "k1", marked_by="other@example.com")
    assert marked_alert_ids(db, [aid]) == {mine}
    assert marked_alert_ids(db, []) == set()


# --- criterion 3: operators can see the patterns ------------------------------

def test_patterns_group_by_rule_and_account_with_their_denominator(db, signed_in):
    aid, _token = signed_in
    for i in range(3):
        alert_id = _alert(db, aid, rule="never_touched_pair_activity", dedup_key=f"k{i}")
        mark_expected(db, alert_id, aid, "never_touched_pair_activity", f"k{i}", marked_by=EMAIL)
    _alert(db, aid, rule="never_touched_pair_activity", dedup_key="k9")  # unmarked

    rows = patterns(db)
    assert len(rows) == 1
    assert rows[0]["rule"] == "never_touched_pair_activity"
    assert rows[0]["marks"] == 3
    assert rows[0]["alerts_total"] == 4  # the denominator, not just the count
    assert rows[0]["label"] == "Main"


def test_patterns_put_the_most_marked_first(db, signed_in):
    aid, _token = signed_in
    for i in range(2):
        a = _alert(db, aid, rule="noisy_rule", dedup_key=f"n{i}")
        mark_expected(db, a, aid, "noisy_rule", f"n{i}", marked_by=EMAIL)
    a = _alert(db, aid, rule="quiet_rule", dedup_key="q0")
    mark_expected(db, a, aid, "quiet_rule", "q0", marked_by=EMAIL)

    assert [r["rule"] for r in patterns(db)] == ["noisy_rule", "quiet_rule"]


def test_the_operator_dashboard_shows_the_patterns(db, signed_in):
    from sentinel.dashboard import _expected_activity_section

    aid, _token = signed_in
    for i in range(3):
        a = _alert(db, aid, rule="never_touched_pair_activity", dedup_key=f"k{i}")
        mark_expected(db, a, aid, "never_touched_pair_activity", f"k{i}", marked_by=EMAIL)

    html_out = _expected_activity_section(db)
    assert "never_touched_pair_activity" in html_out
    assert "3 of 3" in html_out  # the count AND its denominator
    assert "mostly marked" in html_out


def test_the_dashboard_section_is_absent_when_nothing_is_marked(db):
    from sentinel.dashboard import _expected_activity_section

    assert _expected_activity_section(db) == ""


# --- criterion 2: false positives, honestly counted ---------------------------

def test_judged_counts_report_the_denominator_not_a_precision_figure(db, signed_in):
    """An unmarked alert is unjudged, not correct. The numbers come back with
    the population they were drawn from so nobody can read silence as a good
    score."""
    aid, _token = signed_in
    marked = _alert(db, aid, rule="r", dedup_key="a")
    _alert(db, aid, rule="r", dedup_key="b")  # never looked at by anybody
    acked = _alert(db, aid, rule="r", dedup_key="c")
    with db.cursor() as cur:
        cur.execute("UPDATE alerts SET acknowledged_at = '2026-08-01T13:00:00+00:00' WHERE id = ?",
                    (acked,))
    mark_expected(db, marked, aid, "r", "a", marked_by=EMAIL)

    counts = judged_counts(db, rule="r")
    assert counts["marked_expected"] == 1
    assert counts["acknowledged"] == 1
    assert counts["alerts_total"] == 3
    # There is no key here claiming a precision ratio: the true-positive signal
    # that would make one honest does not exist yet.
    assert "precision" not in counts


def test_the_metric_counts_a_marking_once_per_person(db, signed_in, portal):
    """A double click is one false positive, not two - the number is what an
    operator judges a rule by."""
    class _Metrics:
        def __init__(self):
            self.calls = []

        def alert_marked_expected(self, rule):
            self.calls.append(rule)

    metrics = _Metrics()
    port = _free_port()
    srv = serve_portal(db, port, metrics=metrics)
    try:
        host_port = ("127.0.0.1", port)
        aid, _token = signed_in
        token = _login(host_port, EMAIL, PASSWORD)
        alert_id = _alert(db, aid, rule="withdrawal_unseen_address")
        for _ in range(2):
            _request(host_port, "POST", "/expected",
                     body={"alert_id": str(alert_id)}, cookie=token)
        assert metrics.calls == ["withdrawal_unseen_address"]
    finally:
        srv.shutdown()
