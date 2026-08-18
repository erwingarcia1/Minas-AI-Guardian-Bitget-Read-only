"""Every field in a withdrawal alert, and what breaks when it is wrong (#65).

`detectors/withdrawal.py` is the CRITICAL path — it is the detector that fires
when money leaves — and it is the worst-covered module in the codebase by
mutation score. It is also structurally invisible to `scripts/mutation-check.sh`:
that tool keeps only mutations to comparisons, boolean operators and control
flow, and this module has none. It is a straight-line `Detection` builder. Its
mutations are *evidence keys and message text*, which the restricted run
discards as cosmetic.

They are not cosmetic here. The evidence dict is not a debugging aid; it is
read by `dispatch._dedup_key` to decide whether two withdrawals are the same
withdrawal, and it is what a customer and an exchange read to decide what
happened. So this file works down the dict field by field and asks what breaks
if each one is wrong or missing:

| field       | what it feeds                            | what a wrong value costs |
|-------------|------------------------------------------|--------------------------|
| `order_id`  | `_dedup_key` identity, support tickets    | two withdrawals become one alert; the second is suppressed for a whole cooldown |
| `coin`      | `_dedup_key`, the message                 | the alert names the wrong asset; dedup collapses further |
| `amount`    | the message, the derived identity         | the customer cannot tell how much left |
| `address`   | `_dedup_key`, the message                 | the customer chases the wrong destination |
| `status`    | the message                               | an in-flight withdrawal reads as settled, or the reverse |
| `timestamp` | forensics and ordering                    | the incident timeline is wrong |

The `order_id` row is G-11's shape, and G-11 was a real shipped defect: an
id-less withdrawal was reported as "already seen" and never reached this
detector at all. The fix put a derived identity in that field. Nothing pinned
that the *derived* identity actually distinguishes two withdrawals, which is
the only reason it is there.
"""
from __future__ import annotations

from sentinel.alerts.dispatch import _dedup_key
from sentinel.detectors.withdrawal import detect_new_withdrawals

ACCOUNT = 1


def _record(**overrides):
    record = {
        "id": "wd-1",
        "currency": "BTC",
        "amount": "0.5",
        "status": "pending",
        "address": "bc1qcold",
        "timestamp": 1_700_000_000_000,
        "network": "BTC",
        "type": "withdrawal",
        "tag": None,
    }
    record.update(overrides)
    return {k: v for k, v in record.items() if v is not _ABSENT}


class _Absent:
    def __repr__(self):
        return "<absent>"


_ABSENT = _Absent()


def _only(records):
    detections = detect_new_withdrawals(records)
    assert len(detections) == 1
    return detections[0]


# --- order_id: the field that decides whether two withdrawals are one --------


def test_two_withdrawals_with_different_exchange_ids_are_two_alerts():
    a, b = detect_new_withdrawals([_record(id="wd-1"), _record(id="wd-2")])

    assert a.evidence["order_id"] == "wd-1"
    assert b.evidence["order_id"] == "wd-2"
    assert _dedup_key(ACCOUNT, a) != _dedup_key(ACCOUNT, b)


def test_two_id_less_withdrawals_differing_only_in_amount_are_two_alerts():
    """G-11's shape, and the reason `order_id` carries a derived identity
    rather than None.

    An exchange that returns no reference for a withdrawal is not exotic — it
    is what G-11 was filed about. With None in this field, `_dedup_key` skips
    it and the identity collapses to coin plus address; a second withdrawal of
    the same coin to the same address is then the *same alert*, and it is
    suppressed for a full CRITICAL cooldown. That is an attacker draining an
    account in instalments to one destination, alerted on once."""
    a, b = detect_new_withdrawals([
        _record(id=_ABSENT, txid=_ABSENT, amount="0.5"),
        _record(id=_ABSENT, txid=_ABSENT, amount="1.5"),
    ])

    assert a.evidence["order_id"] != b.evidence["order_id"]
    assert _dedup_key(ACCOUNT, a) != _dedup_key(ACCOUNT, b)


def test_two_id_less_withdrawals_to_different_addresses_are_two_alerts():
    a, b = detect_new_withdrawals([
        _record(id=_ABSENT, txid=_ABSENT, address="bc1qcold"),
        _record(id=_ABSENT, txid=_ABSENT, address="bc1qattacker"),
    ])

    assert _dedup_key(ACCOUNT, a) != _dedup_key(ACCOUNT, b)


def test_the_same_withdrawal_seen_twice_keeps_one_identity():
    """The other half. If the derived identity were not stable — a timestamp
    taken at detection time, a random component — every re-poll of an id-less
    withdrawal would be a new alert, and the customer would be paged
    repeatedly for one movement while it settles."""
    a = _only([_record(id=_ABSENT, txid=_ABSENT)])
    b = _only([_record(id=_ABSENT, txid=_ABSENT)])

    assert _dedup_key(ACCOUNT, a) == _dedup_key(ACCOUNT, b)


