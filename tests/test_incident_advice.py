"""Decisions 3 and 9 (2026-08-27): what the product tells a customer to do
next - one canonical text, on the alert email and the incident page, from
one source so they cannot drift.

The order: revoke unrecognised keys -> check the exchange's own withdrawal
history -> contact exchange support with a reference. "Move your funds" is
not a step; it is only something to raise with the exchange. The line:
instruct for the reversible, never the irreversible. The tone: certain
about what was observed, never about what it means - and no hedging
preamble, because a summariser strips it. So every sentence must read
correctly ALONE.
"""
from __future__ import annotations

import re
from pathlib import Path

from sentinel.alerts.base import (
    ACCOUNT_ADVICE,
    COVERAGE_ADVICE,
    DATA_GAP_ADVICE,
    NO_ADVICE,
    RULE_ADVICE,
    AlertMessage,
    advice_kind,
    advice_lead,
    advice_note,
    advice_steps,
    customer_advised_line,
    monitoring_note,
    runbook,
)
from tests.test_incident_page import _incident, _request, alice, portal  # noqa: F401 - fixtures

INFERENCES = ("compromis", "attacker", "hacked", "hack ", "theft", "stolen", "breach", "intruder", "fraud")
HEDGES = ("if this was not you", "if this wasn't you", "if you did not authorise", "may have", "might",
          "possibly", "we think", "we believe", "it looks like")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


# --- the order and the line ---------------------------------------------------

def test_the_three_steps_in_the_decided_order():
    steps = advice_steps("bitget")
    assert len(steps) == 3
    assert steps[0].startswith("Revoke any API key on this account that you do not recognise")
    assert steps[1].startswith("Check the withdrawal history in Bitget's own app")
    assert steps[2].startswith("Contact Bitget's support, quote the incident number above")


def test_moving_funds_is_never_an_instruction_only_something_to_raise_with_the_exchange():
    text = " ".join(advice_steps())
    assert "Move your funds" not in text and "move your funds to" not in text.lower()
    assert "what they advise about moving your funds" in advice_steps()[2]
    # and it lives only in the exchange step
    assert "moving your funds" not in advice_steps()[0] and "moving your funds" not in advice_steps()[1]


def test_no_step_instructs_an_irreversible_act():
    text = " ".join(advice_steps()).lower()
    for irreversible in ("move your funds", "transfer your funds", "withdraw your", "send your",
                         "close the account", "delete ", "freeze"):
        assert irreversible not in text, irreversible


def test_the_guardians_own_key_is_named_so_it_is_not_revoked_blind():
    assert "Leave the Minas AI Guardian key in place" in advice_steps()[0]
    assert "read-only" in advice_steps()[0]
    assert "stops your monitoring" in advice_steps()[0]


# --- the tone: observed, not inferred; no hedging ---------------------------------

def test_every_sentence_reads_correctly_in_isolation():
    """Each sentence, alone: starts with a capital, ends with a full stop,
    is an instruction for a reversible act or a plain statement, and says
    nothing about what the event MEANS."""
    texts = [*advice_steps("bitget"), monitoring_note()]
    for rule in ("account_unreachable", "unseen_address_check_degraded"):
        texts += [*advice_steps("bitget", rule=rule), advice_lead(rule), customer_advised_line(rule)]
    for step in texts:
        for sentence in _sentences(step):
            assert sentence[0].isupper() and sentence.endswith("."), sentence
            low = sentence.lower()
            for word in INFERENCES:
                assert word not in low, f"inference {word!r} in: {sentence}"
            for hedge in HEDGES:
                assert hedge not in low, f"hedge {hedge!r} in: {sentence}"


def test_the_steps_open_with_a_plain_instruction_not_a_conditional_preamble():
    for step in advice_steps():
        first = _sentences(step)[0]
        assert re.match(r"^(Revoke|Check|Contact) ", first), first


# --- one source, two surfaces -----------------------------------------------------

def test_the_email_runbook_is_the_numbered_steps_plus_the_note():
    text = runbook("bitget")
    for i, step in enumerate(advice_steps("bitget"), 1):
        assert f"{i}. {step}" in text
    assert monitoring_note() in text
    assert "4." not in text.split(monitoring_note())[0]


