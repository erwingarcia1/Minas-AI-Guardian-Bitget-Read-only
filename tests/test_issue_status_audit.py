"""The one rule `scripts/audit_issue_status.py` enforces.

The status label describes the current state of the feature, and its meanings
(CONTRIBUTING.md) are deliberately loose about completeness:

    built      the core exists and works; criteria may remain
    partial    exists but materially incomplete
    broken     an active defect exists in it right now
    not-built  doesn't exist yet

Which leaves exactly one mechanical rule worth enforcing: **a closed issue must
be `built`.** Anything else on a closed issue is drift — most often a
`Closes #N` trailer, which closes an issue without touching its labels, leaving
a fixed defect still labelled `broken`.

Everything else is deliberately *not* checked:

- **Open issues are ignored entirely.** An open issue already means work
  remains, so built-vs-partial on one carries no decision. Flagging them would
  make `partial` the default and destroy the signal that matters, which is
  `broken` — something is wrong *now*.
- **Acceptance criteria are not consulted.** Under these meanings unticked
  criteria do not contradict `built`, so counting them produced noise rather
  than findings.
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


# --- the rule -----------------------------------------------------------------

def test_a_closed_issue_not_labelled_built_is_drift():
    label, reason, actionable = recommend(_issue(labels=["status: broken"]), _evidence())
    assert label == "status: built"
    assert actionable is True
    assert "broken" in reason


def test_a_closed_partial_is_drift_too():
    label, _, actionable = recommend(_issue(labels=["status: partial"]), _evidence())
    assert label == "status: built"
    assert actionable is True


def test_a_closed_built_issue_is_not_drift():
    label, reason, actionable = recommend(_issue(labels=["status: built"]), _evidence())
    assert label is None
    assert actionable is False
    assert "already correct" in reason


def test_a_closed_built_issue_is_fine_even_if_its_ci_run_failed():
    """CI history gates *promotion*, not an issue already at the target state.
    Three closures landed inside a red window caused by an unrelated defect;
    re-flagging them forever would be noise, and the work was verified
    separately."""
    label, _, actionable = recommend(
        _issue(labels=["status: built"]), _evidence(ci="failure")
    )
    assert label is None
    assert actionable is False


def test_unticked_criteria_on_a_closed_issue_are_not_drift():
    """`built` means the core exists and works; criteria may remain. Counting
    them here produced noise rather than findings."""
    label, _, actionable = recommend(
        _issue(labels=["status: built"], body="- [ ] a\n- [ ] b"), _evidence()
    )
    assert label is None
    assert actionable is False


# --- what it deliberately ignores --------------------------------------------

def test_open_issues_are_ignored_whatever_their_label():
    for current in ("status: built", "status: partial", "status: broken", "status: not-built"):
        label, reason, actionable = recommend(
            _issue(state="open", state_reason=None, labels=[current],
                   body="- [x] a\n- [ ] b"), _evidence()
        )
        assert label is None, f"{current} on an open issue should not be drift"
        assert actionable is False
        assert "open" in reason.lower()


# --- guards on acting ---------------------------------------------------------

def test_a_not_planned_closure_is_never_recommended_built():
    """GitHub's "closed without doing it". Marking that built puts a lie in the
    tracker and is the most likely way a script like this does real damage."""
    label, reason, actionable = recommend(_issue(state_reason="not_planned"), _evidence(commits=3))
    assert label is None
    assert actionable is False
    assert "not planned" in reason.lower()


def test_no_commit_reports_the_drift_but_will_not_act_on_it():
    """Closed is a claim; a commit on main is evidence. The label is still
    wrong, so it is reported - but nothing auto-changes on no evidence."""
    label, reason, actionable = recommend(_issue(), _evidence(commits=0))
    assert label == "status: built"
    assert actionable is False
    assert "commit" in reason.lower()


def test_a_failed_ci_run_blocks_acting_on_a_promotion():
    label, reason, actionable = recommend(_issue(), _evidence(ci="failure", sha="deadbee"))
    assert label == "status: built"
    assert actionable is False
    assert "deadbee" in reason, "name the commit, so it can be checked rather than taken on trust"


def test_an_open_blocker_blocks_acting():
    label, reason, actionable = recommend(_issue(), _evidence(open_blockers=[54]))
    assert label == "status: built"
    assert actionable is False
    assert "#54" in reason


def test_a_closed_issue_with_no_status_label_is_reported():
    label, reason, actionable = recommend(_issue(labels=["epic-02-watching-the-account"]), _evidence())
    assert actionable is False
    assert "no status label" in reason.lower()


def test_unknown_ci_is_noted_but_does_not_block():
    label, reason, actionable = recommend(_issue(), _evidence(ci=None))
    assert label == "status: built"
    assert actionable is True
    assert "ci unknown" in reason.lower()


# --- drift accounting ---------------------------------------------------------

def test_drift_is_any_row_at_all():
    assert audit.has_drift([("#1", "status: built", True)]) is True
    assert audit.has_drift([]) is False
