#!/usr/bin/env python3
"""Audit the `status:` label on every issue against what the code actually is.

The definition it enforces is in CONTRIBUTING.md. In short, the label describes
the **current state of the feature** — not the state it was in when the backlog
was written, and not whether the issue is closed:

    built      every acceptance criterion met, tested, nothing left
    partial    works, but some criteria remain
    broken     a defect exists in it right now
    not-built  doesn't exist yet

Two consequences do most of the work here. A **closed issue labelled broken is
a contradiction** unless the defect genuinely still exists. And **unticked
criteria are incompatible with `built`** in either direction, including on open
issues — which is where fifteen had drifted under the label's older reading
("core is built, may still have unchecked criteria").

Usage (see also scripts/audit-issue-status.sh):

    audit-issue-status.sh              # report; changes nothing; exit 1 on drift
    audit-issue-status.sh --apply      # also apply the recommendations it can evidence

## What it checks, and what it cannot

Per issue it gathers:

  1. whether every acceptance-criteria checkbox is ticked;
  2. whether a commit on `main` references it, and whether CI passed on that
     commit;
  3. whether any test file mentions the issue or its G-number;
  4. whether any issue it names as a blocker is still open.

It **cannot** check whether the code actually does what the criteria claim.
Nothing mechanical can. That judgement stays with a person, and everything above
exists to make it cheap rather than to replace it — so the script recommends,
and applies only recommendations that have evidence behind them.

It will never tick a checkbox. A tick is a claim that a criterion is met; a
script that ticked boxes to make itself pass would destroy the signal it exists
to raise.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

STATUS_PREFIX = "status:"
BUILT = "status: built"
PARTIAL = "status: partial"

UNTICKED = re.compile(r"^\s*-\s*\[ \]", re.MULTILINE)
GNUMBER = re.compile(r"\bG-(\d+)\b")
# Issue references on a line that reads like a dependency, rather than every
# `#N` in the body - most references are context, not blockers.
BLOCKER_LINE = re.compile(
    r"^.*\b(blocked on|blocks this|depends on|prerequisite|waits on|needs .* first)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
ISSUE_REF = re.compile(r"#(\d+)")


def unticked_count(body: str) -> int:
    return len(UNTICKED.findall(body or ""))


def blocker_refs(body: str) -> list[int]:
    """Issue numbers named on a line that reads like a dependency."""
    found: list[int] = []
    for line in BLOCKER_LINE.findall(body or ""):
        found.extend(int(n) for n in ISSUE_REF.findall(line))
    return sorted(set(found))


def recommend(issue: dict, evidence: dict) -> tuple[str | None, str, bool]:
    """(recommended label or None, reason, safe to apply).

    One rule: a **closed** issue must be `status: built`. Anything else on a
    closed issue is drift - usually a `Closes #N` trailer, which closes an issue
    without touching its labels and leaves a fixed defect labelled `broken`.

    Open issues are ignored entirely, and acceptance criteria are not consulted.
    An open issue already means work remains, so built-vs-partial on one carries
    no decision; flagging them would make `partial` the default and drown the
    signal that matters, which is `broken` - something is wrong *now*. And under
    these label meanings unticked criteria do not contradict `built`.

    Drift is reported whether or not it can be acted on. The evidence gates
    below decide only whether `--apply` may touch it, never whether the label is
    wrong.
    """
    labels = issue.get("labels") or []
    status = [label for label in labels if label.startswith(STATUS_PREFIX)]
    current = status[0] if status else None

    if issue.get("state") != "closed":
        return None, "open - no rule applies to an open issue's status", False

    if issue.get("state_reason") == "not_planned":
        return None, "closed as not planned - never work, so it never becomes built", False

    if not status:
        return None, "closed with no status label at all - needs one, but which is a judgement", False

    if current == BUILT:
        return None, "already correct", False

    # Closed and not built: the label is wrong. What follows only decides
    # whether the script may fix it unattended.
    if evidence.get("commits", 0) == 0:
        return BUILT, (
            f"closed but labelled '{current}', and no commit on main references it - "
            f"nothing says the work landed"
        ), False
    if evidence.get("ci") == "failure":
        return BUILT, (
            f"closed but labelled '{current}'; CI failed on {evidence.get('sha', 'its commit')} - "
            f"verify the work before trusting the closure"
        ), False
    if evidence.get("open_blockers"):
        refs = ", ".join(f"#{n}" for n in evidence["open_blockers"])
        return BUILT, (
            f"closed but labelled '{current}', and names {refs} as a blocker, still open"
        ), False

    notes = []
    if evidence.get("ci") is None:
        notes.append("CI unknown (check runs expire)")
    if not evidence.get("tests"):
        notes.append("no test names this issue")
    suffix = f"; {', '.join(notes)}" if notes else ""

    return BUILT, (
        f"closed but labelled '{current}' - {evidence['commits']} commit(s) on main, "
        f"CI clean{suffix}"
    ), True


def has_drift(recommendations: list) -> bool:
    """Any recommendation at all is drift - including the ones a person has to
    resolve, since an unresolved question is exactly what this exists to
    surface."""
    return bool(recommendations)


# --- gathering evidence -------------------------------------------------------

def _run(cmd: list[str], check: bool = True) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        if check:
            raise SystemExit(f"command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
        return ""
    return result.stdout


def fetch_issues() -> list[dict]:
    """Every issue with the fields the classifier needs.

    REST rather than `gh issue list --json`, because `state_reason` - the
    completed/not-planned distinction several rules turn on - is not exposed by
    the latter in every gh version. Pull requests share this endpoint and are
    filtered out."""
    raw = _run(["gh", "api", "--paginate", "repos/:owner/:repo/issues?state=all&per_page=100"])
    issues: list[dict] = []
    for chunk in re.findall(r"\[.*?\]\s*(?=\[|$)", raw, re.DOTALL) or [raw]:
        try:
            issues.extend(json.loads(chunk))
        except json.JSONDecodeError:
            continue
    return [
        {
            "number": i["number"],
            "title": i["title"],
            "state": i["state"],
            "state_reason": i.get("state_reason"),
            "labels": [label["name"] for label in i.get("labels", [])],
            "body": i.get("body") or "",
        }
        for i in issues
        if "pull_request" not in i
    ]


def commits_for(issue: dict) -> list[str]:
    """Commits referencing this issue, closing commit first.

    Two refinements the first version got wrong, both found by running it:

    - It took the most *recent* mention, which for several issues was a later
      docs commit rather than the commit that closed them - so it reported CI
      for the wrong commit entirely. A `Closes/Fixes/Resolves #N` trailer names
      the closing commit, so that is preferred.
    - It matched only `#N`. Work predating the issue-number convention is
      referenced by G-number instead (`G-7`, not `#5`), and looked to the script
      like work that never landed."""
    number = issue["number"]
    patterns = [rf"(Closes|Fixes|Resolves) #{number}\b", rf"#{number}\b"]
    match = GNUMBER.search(issue.get("title", ""))
    if match:
        patterns.append(rf"\bG-{match.group(1)}\b")

    ordered: list[str] = []
    for pattern in patterns:
        out = _run(["git", "log", "--format=%H", "-E", f"--grep={pattern}"], check=False)
        for line in out.splitlines():
            sha = line.strip()
            if sha and sha not in ordered:
                ordered.append(sha)
    return ordered


# A check that went red. Deliberately a list of what *is* red rather than
# "anything that isn't success": a cancelled or timed-out run proved nothing, and
# treating either as clean is how an unverified commit gets promoted unattended.
RED_CONCLUSIONS = frozenset({
    "failure", "cancelled", "timed_out", "action_required", "startup_failure",
})


def ci_for(sha: str) -> str | None:
    """'success', 'failure', or None when nothing conclusive ran.

    Three answers, not two, because `skipped` is neither. This was
    `all(c == "success")`, and the weekly mutation workflow is conditional - so
    GitHub records it as `skipped` on every push and that expression returned
    "failure" for every commit in this repository, permanently. The row it
    gated then read "CI failed on <sha>" about commits whose CI was green, and
    `--apply` could never act on anything. Nothing looked broken; the audit
    printed its table either way.

    A skipped or neutral check did not run, so it is not evidence of failure -
    but neither is it evidence of success, which is why a commit where
    *everything* was skipped returns None rather than "success". The caller
    already reports None as "CI unknown" and hands that row to a person."""
    out = _run(["gh", "api", f"repos/:owner/:repo/commits/{sha}/check-runs",
                "--jq", "[.check_runs[].conclusion] | join(\",\")"], check=False).strip()
    conclusions = [c for c in out.split(",") if c] if out else []
    if not conclusions:
        return None
    if any(c in RED_CONCLUSIONS for c in conclusions):
        return "failure"
    if any(c == "success" for c in conclusions):
        return "success"
    return None


def tests_reference(issue: dict) -> bool:
    """Does any test name this issue or its G-number?

    A grep, and deliberately only informative: plenty of real work is covered by
    tests that never name the issue."""
    needles = [f"#{issue['number']}"]
    match = GNUMBER.search(issue.get("title", ""))
    if match:
        needles.append(f"G-{match.group(1)}")
    return any(_run(["grep", "-rl", needle, "tests/"], check=False).strip() for needle in needles)


def needs_evidence(issue: dict) -> bool:
    """Whether the expensive checks are worth making for this issue.

    Criteria, state and label settle most rows on their own, and each evidence
    pass costs a git log plus a check-runs API call. Gathering it for an open
    issue with unticked boxes - which is decided before evidence is consulted -
    made a full audit take minutes instead of seconds."""
    if issue.get("state") != "closed":
        return False
    if issue.get("state_reason") == "not_planned":
        return False
    status = [x for x in (issue.get("labels") or []) if x.startswith(STATUS_PREFIX)]
    return bool(status) and BUILT not in status


def build_evidence(issue: dict, states: dict[int, str]) -> dict:
    shas = commits_for(issue)
    return {
        "commits": len(shas),
        "sha": shas[0][:7] if shas else None,
        "ci": ci_for(shas[0]) if shas else None,
        "tests": tests_reference(issue),
        "open_blockers": [n for n in blocker_refs(issue["body"]) if states.get(n) == "open"],
    }


def apply_label(number: int, current: str, recommended: str) -> None:
    _run(["gh", "issue", "edit", str(number), "--remove-label", current, "--add-label", recommended])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true",
                        help="Apply the recommendations that have evidence behind them")
    args = parser.parse_args()

    issues = fetch_issues()
    states = {i["number"]: i["state"] for i in issues}
    rows, recommendations = [], []

    for issue in sorted(issues, key=lambda i: i["number"]):
        evidence = build_evidence(issue, states) if needs_evidence(issue) else {}
        label, reason, actionable = recommend(issue, evidence)
        current = next((label_ for label_ in issue["labels"]
                        if label_.startswith(STATUS_PREFIX)), "(none)")
        if label is None:
            # Not drift: open, already correct, or deliberately never built.
            continue
        if label is not None:
            rows.append((issue, current, label, reason, actionable))
            recommendations.append((issue["number"], label, actionable))
            if actionable and args.apply:
                apply_label(issue["number"], current, label)

    if not rows:
        print("No drift. Every issue's status label matches its state.")
        return 0

    verb = "APPLIED" if args.apply else "RECOMMENDED"
    print(f"\n{'ISSUE':<7}{'STATE':<8}{'CURRENT':<19}{'RECOMMENDED':<19}WHY")
    print("─" * 110)
    for issue, current, label, reason, actionable in rows:
        mark = "*" if actionable else " "
        print(f"{mark}#{issue['number']:<5} {issue['state']:<8}{current:<19}{label:<19}{reason}")
    print("─" * 110)
    acted = sum(1 for _, label, actionable in recommendations if actionable and label)
    print(f"* = evidenced, {verb.lower()} with --apply ({acted} of {len(rows)})")
    print("Everything unmarked needs a person: the script cannot check whether the")
    print("code does what the criteria claim, and does not guess.")

    return 1 if has_drift(recommendations) else 0


if __name__ == "__main__":
    sys.exit(main())
