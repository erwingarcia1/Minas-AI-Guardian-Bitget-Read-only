"""Silencing an alert you are already dealing with, and nothing else (G-26, #30).

A snooze is the only feature in this product whose *purpose* is to stop an
alert reaching someone. Everything else that suppresses — dedup, cooldown,
severity routing — suppresses a repeat of something already delivered. A snooze
suppresses a delivery outright, at the customer's request, and the request
arrives at the exact moment a customer is most likely to be wrong about what is
happening to their account.

So the tests are written around the ways a snooze could suppress more than the
customer asked for:

- a snooze is keyed on the *specific alert*, so a second, different occurrence
  of the same rule still fires — a withdrawal to a new address is not the
  withdrawal you were dealing with;
- a snooze never crosses to another rule, another account, or another
  customer;
- a snooze cannot be permanent, and cannot be made permanent by asking for a
  long enough duration;
- an expired snooze suppresses nothing, including for escalation;
- a snoozed alert is still detected, still recorded, and still visible — the
  customer silenced their phone, not the monitoring.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sentinel.alerts.dispatch import _dedup_key, dispatch
from sentinel.alerts.escalation import Escalator
from sentinel.detectors.base import Detection
from sentinel.snooze import MAX_SNOOZE_HOURS, active_snooze, snooze_alert
from tests.conftest import make_account

COOLDOWN = {"CRITICAL": 15, "HIGH": 30, "MEDIUM": 720, "LOW": 1440}


class RecordingChannel:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


def _detection(rule="withdrawal_initiated", severity="CRITICAL", **evidence):
    return Detection(rule=rule, severity=severity, confidence="high",
                     message="0.5 BTC left the account", evidence=evidence or {"order_id": "wd-1"})


def _dispatch(db, channel, account_id, detection, label="acct-1"):
    return dispatch(db, {detection.severity: [channel]}, account_id, label, detection, COOLDOWN)


def _alerts(db, account_id):
    with db.cursor() as cur:
        cur.execute(
            "SELECT rule, delivered_to, snoozed_until FROM alerts WHERE account_id = ? "
            "ORDER BY id", (account_id,))
        return cur.fetchall()


# --- a snooze covers the alert you snoozed, not the rule ----------------------


def test_a_second_different_occurrence_of_the_same_rule_still_fires(db):
    """The failure that would make this feature dangerous.

    A customer snoozes "withdrawal to an unrecognised address" because they
    recognise *that* address. If the snooze covered the rule, the attacker's
    next withdrawal — to a different address, which is the whole reason the
    rule exists — arrives silently. Keying on the alert's own identity is what
    keeps the second one loud."""
    account_id = make_account(db)
    channel = RecordingChannel()
    first = _detection(order_id="wd-1", address="known-cold-wallet")
    _dispatch(db, channel, account_id, first)
    assert len(channel.sent) == 1

    snooze_alert(db, account_id, _dedup_key(account_id, first), first.rule,
                 hours=4, requested_by="alice@example.com")

    # Same rule, different withdrawal.
    _dispatch(db, channel, account_id, _detection(order_id="wd-2", address="attacker-wallet"))

    assert len(channel.sent) == 2
    assert channel.sent[1].evidence["address"] == "attacker-wallet"


def test_the_snoozed_alert_itself_is_silenced(db):
    """And the thing the customer actually asked for still works, so the test
    above is not passing because snoozing does nothing at all."""
    account_id = make_account(db)
    channel = RecordingChannel()
    detection = _detection(order_id="wd-1")
    _dispatch(db, channel, account_id, detection)

    snooze_alert(db, account_id, _dedup_key(account_id, detection), detection.rule,
                 hours=4, requested_by="alice@example.com")
    # Far enough past the CRITICAL cooldown that only the snooze can suppress it.
    with db.cursor() as cur:
        cur.execute("UPDATE alerts SET sent_at = ? WHERE account_id = ?",
                    ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), account_id))

    _dispatch(db, channel, account_id, detection)

    assert len(channel.sent) == 1


def test_a_snooze_on_one_rule_never_silences_another(db):
    """The stated criterion. Two rules firing on the same underlying
    withdrawal share an account and an order id — everything except the rule
    name — so this is where a key built from the wrong fields shows up."""
    account_id = make_account(db)
    channel = RecordingChannel()
    withdrawal = _detection(rule="withdrawal_initiated", order_id="wd-1")
    unknown_address = _detection(rule="withdrawal_to_unknown_address", order_id="wd-1")

    snooze_alert(db, account_id, _dedup_key(account_id, withdrawal), withdrawal.rule,
                 hours=4, requested_by="alice@example.com")
    _dispatch(db, channel, account_id, unknown_address)

    assert [a.rule for a in channel.sent] == ["withdrawal_to_unknown_address"]


def test_a_snooze_never_crosses_to_another_account(db):
    """Two accounts belonging to the same customer produce identical
    detections all the time — the same coin, the same exchange, often the same
    order ids from a mirrored setup. The account has to be part of the key."""
    alice = make_account(db, label="alice-1")
    bob = make_account(db, label="bob-1")
    channel = RecordingChannel()
    detection = _detection(order_id="wd-1")

    snooze_alert(db, alice, _dedup_key(alice, detection), detection.rule,
                 hours=4, requested_by="alice@example.com")
    _dispatch(db, channel, bob, detection, label="bob-1")

    assert len(channel.sent) == 1


# --- a snooze always expires --------------------------------------------------


def test_an_expired_snooze_suppresses_nothing(db):
    account_id = make_account(db)
    channel = RecordingChannel()
    detection = _detection(order_id="wd-1")
    key = _dedup_key(account_id, detection)
    snooze_alert(db, account_id, key, detection.rule, hours=4, requested_by="alice@example.com")
    with db.cursor() as cur:  # wind it past its expiry
        cur.execute("UPDATE snoozes SET expires_at = ? WHERE dedup_key = ?",
                    ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), key))

    assert active_snooze(db, account_id, key) is None
    _dispatch(db, channel, account_id, detection)
    assert len(channel.sent) == 1


def test_a_snooze_cannot_be_asked_for_indefinitely(db):
    """"Snoozes always expire — there is no permanent mute."

    A cap that only lives in the dropdown is not a cap: the form is a POST
    anyone can retype. The clamp is in the function that writes the row, and
    the column is NOT NULL, so the table cannot represent a snooze that never
    ends even if every caller is wrong."""
    account_id = make_account(db)
    before = datetime.now(timezone.utc)

    row = snooze_alert(db, account_id, "k", "withdrawal_initiated",
                       hours=24 * 3650, requested_by="alice@example.com")

    expires = datetime.fromisoformat(row["expires_at"])
    assert expires <= before + timedelta(hours=MAX_SNOOZE_HOURS) + timedelta(seconds=5)


@pytest.mark.parametrize("hours", [0, -1, -10_000])
def test_a_zero_or_negative_duration_is_not_a_snooze(db, hours):
    """`hours=0` reads as "snooze for no time", which is a no-op — but through
    a naive `now + timedelta(hours=hours)` it writes a row that is already
    expired, and through a negative one it writes a snooze that expired in the
    past and would look like a mute if any comparison were ever flipped.
    Neither is a thing a customer can ask for."""
    account_id = make_account(db)

    assert snooze_alert(db, account_id, "k", "withdrawal_initiated",
                        hours=hours, requested_by="alice@example.com") is None
    assert active_snooze(db, account_id, "k") is None


# --- a snooze silences the phone, not the monitoring --------------------------


def test_a_snoozed_alert_is_still_detected_and_recorded(db):
    """"Snoozed alerts stay visible in the portal."

    The alert row is written whether or not anyone was told — the same rule
    #57 established for a failed delivery. A snooze that dropped the detection
    would leave nothing in the history a customer could later hand to an
    exchange, and would make "I silenced it" indistinguishable from "it never
    happened"."""
    account_id = make_account(db)
    channel = RecordingChannel()
    detection = _detection(order_id="wd-1")
    snooze_alert(db, account_id, _dedup_key(account_id, detection), detection.rule,
                 hours=4, requested_by="alice@example.com")

    _dispatch(db, channel, account_id, detection)

    rows = _alerts(db, account_id)
    assert len(rows) == 1
    assert rows[0]["rule"] == "withdrawal_initiated"
    assert channel.sent == []