def test_the_incident_page_carries_exactly_the_same_steps(portal, db, alice):  # noqa: F811
    account_id, token = alice
    incident = _incident(db, account_id)
    status, body = _request(portal, f"/incident?id={incident}", cookie=token)
    assert status == 200
    assert "What to do next" in body
    import html as _html

    for step in advice_steps("bitget"):
        assert _html.escape(step) in body or step in body, step
    assert _html.escape(monitoring_note()) in body or monitoring_note() in body
    assert "Move your funds" not in body


def test_the_page_and_the_email_share_one_function_not_two_copies():
    """The guard against drift: the portal renders `advice_steps`, not a
    string of its own."""
    from pathlib import Path

    portal_src = (Path(__file__).resolve().parent.parent / "sentinel" / "portal.py").read_text()
    assert "advice_steps(alert[\"exchange\"], rule=alert[\"rule\"], cause=cause)" in portal_src
    assert "Revoke any API key" not in portal_src, "the portal must not carry its own copy of the text"


def test_an_unknown_exchange_reads_as_the_exchange():
    steps = advice_steps(None)
    assert "the exchange's own app" in steps[1] and "Contact the exchange's support" in steps[2]


def test_the_customer_email_still_carries_the_steps_for_a_critical():
    alert = AlertMessage(account_label="acct", rule="withdrawal_initiated", severity="CRITICAL",
                         confidence="high", message="A withdrawal was initiated.", evidence={})
    body = alert.render_customer()
    assert advice_steps()[0] in body


# --- the advice is a property of the rule (2026-09-03) -------------------------

_SOURCE_ROOT = Path(__file__).resolve().parent.parent / "sentinel"
_RULE_LITERAL = re.compile(
    r"""(?:\b[a-z_]*rule=|\b[A-Z_]*RULE(?::\s*str)?\s*=\s*|"rule":\s*)["']([a-z][a-z0-9_]*)["']""")


def _rules_in_the_source() -> set[str]:
    """Every rule identifier written as a literal anywhere in sentinel/:
    a detector's `rule="..."`, an operator page's `rule="..."`, a notice's
    `subject_rule="..."`, a module constant `X_RULE = "..."`, a dict's
    `"rule": "..."`."""
    found = set()
    for path in _SOURCE_ROOT.rglob("*.py"):
        found.update(_RULE_LITERAL.findall(path.read_text()))
    return found


def test_every_rule_has_advice_written_for_it_or_explicitly_declares_none():
    """The guard that stops this recurring when a rule is added: a rule in
    the source that RULE_ADVICE neither gives advice nor explicitly denies
    it fails here - and a stale entry for a rule that no longer exists
    fails too, so the map stays the map of the codebase."""
    from sentinel.alerts.base import _HEADLINES, _NOTICE_SUBJECTS

    in_source = _rules_in_the_source() | set(_HEADLINES) | set(_NOTICE_SUBJECTS)
    assert len(in_source) > 40, "the enumeration found too few rules to be believed"
    undeclared = sorted(in_source - set(RULE_ADVICE))
    assert undeclared == [], (
        f"rule(s) with no advice decision: {undeclared} - add each to RULE_ADVICE in "
        "sentinel/alerts/base.py as ACCOUNT_ADVICE or NO_ADVICE, with the reason")
    stale = sorted(set(RULE_ADVICE) - in_source)
    assert stale == [], f"RULE_ADVICE names rule(s) that no longer exist: {stale}"
    for rule, kind in RULE_ADVICE.items():
        assert kind in (ACCOUNT_ADVICE, COVERAGE_ADVICE, DATA_GAP_ADVICE, NO_ADVICE), f"{rule}: unknown advice kind {kind!r}"


