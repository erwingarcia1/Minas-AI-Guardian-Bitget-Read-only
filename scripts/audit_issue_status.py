#!/usr/bin/env python3
"""Keep closed issues' `status:` labels honest.

Closing an issue with a `Closes #N` trailer closes it **without touching its
labels**, so issues finished by a commit keep whatever status they were filed
with while issues closed by hand get updated. Three had drifted before anyone
looked: two sat `status: broken` months after being fixed, and one sat
`status: partial` after its blocker was closed.

Run it periodically, or before a review:

    python3 scripts/audit_issue_status.py            # report, changes nothing
    python3 scripts/audit_issue_status.py --fix      # promote the safe ones
    python3 scripts/audit_issue_status.py --check    # exit 1 if anything is stale

## What it will and will not do

It promotes an issue to `status: built` only when **all** of these hold:

  - it is closed, and closed as *completed* rather than *not planned*;
  - at least one commit on `main` references it, so something beyond a click
    says the work landed;
  - every acceptance-criteria checkbox in the body is ticked.

Everything else goes to a human, with the reason printed. In particular it will
**not** tick a checkbox for you. A tick is a claim that a criterion is met, and
nothing here can verify that — a script that ticked boxes to make itself pass
would destroy the one signal that caught the original drift.

Needs the `gh` CLI, authenticated. Read-only unless `--fix` is passed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

STATUS_PREFIX = "status:"
BUILT = "status: built"
UNTICKED = re.compile(r"^\s*-\s*\[ \]", re.MULTILINE)

VERDICT_ORDER = ("review", "promote", "leave", "ok", "skip")


def unticked_count(body: str) -> int:
    return len(UNTICKED.findall(body or ""))


def classify(issue: dict, commit_count: int) -> tuple[str, str]:
    """One of: skip / ok / promote / review / leave, with the reason.

    Deliberately conservative. The failure that matters is promoting something
    that is not built, so every ambiguous case resolves to `review` rather than
    to an action."""
    if issue.get("state") != "closed":
        return "skip", "still open"

    if issue.get("state_reason") == "not_planned":
        # GitHub's "closed without doing it". Calling that built would put a lie
        # in the tracker, and it is the most likely way a script like this does
        # real damage.
        return "leave", "closed as not planned - a status of built would be a lie"

    labels = issue.get("labels") or []
    status = [label for label in labels if label.startswith(STATUS_PREFIX)]
    open_boxes = unticked_count(issue.get("body", ""))

    if open_boxes:
        # Checked before the built shortcut on purpose: an issue already
        # labelled built with unticked criteria is a closed issue claiming a
        # completeness it never recorded, and that is worth surfacing too.
        return "review", f"{open_boxes} acceptance criteria still unticked"

    if not status:
        return "review", "no status label at all"
    if BUILT in status:
        return "ok", "closed and labelled built"
    if commit_count == 0:
        # Closed is a claim; a commit on main is evidence.
        return "review", f"labelled '{status[0]}' but no commit on main references it"

    return "promote", (
        f"closed as completed, {commit_count} commit(s) reference it, all criteria ticked "
        f"- currently '{status[0]}'"
    )


def needs_attention(counts: dict) -> bool:
    """True when something is stale. `--check` exits non-zero on this."""
    return bool(counts.get("promote") or counts.get("review"))


# --- talking to git and gh ----------------------------------------------------

def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout


def fetch_issues() -> list[dict]:
    """Every issue, with the fields the classifier needs.

    Uses the REST endpoint rather than `gh issue list --json` because
    `state_reason` - the completed/not-planned distinction this whole thing
    turns on - is not exposed by the latter in every gh version. Pull requests
    share the issues endpoint and are filtered out."""
    raw = _run(["gh", "api", "--paginate",
                "repos/:owner/:repo/issues?state=all&per_page=100"])
    issues = []
    # --paginate concatenates JSON arrays; parse each one.
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


def commit_count_for(number: int) -> int:
    out = _run(["git", "log", "--oneline", f"--grep=#{number}\\b"])
    return len([line for line in out.splitlines() if line.strip()])


def promote(number: int, current_status: str, dry_run: bool) -> None:
    if dry_run:
        return
    _run(["gh", "issue", "edit", str(number),
          "--remove-label", current_status, "--add-label", BUILT])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--fix", action="store_true",
                        help="Apply the safe promotions (default: report only)")
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 if anything needs promoting or reviewing")
    args = parser.parse_args()

    issues = fetch_issues()
    counts: dict[str, int] = {}
    promoted, flagged = [], []

    for issue in sorted(issues, key=lambda i: i["number"]):
        verdict, reason = classify(issue, commit_count_for(issue["number"]))
        counts[verdict] = counts.get(verdict, 0) + 1
        if verdict == "promote":
            status = next(label for label in issue["labels"] if label.startswith(STATUS_PREFIX))
            promote(issue["number"], status, dry_run=not args.fix)
            promoted.append((issue, reason))
        elif verdict in ("review", "leave"):
            flagged.append((verdict, issue, reason))

    if promoted:
        header = "PROMOTED to status: built" if args.fix else "WOULD PROMOTE (run with --fix)"
        print(f"\n{header}")
        for issue, reason in promoted:
            print(f"  #{issue['number']:<4} {issue['title'][:62]}")
            print(f"        {reason}")

    review = [f for f in flagged if f[0] == "review"]
    if review:
        print("\nNEEDS A HUMAN — not touched")
        for _, issue, reason in review:
            print(f"  #{issue['number']:<4} {issue['title'][:62]}")
            print(f"        {reason}")

    left = [f for f in flagged if f[0] == "leave"]
    if left:
        print("\nDELIBERATELY LEFT")
        for _, issue, reason in left:
            print(f"  #{issue['number']:<4} {issue['title'][:62]} — {reason}")

    print(
        f"\n{counts.get('ok', 0)} already correct, {counts.get('promote', 0)} to promote, "
        f"{counts.get('review', 0)} need a human, {counts.get('leave', 0)} left alone, "
        f"{counts.get('skip', 0)} still open."
    )

    if args.check and needs_attention(counts):
        print("\nIssue status is stale. Run with --fix, or resolve the flagged ones by hand.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
