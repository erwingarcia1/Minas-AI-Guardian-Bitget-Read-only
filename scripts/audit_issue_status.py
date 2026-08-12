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

    Conservative by construction: the failure that matters is asserting
    something is built when it is not, so every ambiguous case returns
    `actionable=False` and goes to a person.
    """
    labels = issue.get("labels") or []
    status = [label for label in labels if label.startswith(STATUS_PREFIX)]
    current = status[0] if status else None
    closed = issue.get("state") == "closed"
    open_boxes = unticked_count(issue.get("body", ""))

    if closed and issue.get("state_reason") == "not_planned":
        return None, "closed as not planned - never work, so no status change follows", False

    if not status:
        return None, "no status label at all - needs one before it can be audited", False

    # Unticked criteria settle it on their own, in both directions.
    if open_boxes:
        if current == BUILT:
            return PARTIAL, (
                f"labelled built with {open_boxes} criteria unticked - either the work is "
                f"incomplete or the issue was never updated, and this cannot tell which"
            ), False
        return None, f"{open_boxes} criteria unticked - consistent with '{current}'", False

    if not closed:
        return None, (
            f"open with every criterion ticked and labelled '{current}' - worth a look: "
            f"finished but unclosed, or criteria that don't capture what's left"
        ), False

    # Closed, completed, every box ticked. Now weigh the evidence.
    notes = []
    if evidence.get("commits", 0) == 0:
        return None, "closed with all criteria ticked but no commit on main references it", False
    if evidence.get("ci") == "failure":
        return None, (
            f"CI failed on {evidence.get('sha', 'the closing commit')} - verify the work "
            f"separately before trusting the closure"
        ), False
    if evidence.get("open_blockers"):
        refs = ", ".join(f"#{n}" for n in evidence["open_blockers"])
        return None, f"names {refs} as a blocker, still open - 'nothing left' cannot be true", False

    if evidence.get("ci") is None:
        notes.append("CI unknown (check runs expire)")
    if not evidence.get("tests"):
        notes.append("no test names this issue")

    if current == BUILT:
        return None, "already correct", False

    suffix = f"; {', '.join(notes)}" if notes else ""
    return BUILT, (
        f"closed as completed, all criteria ticked, {evidence['commits']} commit(s) on main"
        f"{suffix} - currently '{current}'"
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


def ci_for(sha: str) -> str | None:
    """'success', 'failure', or None when GitHub has no check runs for it."""
    out = _run(["gh", "api", f"repos/:owner/:repo/commits/{sha}/check-runs",
                "--jq", "[.check_runs[].conclusion] | join(\",\")"], check=False).strip()
    if not out:
        return None
    conclusions = [c for c in out.split(",") if c]
    if not conclusions:
        return None
    return "success" if all(c == "success" for c in conclusions) else "failure"


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
    if not [x for x in (issue.get("labels") or []) if x.startswith(STATUS_PREFIX)]:
        return False
    return unticked_count(issue.get("body", "")) == 0


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
        if label is None and not reason.startswith(("already correct", f"{unticked_count(issue['body'])} criteria unticked")):
            rows.append((issue, current, "—", reason, False))
            recommendations.append((issue["number"], None, False))
        elif label is not None:
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