def test_an_operational_alert_carries_no_customer_runbook():
    """The page of 2026-08-31, subject "[Minas AI Guardian HIGH]
    liveness_failed - MINAS-OPS": a missing backup directory and an unset
    SENTINEL_HEARTBEAT_URL, under "Revoke any API key on this account that
    you do not recognise" and "ask whether the withdrawal can be held".
    Told to a customer it would have them revoking exchange keys and
    phoning exchange support because Minas had not installed a cron job.
    Every rule declared NO_ADVICE renders with no advice on EITHER format,
    at every severity - no block, not a generic one."""
    page = AlertMessage(
        account_label="MINAS-OPS", rule="liveness_failed", severity="HIGH", confidence="operator",
        message="Liveness: 7 mechanism(s) built but not running: Database backup (dead), Dead-man's switch (dead).",
        evidence={"failing": [{"key": "backup", "state": "dead", "evidence": "no backup file exists"}]},
    )
    body = page.render()
    assert "Runbook" not in body and "Revoke" not in body and "withdrawal" not in body.lower()
    assert "Database backup (dead)" in body, "the operator's own content is intact"

    no_advice = sorted(r for r, kind in RULE_ADVICE.items() if kind is NO_ADVICE)
    assert "liveness_failed" in no_advice and "account_onboarded_with_partial_baseline" in no_advice
    for rule in no_advice:
        for severity in ("CRITICAL", "HIGH"):
            alert = AlertMessage(account_label="acct", rule=rule, severity=severity, confidence="high",
                                 message="something happened", evidence={})
            for surface, text in (("operator", alert.render()), ("customer", alert.render_customer())):
                assert "Runbook" not in text and "What to check" not in text, f"{rule} {surface}"
                for step in advice_steps("bitget"):
                    assert step not in text, f"{rule}: the {surface} format carries advice it does not apply to"
                assert monitoring_note() not in text and "What was observed" not in text, f"{rule} {surface}"
    assert runbook(rule="liveness_failed") == "" and advice_steps(rule="liveness_failed") == []


def test_advice_follows_the_original_rule_through_an_escalation():
    """The escalator re-sends to Minas under "ESCALATION-unacknowledged:
    <rule>"; the advice, if any, is the original rule's."""
    assert advice_kind("ESCALATION-unacknowledged: withdrawal_initiated") == "account"
    assert advice_kind("ESCALATION-unacknowledged: account_unreachable") == COVERAGE_ADVICE
    assert advice_kind("a_rule_nobody_registered") is None, "omission never leaks advice"
    assert advice_kind(None) is None


def test_the_incident_page_has_no_advice_section_for_a_rule_without_advice(portal, db, alice):  # noqa: F811
    account_id, token = alice
    incident = _incident(db, account_id)
    with db.cursor() as cur:
        cur.execute("UPDATE alerts SET rule = 'account_onboarded_with_partial_baseline' WHERE id = ?", (incident,))
    status, body = _request(portal, f"/incident?id={incident}", cookie=token)
    assert status == 200
    assert "What to do next" not in body
    assert "Revoke any API key" not in body and monitoring_note() not in body
    assert "Minas cannot do this for you" in body, "the read-only notice is about the product, and stays"


# --- the coverage alert has advice of its own (2026-09-03) ---------------------

def test_account_unreachable_carries_its_own_advice_not_the_withdrawal_steps():
    """A CRITICAL that reaches the customer directly, and the one coverage
    alert they can act on: the key can be checked at the exchange and
    replaced from the portal (#69), or Minas asked. It states that checks
    are failing - never that anything happened to the funds - and carries
    no "revoke", no "withdrawal history", no "if you revoked every key"."""
    assert advice_kind("account_unreachable") == COVERAGE_ADVICE
    alert = AlertMessage(account_label="acct", rule="account_unreachable", severity="CRITICAL",
                         confidence="high", message="Contact lost.", evidence={}, incident_id=7)
    body = alert.render_customer()
    assert "What was observed: Minas AI Guardian's read-only checks on this account have failed" in body
    assert "Nothing in this record says anything happened to your funds." in body
    for step in advice_steps(None, rule="account_unreachable"):  # the email names no exchange
        assert step in body
    assert re.match(r"^(Check|Put|Ask) ", advice_steps(rule="account_unreachable")[0])
    assert "Revoke" not in body and "withdrawal history" not in body and monitoring_note() not in body
    assert "compromis" not in body.lower() and "revoked or expired" not in body
    assert advice_note("account_unreachable") == "" and advice_lead("withdrawal_initiated") == ""
    # The operator hears what the customer was told, in the operator's voice.
    ops = alert.render()
    assert "Customer advised: the read-only checks are failing" in ops
    assert "yourself" not in ops and "Revoke" not in ops