def test_a_snoozed_alert_is_marked_as_snoozed_not_as_undelivered(db):
    """`delivered_to = 0` already means "we tried to tell someone and reached
    nobody" (#57) — an operator-visible fault. A snooze is the opposite: it
    worked exactly as asked. Sharing one column would turn every snooze into a
    false report of a broken alert channel."""
    account_id = make_account(db)
    detection = _detection(order_id="wd-1")
    snooze_alert(db, account_id, _dedup_key(account_id, detection), detection.rule,
                 hours=4, requested_by="alice@example.com")

    _dispatch(db, RecordingChannel(), account_id, detection)

    row = _alerts(db, account_id)[0]
    assert row["snoozed_until"] is not None
    assert row["delivered_to"] == 0


def test_an_unsnoozed_alert_carries_no_snooze_marker(db):
    """So the marker above means something."""
    account_id = make_account(db)
    _dispatch(db, RecordingChannel(), account_id, _detection(order_id="wd-1"))

    assert _alerts(db, account_id)[0]["snoozed_until"] is None


# --- escalation ----------------------------------------------------------------


def test_escalation_holds_while_the_snooze_is_live_and_resumes_after(db):
    """A snooze that escalation ignored would page the customer anyway and the
    button would be a lie. A snooze that suppressed escalation *permanently*
    would be the permanent mute this feature is not allowed to have. So it
    holds, and then it lets go — which is the whole reason snoozes expire."""
    account_id = make_account(db)
    detection = _detection(order_id="wd-1")
    key = _dedup_key(account_id, detection)
    snooze_alert(db, account_id, key, detection.rule, hours=4, requested_by="alice@example.com")
    _dispatch(db, RecordingChannel(), account_id, detection)
    with db.cursor() as cur:  # old enough to escalate
        cur.execute("UPDATE alerts SET sent_at = ? WHERE account_id = ?",
                    ((datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(), account_id))

    assert Escalator([], unacknowledged_minutes=15).check(db) == 0

    with db.cursor() as cur:
        cur.execute("UPDATE snoozes SET expires_at = ? WHERE dedup_key = ?",
                    ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), key))

    assert Escalator([], unacknowledged_minutes=15).check(db) == 1


# --- the record of who asked --------------------------------------------------


def test_a_snooze_records_who_asked_for_it_and_until_when(db):
    """Someone silenced an alarm on an account that may later be the subject of
    an exchange dispute. That is a fact about a person and a time, and it has
    to survive the snooze expiring."""
    account_id = make_account(db)

    row = snooze_alert(db, account_id, "k", "withdrawal_initiated",
                       hours=4, requested_by="alice@example.com")

    assert row["requested_by"] == "alice@example.com"
    assert row["rule"] == "withdrawal_initiated"
    assert datetime.fromisoformat(row["expires_at"]) > datetime.now(timezone.utc)
    assert datetime.fromisoformat(row["created_at"]) <= datetime.now(timezone.utc)
