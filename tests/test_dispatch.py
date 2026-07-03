from __future__ import annotations

from sentinel.alerts.dispatch import dispatch
from sentinel.detectors.base import Detection
from tests.conftest import make_account


class RecordingChannel:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


def test_alert_is_sent_once_and_deduped_within_cooldown(db):
    channel = RecordingChannel()
    channels = {"CRITICAL": [channel]}
    account_id = make_account(db)
    detection = Detection(
        rule="withdrawal_initiated", severity="CRITICAL", confidence="high", message="m",
        evidence={"order_id": "wd-1"},
    )

    sent_first = dispatch(db, channels, account_id, "a", detection, {"CRITICAL": 15})
    sent_second = dispatch(db, channels, account_id, "a", detection, {"CRITICAL": 15})

    assert sent_first is True
    assert sent_second is False
    assert len(channel.sent) == 1


def test_different_evidence_is_not_deduped_together(db):
    channel = RecordingChannel()
    channels = {"CRITICAL": [channel]}
    account_id = make_account(db)
    d1 = Detection(
        rule="withdrawal_initiated", severity="CRITICAL", confidence="high", message="m",
        evidence={"order_id": "wd-1"},
    )
    d2 = Detection(
        rule="withdrawal_initiated", severity="CRITICAL", confidence="high", message="m",
        evidence={"order_id": "wd-2"},
    )

    dispatch(db, channels, account_id, "a", d1, {"CRITICAL": 15})
    sent = dispatch(db, channels, account_id, "a", d2, {"CRITICAL": 15})

    assert sent is True
    assert len(channel.sent) == 2