def test_the_data_gap_alert_has_its_own_advice_and_the_email_keeps_the_severity_gate():
    """unseen_address_check_degraded reaches customers (LOW is delivered by
    default). Its steps send them to the exchange's own record; at LOW the
    email carries no steps (the severity gate stands), the incident page
    does."""
    assert advice_kind("unseen_address_check_degraded") == DATA_GAP_ADVICE
    steps = advice_steps("bitget", rule="unseen_address_check_degraded")
    assert steps and "Bitget's own app or website" in steps[0] and "Revoke" not in " ".join(steps)
    alert = AlertMessage(account_label="acct", rule="unseen_address_check_degraded", severity="LOW",
                         confidence="low (data gap)", message="no destination field", evidence={})
    assert "What to check" not in alert.render_customer()
    assert "Customer advised: the withdrawal record had no destination" in alert.render()
    assert advice_kind("account_onboarded_with_partial_baseline") is NO_ADVICE, "an operator page only; left alone"


def test_the_incident_page_shows_the_coverage_advice_with_its_lead_and_no_key_note(portal, db, alice):  # noqa: F811
    account_id, token = alice
    incident = _incident(db, account_id)
    with db.cursor() as cur:
        cur.execute("UPDATE alerts SET rule = 'account_unreachable' WHERE id = ?", (incident,))
    status, body = _request(portal, f"/incident?id={incident}", cookie=token)
    assert status == 200
    import html as _html

    assert "What to do next" in body
    assert _html.escape(advice_lead("account_unreachable")) in body
    for step in advice_steps("bitget", rule="account_unreachable"):
        assert _html.escape(step) in body
    assert "Revoke any API key" not in body and _html.escape(monitoring_note()) not in body


# --- advice is a property of the DIAGNOSIS (2026-09-23) ---------------------------
# A rule can fire with an established cause; the cause replaces the rule's
# advice. The host-level auth failure is the first: the coverage rule's own
# advice - replace the key - would have a customer rotate a working credential
# to one with no allowlist entry either.

from sentinel.alerts.base import CAUSE_ADVICE, CAUSE_KEY, OUR_SIDE_ADVICE, established_cause  # noqa: E402

HOST = "host_auth_failure"


def test_an_established_cause_replaces_the_rules_advice_only_where_the_rule_has_any():
    assert advice_kind("account_unreachable") == COVERAGE_ADVICE
    assert advice_kind("account_unreachable", HOST) == OUR_SIDE_ADVICE
    assert advice_kind("ESCALATION-unacknowledged: account_unreachable", HOST) == OUR_SIDE_ADVICE
    assert advice_kind("liveness_failed", HOST) is None, "a cause never adds advice to a rule that carries none"
    assert advice_kind("account_unreachable", "a_cause_nobody_registered") == COVERAGE_ADVICE, (
        "an unknown cause changes nothing - omission never changes advice")
    assert advice_kind("account_unreachable", None) == COVERAGE_ADVICE


def test_every_registered_cause_is_an_operator_rule_with_a_kind_that_renders():
    """The registry's own guard: a cause is the rule of the page that
    established it, and its kind has steps to show."""
    for cause, kind in CAUSE_ADVICE.items():
        assert cause in RULE_ADVICE, f"{cause}: a cause is named after the page that established it"
        assert advice_steps("bitget", rule="account_unreachable", cause=cause), f"{cause}: {kind} renders no steps"
        assert kind == OUR_SIDE_ADVICE


def test_the_our_side_advice_asks_the_customer_to_do_nothing_to_the_key():
    steps = advice_steps("bitget", rule="account_unreachable", cause=HOST)
    assert len(steps) == 2
    assert steps[0].startswith("Leave your API key exactly as it is: do not replace it and do not revoke it.")
    assert "Wait for our message" in steps[1] and "Nothing else is needed from you" in steps[1]
    text = " ".join(steps).lower()
    for act in ("re-issue", "replace it yourself", "put a new", "revoke any", "sign in"):
        assert act not in text, act
    lead = advice_lead("account_unreachable", HOST)
    assert lead.startswith("What was observed: the exchange is refusing")
    assert "because of a change on our side" in lead and "not because of anything you did" in lead
    assert "Nothing in this record says anything happened to your funds." in lead
    assert advice_note("account_unreachable", HOST) == ""


