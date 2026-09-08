from __future__ import annotations

from sentinel.detectors.base import Detection
from sentinel.identity import evidence_reference, withdrawal_identity


def detect_new_withdrawals(new_withdrawal_records: list[dict]) -> list[Detection]:
    """new_withdrawal_records must already be filtered down to withdrawals
    NOT previously seen (see baseline.record_seen_withdrawal). Fires
    CRITICAL on first sighting regardless of status - pending/in-flight
    withdrawals are alerted on, not just completed ones. Bitget's withdrawal
    status enum (pending, pending_review, pending_fail, pending_review_fail,
    reject, success) is a real in-flight state machine, confirmed in
    docs/phase0-capability-audit.md section 1.

    `order_id` in the evidence is the exchange's own reference where there is
    one, and the derived identity where there isn't. That field is not
    cosmetic: `dispatch._dedup_key` falls back to the *coin* when it is empty,
    so leaving it None makes two unrelated unreferenced withdrawals of the same
    coin a single alert and suppresses the second for a full cooldown (G-11)."""
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
                    "order_id": evidence_reference(record, withdrawal_identity(record)),
                    "coin": record.get("currency"),
                    "amount": record.get("amount"),
                    "status": record.get("status"),
                    "address": record.get("address"),
                    "timestamp": record.get("timestamp"),
                },
            )
        )
    return detections


FAMILIAR_DESTINATION_NOTE = "destination this account has withdrawn to before"


def reclassify_familiar_destinations(detections: list[Detection], familiar: set[str]) -> list[Detection]:
    """Severity by address history (2026-09-08). A withdrawal to an address
    THIS ACCOUNT HAS WITHDRAWN TO BEFORE is not a CRITICAL: it is a receipt -
    money left, to a destination the customer chose once already. An
    attacker reusing the customer's own destination is sending funds to the
    customer.

    `familiar` must be built from known_addresses rows with source =
    'withdrawal_history' ONLY. A deposit_history address means someone sent
    FROM there; it does not prove the customer chose it as a destination,
    so deposit-only stays unknown and the withdrawal stays CRITICAL.

    Fail safe: a record with no address, or an address with no withdrawal
    history, is untouched. Only withdrawal_initiated is judged; the
    unseen-address and burst rules never pass through here."""
    for detection in detections:
        if detection.rule != "withdrawal_initiated":
            continue
        address = detection.evidence.get("address")
        if not address or address not in familiar:
            continue
        detection.severity = "MEDIUM"
        detection.receipt = True
        detection.evidence["destination"] = FAMILIAR_DESTINATION_NOTE
        detection.message = f"{detection.message} - {FAMILIAR_DESTINATION_NOTE}"
    return detections

