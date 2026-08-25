"""Sibling alerts: two detections born of one customer action (#99).

#369 and #370 were one withdrawal - same account, same exchange
order_id, seven seconds apart. One was acknowledged; the other stayed
open and escalated to the emergency contact about an incident already
handled. Acknowledging should close the ACTION, not one detector's view
of it.

The definition is deliberately conservative: same account, same
non-empty `order_id` in the evidence, within 24 hours. The order
reference is the one identifier that provably means "the same customer
action"; a false co-acknowledgement removes attention from an unrelated
alert, so false negatives - leaving an alert open - are the safe failure
direction. Widening (shared address, burst membership, time clusters) is
decision 18, the owner's call.

Comparison happens in Python rather than SQL json_extract: exchanges
have sent order ids as strings and as numbers, and a type-strict SQL
compare would silently split real siblings.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sentinel.db import Database

logger = logging.getLogger("sentinel.alerts.siblings")

SIBLING_WINDOW_HOURS = 24


def sibling_alert_ids(db: Database, alert_id: int) -> list[int]:
    """Unacknowledged alerts born of the same customer action as
    `alert_id`. Empty when the alert carries no order reference - no
    identifier, no claim of sameness."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT account_id, sent_at, evidence_json FROM alerts WHERE id = ?",
            (alert_id,))
        row = cur.fetchone()
    if row is None:
        return []
    try:
        order_id = json.loads(row["evidence_json"]).get("order_id")
    except (ValueError, TypeError):
        return []
    if order_id is None or str(order_id).strip() == "":
        return []

    anchor = datetime.fromisoformat(row["sent_at"])
    window = timedelta(hours=SIBLING_WINDOW_HOURS)
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, sent_at, evidence_json FROM alerts "
            "WHERE account_id = ? AND id != ? AND acknowledged_at IS NULL",
            (row["account_id"], alert_id))
        candidates = cur.fetchall()

    siblings = []
    for candidate in candidates:
        try:
            if abs(datetime.fromisoformat(candidate["sent_at"]) - anchor) > window:
                continue
            candidate_order = json.loads(candidate["evidence_json"]).get("order_id")
        except (ValueError, TypeError):
            continue
        if candidate_order is not None and str(candidate_order) == str(order_id):
            siblings.append(int(candidate["id"]))
    return siblings