def test_the_our_side_copy_reads_correctly_in_isolation():
    texts = [*advice_steps("bitget", rule="account_unreachable", cause=HOST),
             advice_lead("account_unreachable", HOST), customer_advised_line("account_unreachable", HOST)]
    for step in texts:
        for sentence in _sentences(step):
            assert sentence[0].isupper() and sentence.endswith("."), sentence
            low = sentence.lower()
            for word in INFERENCES:
                assert word not in low, f"inference {word!r} in: {sentence}"
            for hedge in HEDGES:
                assert hedge not in low, f"hedge {hedge!r} in: {sentence}"


def test_the_customer_email_for_a_coverage_alert_with_a_host_cause_says_do_nothing():
    """The sentence a customer actually receives."""
    alert = AlertMessage(account_label="acct", rule="account_unreachable", severity="HIGH", confidence="high",
                         message="Your account is NOT being watched.", evidence={CAUSE_KEY: HOST})
    body = alert.render_customer()
    assert "Leave your API key exactly as it is" in body
    assert "because of a change on our side" in body
    for wrong in ("Put a new read-only key in place", "Check in Bitget's own app", "Revoke any API key", "re-issue"):
        assert wrong not in body, wrong
    plain = AlertMessage(account_label="acct", rule="account_unreachable", severity="HIGH", confidence="high",
                         message="Your account is NOT being watched.", evidence={})
    assert "Put a new read-only key in place yourself" in plain.render_customer(), "without a cause, the rule's advice"


def test_the_operator_format_says_what_the_customer_was_told_under_the_cause():
    alert = AlertMessage(account_label="acct", rule="account_unreachable", severity="HIGH", confidence="high",
                         message="m", evidence={CAUSE_KEY: HOST})
    body = alert.render()
    assert "Customer advised: the checks are being refused because of a change on Minas's side" in body
    assert "leave their key exactly as it is" in body
    assert "replace it from the portal" not in body


def test_the_cause_is_read_from_evidence_and_nothing_else():
    assert established_cause({CAUSE_KEY: HOST}) == HOST
    assert established_cause({}) is None and established_cause(None) is None and established_cause("x") is None
    assert established_cause({CAUSE_KEY: ""}) is None
    assert AlertMessage(account_label="a", rule="account_unreachable", severity="HIGH", confidence="high",
                        message="m", evidence={CAUSE_KEY: HOST}).cause == HOST


def test_the_incident_page_carries_the_diagnosiss_advice(portal, db, alice):  # noqa: F811
    import html as _html
    import json

    account_id, token = alice
    incident = _incident(db, account_id)
    with db.cursor() as cur:
        cur.execute("UPDATE alerts SET rule = 'account_unreachable', evidence_json = ? WHERE id = ?",
                    (json.dumps({CAUSE_KEY: HOST, "auth_failures": 63}), incident))
    status, body = _request(portal, f"/incident?id={incident}", cookie=token)
    assert status == 200 and "What to do next" in body
    for step in advice_steps("bitget", rule="account_unreachable", cause=HOST):
        assert _html.escape(step) in body or step in body, step
    assert "Put a new read-only key in place" not in body


def test_an_escalation_copy_carries_the_original_cause(db):
    """The escalator rebuilds the customer's copy from the stored row; the
    cause must survive that, or the second sending tells them to replace
    the key the first sending told them to leave alone."""
    import json

    from sentinel.alerts.escalation import Escalator
    from tests.test_escalation_reach import Recording, SpyRouter, _account

    captured = []

    class _Router(SpyRouter):
        def send(self, detection, account_label, emails, phones, *, account_id=None, alert_id=None):
            captured.append(detection)
            super().send(detection, account_label, emails, phones, account_id=account_id, alert_id=alert_id)

    account_id = _account(db, alert_email="primary@example.com")
    from datetime import datetime, timedelta, timezone

    sent_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, confidence, sent_at) "
            "VALUES (?, 'account_unreachable', 'HIGH', 'k', 'Your account is NOT being watched.', ?, 'high', ?)",
            (account_id, json.dumps({CAUSE_KEY: HOST}), sent_at))
    Escalator([Recording()], unacknowledged_minutes=15, contact_router=_Router()).check(db)
    primary = [d for d in captured if d.rule.endswith("account_unreachable")]
    assert primary and established_cause(primary[0].evidence) == HOST
