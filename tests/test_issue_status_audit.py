"""The rules `scripts/audit_issue_status.py` promotes an issue by.

The script exists because closing an issue with a `Closes #N` commit trailer
closes it without touching its labels — so issues finished by a commit kept
their `status: broken` label while issues closed by hand got updated. Three had
drifted before anyone looked.

The whole risk is in what it is willing to promote. A script that mapped
"closed" to `status: built` would happily mark a wontfix as built, and would
mark an issue built on the strength of somebody having closed it in a hurry. So
the classifier is conservative on purpose: it promotes only what has evidence,
and everything else goes to a human.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "audit_issue_status", Path(__file__).resolve().parent.parent / "scripts" / "audit_issue_status.py"
)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)

classify = audit.classify


def _issue(number=1, state="closed", state_reason="completed", labels=("status: broken",), body=""):
    return {"number": number, "state": state, "state_reason": state_reason,
            "labels": list(labels), "body": body}


# --- the one case it may act on ----------------------------------------------

def test_a_finished_issue_is_promoted(): 
    verdict, _ = classify(_issue(body="- [x] done\n- [x] also done"), commit_count=1)
    assert verdict == "promote"


def test_an_issue_with_no_criteria_at_all_is_promoted():
    """Plenty of issues are prose with no checklist. Absent criteria are not
    unmet criteria."""
    verdict, _ = classify(_issue(body="Just a description."), commit_count=1)
    assert verdict == "promote"


# --- everything it must refuse to act on -------------------------------------

def test_an_open_issue_is_skipped():
    verdict, _ = classify(_issue(state="open"), commit_count=1)
    assert verdict == "skip"


def test_a_wontfix_is_never_promoted():
    """`not_planned` is GitHub's "closed without doing it". Calling that built
    would put a lie in the tracker, and it is the single most likely way a
    script like this does damage."""
    verdict, reason = classify(_issue(state_reason="not_planned"), commit_count=3)
    assert verdict == "leave"
    assert "not planned" in reason.lower()


def test_an_issue_with_no_commit_referencing_it_goes_to_review():
    """Closed is a claim; a commit on main is evidence. Without one there is
    nothing to say the work actually landed."""
    verdict, reason = classify(_issue(body="- [x] done"), commit_count=0)
    assert verdict == "review"
    assert "commit" in reason.lower()


def test_unticked_criteria_go_to_review_rather_than_being_papered_over():
    """The important refusal. Unticked boxes on a closed issue mean one of two
    things - the work is incomplete, or nobody updated the issue - and the
    script cannot tell which. Promoting anyway would hide exactly the signal
    that caught the real drift."""
    verdict, reason = classify(
        _issue(body="- [x] done\n- [ ] not done\n- [ ] also not done"), commit_count=1
    )
    assert verdict == "review"
    assert "2" in reason


def test_an_issue_with_no_status_label_goes_to_review():
    verdict, reason = classify(_issue(labels=["epic-02-watching-the-account"]), commit_count=1)
    assert verdict == "review"
    assert "status" in reason.lower()


def test_an_already_built_issue_is_left_alone():
    verdict, _ = classify(_issue(labels=["status: built"], body="- [x] done"), commit_count=1)
    assert verdict == "ok"


def test_an_already_built_issue_with_unticked_criteria_is_still_flagged():
    """Being labelled built does not make unticked criteria fine - that
    combination is a closed issue claiming completeness it has not recorded."""
    verdict, reason = classify(
        _issue(labels=["status: built"], body="- [ ] not done"), commit_count=1
    )
    assert verdict == "review"
    assert "1" in reason


# --- the summary the caller acts on ------------------------------------------

def test_needs_attention_counts_only_actionable_verdicts():
    assert audit.needs_attention({"promote": 1, "review": 2, "ok": 9, "skip": 4, "leave": 1}) is True
    assert audit.needs_attention({"ok": 9, "skip": 4, "leave": 1}) is False
