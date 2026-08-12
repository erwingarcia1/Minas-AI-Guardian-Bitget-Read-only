"""The rules `scripts/audit_issue_status.py` recommends a label by.

The definition it enforces (CONTRIBUTING.md) is about the *current state of the
feature*, not the state it was in when the backlog was written and not whether
the issue is closed:

    built      every acceptance criterion met, tested, nothing left
    partial    works, but some criteria remain
    broken     a defect exists in it right now
    not-built  doesn't exist yet

Two consequences drive almost every rule below. A **closed issue labelled
broken is a contradiction** unless the defect genuinely still exists. And
**unticked criteria are incompatible with `built`** in either direction —
including on open issues, which is where the old label description ("core is
built, may still have unchecked criteria") had let fifteen of them drift.

The script recommends. It applies only the recommendations that have evidence
behind them, and only under `--apply`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "audit_issue_status", Path(__file__).resolve().parent.parent / "scripts" / "audit_issue_status.py"
)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)

recommend = audit.recommend


def _issue(number=1, state="closed", state_reason="completed", labels=("status: broken",),
           body="- [x] done", title="G-1 · A thing"):
    return {"number": number, "title": title, "state": state, "state_reason": state_reason,
            "labels": list(labels), "body": body}


def _evidence(commits=1, ci="success", tests=True, open_blockers=(), sha="abc1234"):
    return {"commits": commits, "ci": ci, "tests": tests, "sha": sha,
            "open_blockers": list(open_blockers)}


# --- the only case it may act on ---------------------------------------------

def test_a_finished_closed_issue_is_recommended_built():
    label, reason, actionable = recommend(_issue(), _evidence())
    assert label == "status: built"
    assert actionable is True
    assert "broken" in reason  # says what it is moving away from


def test_it_is_a_no_op_when_the_label_is_already_right():
    label, _, actionable = recommend(_issue(labels=["status: built"]), _evidence())
    assert label is None
    assert actionable is False


# --- unticked criteria are incompatible with built, in both directions -------

def test_a_closed_issue_with_unticked_criteria_is_not_built():
    """Either the work is incomplete or nobody updated the issue. The script
    cannot tell which, so it recommends `partial` and refuses to act."""
    label, reason, actionable = recommend(
        _issue(labels=["status: built"], body="- [x] a\n- [ ] b\n- [ ] c"), _evidence()
    )
    assert label == "status: partial"
    assert actionable is False
    assert "2" in reason


def test_an_open_issue_labelled_built_with_unticked_criteria_is_partial():
    """The direction the old label description hid. `built` means every
    criterion met; an open issue with criteria outstanding is `partial` by
    definition, regardless of how much of it works."""
    label, reason, actionable = recommend(
        _issue(state="open", state_reason=None, labels=["status: built"],
               body="- [x] a\n- [ ] b"), _evidence()
    )
    assert label == "status: partial"
    assert actionable is False
    assert "1" in reason


def test_an_open_issue_with_every_criterion_ticked_is_flagged_not_promoted():
    """All criteria met but still open is a question for a human - it may be
    finished and unclosed, or the criteria may be incomplete."""
    label, reason, actionable = recommend(
        _issue(state="open", state_reason=None, labels=["status: partial"]), _evidence()
    )
    assert actionable is False
    assert "open" in reason.lower()


# --- a closed issue labelled broken is a contradiction -----------------------

def test_a_closed_broken_issue_with_no_commit_is_flagged_not_promoted():
    """Closed is a claim; a commit on main is evidence. Without one there is
    nothing to say the defect was actually fixed."""
    label, reason, actionable = recommend(_issue(), _evidence(commits=0))
    assert actionable is False
    assert "commit" in reason.lower()


def test_a_failing_ci_run_blocks_the_recommendation():
    """A commit landed does not mean it landed green. The ten-hour red-CI
    window is exactly the state this must not rubber-stamp."""
    label, reason, actionable = recommend(_issue(), _evidence(ci="failure", sha="deadbee"))
    assert actionable is False
    assert "ci" in reason.lower()
    assert "deadbee" in reason, "name the commit, so it can be checked rather than taken on trust"


def test_unknown_ci_is_reported_but_does_not_block():
    """Check runs expire. Absence of a result is not evidence of failure, so it
    is surfaced rather than treated as one."""
    label, reason, actionable = recommend(_issue(), _evidence(ci=None))
    assert label == "status: built"
    assert actionable is True
    assert "ci unknown" in reason.lower()


def test_no_test_referencing_the_issue_is_reported_but_does_not_block():
    """Test discovery is a grep for the issue and G-number in tests/. Plenty of
    real work is covered by tests that never name it, so this informs rather
    than decides."""
    label, reason, actionable = recommend(_issue(), _evidence(tests=False))
    assert label == "status: built"
    assert actionable is True
    assert "no test" in reason.lower()


def test_an_open_blocker_blocks_the_recommendation():
    """"Nothing left" cannot be true while something this issue depends on is
    still open."""
    label, reason, actionable = recommend(_issue(), _evidence(open_blockers=[54]))
    assert actionable is False
    assert "#54" in reason


# --- closures that were never work -------------------------------------------

def test_a_not_planned_closure_is_never_recommended_built():
    """GitHub's "closed without doing it". Marking that built puts a lie in the
    tracker and is the most likely way a script like this does real damage."""
    label, reason, actionable = recommend(_issue(state_reason="not_planned"), _evidence(commits=3))
    assert label is None
    assert actionable is False
    assert "not planned" in reason.lower()


def test_a_closed_issue_with_no_status_label_is_flagged():
    label, reason, actionable = recommend(_issue(labels=["epic-02-watching-the-account"]), _evidence())
    assert actionable is False
    assert "no status label" in reason.lower()


# --- drift accounting ---------------------------------------------------------

def test_drift_is_any_recommendation_at_all():
    assert audit.has_drift([("#1", "status: built", True)]) is True
    assert audit.has_drift([("#1", "status: partial", False)]) is True
    assert audit.has_drift([]) is False