def test_a_settling_withdrawal_keeps_its_identity_as_its_status_changes():
    """A withdrawal record mutates while it settles: `status` walks the
    exchange's pending → pending_review → success machine and `txid` appears
    partway through. If those fed the identity, one movement would page the
    customer three times — which is how a real alert gets muted."""
    pending = _only([_record(id=_ABSENT, txid=_ABSENT, status="pending")])
    review = _only([_record(id=_ABSENT, txid=_ABSENT, status="pending_review")])
    done = _only([_record(id=_ABSENT, txid=_ABSENT, status="success")])

    assert _dedup_key(ACCOUNT, pending) == _dedup_key(ACCOUNT, review) == _dedup_key(ACCOUNT, done)


def test_the_exchange_reference_is_used_verbatim_when_there_is_one():
    """Not the digest. An operator pastes this value into a support ticket
    with the exchange, and a sha256 of our own devising is not a thing the
    exchange can look up."""
    detection = _only([_record(id="BG-99887766")])

    assert detection.evidence["order_id"] == "BG-99887766"


def test_txid_is_used_when_the_exchange_gives_no_id():
    """Some exchanges reference a withdrawal only by its on-chain transaction.
    That is still a reference the exchange recognises, so it beats the
    digest."""
    detection = _only([_record(id=_ABSENT, txid="0xdeadbeef")])

    assert detection.evidence["order_id"] == "0xdeadbeef"


def test_order_id_is_never_none_or_empty():
    """The single most consequential property in this file. `_dedup_key` skips
    evidence fields that are None, so an empty `order_id` silently widens the
    dedup identity instead of failing."""
    for record in (_record(id=_ABSENT, txid=_ABSENT),
                   _record(id=None, txid=None),
                   _record(id="", txid="")):
        order_id = _only([record]).evidence["order_id"]
        assert order_id, f"empty order_id for {record}"


# --- the fields a human reads -------------------------------------------------


def test_the_message_names_the_amount_the_coin_and_the_status():
    """This text is the SMS. A customer deciding whether to phone the exchange
    reads it on a lock screen, and "a withdrawal happened" is not a basis for
    that decision — how much, of what, and whether it has already settled
    are."""
    message = _only([_record(amount="0.5", currency="BTC", status="pending")]).message

    assert "0.5" in message
    assert "BTC" in message
    assert "pending" in message


def test_every_evidence_field_carries_the_records_own_value():
    """Field-by-field, because a copy-paste that reads `currency` into
    `address` produces a perfectly well-formed alert pointing at the wrong
    destination — and the customer chases the wrong address while the real one
    keeps receiving."""
    detection = _only([_record(
        currency="ETH", amount="12.25", status="success",
        address="0xdestination", timestamp=1_699_999_999_000,
    )])

    assert detection.evidence["coin"] == "ETH"
    assert detection.evidence["amount"] == "12.25"
    assert detection.evidence["status"] == "success"
    assert detection.evidence["address"] == "0xdestination"
    assert detection.evidence["timestamp"] == 1_699_999_999_000


def test_a_missing_field_is_absent_rather_than_wrong():
    """An exchange that omits the destination must not produce an alert that
    names some other field's value as the address. None is a truthful "we were
    not told"; a wrong address is a lie the customer acts on."""
    detection = _only([_record(address=_ABSENT, timestamp=_ABSENT)])

    assert detection.evidence["address"] is None
    assert detection.evidence["timestamp"] is None
    assert detection.evidence["coin"] == "BTC"  # the others are unaffected


# --- what the alert is ---------------------------------------------------------


def test_a_pending_withdrawal_alerts_at_critical():
    """The point of the whole detector. A completed withdrawal is a report; a
    pending one is the only window in which the customer can still call the
    exchange and stop it. Downgrading or delaying the in-flight case would
    make this product a record-keeper."""
    detection = _only([_record(status="pending")])

    assert detection.rule == "withdrawal_initiated"
    assert detection.severity == "CRITICAL"
    assert detection.confidence == "high"


def test_every_status_in_the_exchanges_state_machine_alerts():
    """Bitget's enum, from the phase-0 capability audit. A status this code has
    never seen must alert too — an unrecognised state on a money-leaving path
    is a reason to be louder, not quieter."""
    statuses = ["pending", "pending_review", "pending_fail", "pending_review_fail",
                "reject", "success", "some_new_state_bitget_added_last_week"]

    detections = detect_new_withdrawals([_record(id=f"wd-{s}", status=s) for s in statuses])

    assert len(detections) == len(statuses)
    assert {d.severity for d in detections} == {"CRITICAL"}


