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

from sentinel.alerts.base import AlertMessage, advice_steps, monitoring_note, runbook
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
    for step in advice_steps("bitget") + [monitoring_note()]:
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
    assert "advice_steps(alert[\"exchange\"])" in portal_src
    assert "Revoke any API key" not in portal_src, "the portal must not carry its own copy of the text"


def test_an_unknown_exchange_reads_as_the_exchange():
    steps = advice_steps(None)
    assert "the exchange's own app" in steps[1] and "Contact the exchange's support" in steps[2]


def test_the_customer_email_still_carries_the_steps_for_a_critical():
    alert = AlertMessage(account_label="acct", rule="withdrawal_initiated", severity="CRITICAL",
                         confidence="high", message="A withdrawal was initiated.", evidence={})
    body = alert.render_customer()
    assert advice_steps()[0] in body
