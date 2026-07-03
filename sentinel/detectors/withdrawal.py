from __future__ import annotations

from sentinel.detectors.base import Detection


def detect_new_withdrawals(new_withdrawal_records: list[dict]) -> list[Detection]:
    """new_withdrawal_records must already be filtered down to withdrawals
    NOT previously seen (see baseline.record_seen_withdrawal). Fires
    CRITICAL on first sighting regardless of status - pending/in-flight
    withdrawals are alerted on, not just completed ones. Bitget's withdrawal
    status enum (pending, pending_review, pending_fail, pending_review_fail,
    reject, success) is a real in-flight state machine, confirmed in
    docs/phase0-capability-audit.md section 1."""
    detections = []
    for record in new_withdrawal_records:
        detections.append(
            Detection(
                rule="withdrawal_initiated",
                severity="CRITICAL",
                confidence="high",
                message=(
                    f"Withdrawal of {record.get('amount')} {record.get('currency')} "
                    f"initiated, status={record.get('status')}"
                ),
                evidence={
                    "order_id": record.get("id"),
                    "coin": record.get("currency"),
                    "amount": record.get("amount"),
                    "status": record.get("status"),
                    "address": record.get("address"),
                    "timestamp": record.get("timestamp"),
                },
            )
        )
    return detections