def test_every_record_produces_its_own_detection_in_order():
    """No deduplication, no early exit, no batching. Deduplication is
    `dispatch`'s job and it needs to see all of them to do it; a detector that
    collapsed or truncated here would lose withdrawals before anything could
    decide they were the same."""
    records = [_record(id=f"wd-{i}", amount=str(i)) for i in range(5)]

    detections = detect_new_withdrawals(records)

    assert [d.evidence["order_id"] for d in detections] == [f"wd-{i}" for i in range(5)]
    assert [d.evidence["amount"] for d in detections] == [str(i) for i in range(5)]


def test_the_rule_name_is_exact():
    """`alerting.routing`, the per-rule cooldown overrides and the customer's
    alert history all key off this string. A rename that looks like a tidy-up
    silently drops the rule out of its routing entry — which does not fail, it
    just stops sending."""
    assert _only([_record()]).rule == "withdrawal_initiated"


# --- a collision this detector cannot see, recorded here rather than hidden ---


def test_two_identical_undated_reference_less_withdrawals_in_one_response_are_distinguishable():
    """#67, fixed for the case that is provable: a response does not list one
    movement twice, so two identical unidentifiable records arriving TOGETHER
    are two withdrawals - an attacker draining an account in equal
    instalments to one destination produces exactly these. The batch
    annotation is what makes the identities distinct; bare records remain
    indistinguishable because they genuinely are."""
    from sentinel.identity import annotate_batch_occurrences, withdrawal_identity

    first = {"currency": "BTC", "amount": "0.5", "address": "bc1qattacker",
             "network": "BTC", "type": "withdrawal"}
    second = dict(first)
    annotate_batch_occurrences([first, second])

    assert withdrawal_identity(first) != withdrawal_identity(second)


def test_the_same_batch_re_polled_keeps_the_same_identities():
    """The property the ordinals must not break: one settling withdrawal
    keeps one identity, or every poll re-pages the customer for the same
    movement. The identity SET is stable across polls."""
    from sentinel.identity import annotate_batch_occurrences, withdrawal_identity

    def batch():
        first = {"currency": "BTC", "amount": "0.5", "address": "bc1qattacker",
                 "network": "BTC", "type": "withdrawal"}
        return [first, dict(first)]

    poll1, poll2 = batch(), batch()
    annotate_batch_occurrences(poll1)
    annotate_batch_occurrences(poll2)

    assert {withdrawal_identity(w) for w in poll1} == {withdrawal_identity(w) for w in poll2}


def test_the_first_occurrence_keeps_its_pre_fix_identity():
    """Upgrade safety: the first occurrence's identity must stay
    byte-identical to what earlier versions recorded, or every already-seen
    unreferenced withdrawal re-alerts on deploy."""
    from sentinel.identity import annotate_batch_occurrences, withdrawal_identity

    record = {"currency": "BTC", "amount": "0.5", "address": "bc1qattacker",
              "network": "BTC", "type": "withdrawal"}
    before = withdrawal_identity(record)
    annotate_batch_occurrences([record, dict(record)])

    assert withdrawal_identity(record) == before


def test_records_with_a_reference_or_a_timestamp_are_never_annotated():
    """The ordinal exists only where the identity can genuinely collide.
    A referenced or dated record is already unique; touching it would
    change identities that are working."""
    from sentinel.identity import annotate_batch_occurrences, withdrawal_identity

    dated = {"currency": "BTC", "amount": "0.5", "address": "bc1qx",
             "network": "BTC", "type": "withdrawal", "timestamp": 1755500000000}
    referenced = {"currency": "BTC", "amount": "0.5", "address": "bc1qx",
                  "network": "BTC", "type": "withdrawal", "id": "W123"}
    d2, r2 = dict(dated), dict(referenced)
    before = [withdrawal_identity(x) for x in (dated, d2, referenced, r2)]
    annotate_batch_occurrences([dated, d2, referenced, r2])

    assert [withdrawal_identity(x) for x in (dated, d2, referenced, r2)] == before
    # Identical dated records still collide - the timestamp is the exchange's
    # claim they are one movement re-listed, and second-guessing it would
    # re-page settling withdrawals.
    assert withdrawal_identity(dated) == withdrawal_identity(d2)


def test_across_responses_the_residual_collision_remains_and_is_the_paged_case():
    """Stated, not hidden: a record that ages out and is replaced by an
    identical newcomer hands the newcomer its identity. No scheme can tell
    "aged out then replaced" from "still there" with nothing stable to key
    on - which is exactly why _report_unidentifiable pages on every poll
    carrying such records. If this test ever fails, the residual has been
    closed and that page's message needs rewriting."""
    from sentinel.identity import annotate_batch_occurrences, withdrawal_identity

    departed = {"currency": "BTC", "amount": "0.5", "address": "bc1qattacker",
                "network": "BTC", "type": "withdrawal"}
    annotate_batch_occurrences([departed])
    newcomer = {"currency": "BTC", "amount": "0.5", "address": "bc1qattacker",
                "network": "BTC", "type": "withdrawal"}
    annotate_batch_occurrences([newcomer])

    assert withdrawal_identity(newcomer) == withdrawal_identity(departed)
