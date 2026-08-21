#!/usr/bin/env bash
# Does scheduled CI already cover this exact tree with green slow gates? (#93)
#
# Exit codes - the caller's contract (release.sh):
#   0   covered and GREEN: the most recent completed scheduled/dispatched CI
#       run sits on an ancestor of HEAD and no gate-relevant path changed
#       since. The probes and the mutation floor ran on effectively this
#       code and passed - the caller may run the fast gates only.
#   3   covered and RED: same coverage, but that run FAILED. The slow gates
#       failed on code this release contains - the caller must REFUSE, not
#       re-roll the dice on a laptop.
#   1 (and any crash): cannot prove coverage - no gh, unauthenticated gh, a
#       GitHub outage, no covering run, an unknown commit, unparseable
#       output. The caller runs the gates locally. FAIL CLOSED: this
#       predicate may only ever make a release cheaper, never weaker, and
#       unreachable CI means "run them locally", never "assume they passed".
#
# "Covers" is deliberately two conditions, not one. The run's commit being
# an ancestor only proves the gates ran on an ANCESTOR - commits since
# could carry sentinel/ changes the probes and mutants never saw. Coverage
# therefore also requires that no gate-relevant path changed between that
# commit and HEAD. Gate-relevant means what the slow gates actually read:
# the package, the tests, the probe definitions, the gate scripts, and the
# dependency spec.
#
# The MOST RECENT covering run decides: an older green does not outvote a
# newer red on the same code.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

GATE_PATHS=(sentinel/ tests/ scripts/probes/ scripts/mutation-check.sh scripts/mutation-probe.py pyproject.toml)

fallback() {
  echo "scheduled-gates-cover: $1 - the slow gates must run locally" >&2
  exit 1
}

command -v gh >/dev/null 2>&1 || fallback "gh is not installed"
gh auth status >/dev/null 2>&1 || fallback "gh is not authenticated"

RUNS=$(gh run list --workflow CI --limit 50 \
  --json headSha,event,status,conclusion \
  --jq '[.[] | select((.event == "schedule" or .event == "workflow_dispatch") and .status == "completed")]') \
  || fallback "could not list CI runs"

COUNT=$(printf '%s' "$RUNS" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null) \
  || fallback "could not parse the CI run list"
[ -n "$COUNT" ] && [ "$COUNT" -gt 0 ] 2>/dev/null || fallback "no completed scheduled CI run exists"

i=0
while [ "$i" -lt "$COUNT" ]; do
  ROW=$(printf '%s' "$RUNS" | python3 -c "import json,sys; r=json.load(sys.stdin)[$i]; print(r['headSha'], r.get('conclusion') or 'unknown')")
  SHA="${ROW%% *}"
  CONCLUSION="${ROW##* }"
  i=$((i + 1))
  # A commit we do not have is a commit we cannot reason about.
  git cat-file -e "$SHA" 2>/dev/null || continue
  # Gates that ran on an unrelated line prove nothing about this one.
  git merge-base --is-ancestor "$SHA" HEAD 2>/dev/null || continue
  # Ancestor is not enough: gate-relevant code moved since means the probes
  # and mutants never saw this tree.
  CHANGED=$(git diff --name-only "$SHA"..HEAD -- "${GATE_PATHS[@]}")
  [ -z "$CHANGED" ] || continue
  if [ "$CONCLUSION" = "success" ]; then
    echo "scheduled-gates-cover: scheduled run on $SHA covers this tree and is green"
    exit 0
  fi
  echo "scheduled-gates-cover: the most recent scheduled run covering this tree ($SHA) concluded '$CONCLUSION' - the slow gates FAILED on code this release contains" >&2
  exit 3
done

fallback "no completed scheduled run covers this tree"
