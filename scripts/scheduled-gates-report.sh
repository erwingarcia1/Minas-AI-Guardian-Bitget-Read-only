#!/usr/bin/env bash
# Classify the scheduled gates' logs and file issues whose TITLES distinguish
# a real regression from a gate that produced no result (#86).
#
# The defect this replaces: CI filed "Mutation score dropped below the floor"
# on ANY non-zero exit of the mutation step - including "no mutants ran",
# which is the gate being broken, not a test losing its grip. That title sent
# someone hunting a lost test when the thing to fix was the gate. The same
# rule the local health check enforces ("a gate it could not run is skipped,
# never pass") applies here with one addition: never "regression" either.
#
# Classification is LOG-DRIVEN, keyed on strings the gate scripts actually
# emit (tests/test_scheduled_gates.py holds the two vocabularies together in
# both directions):
#
#   design-guard probes (scripts/mutation-probe.py):
#     regression  - "FAIL  survived/killed" lines: a probe landed, ran, and
#                   got the wrong verdict. A test lost its grip (or an
#                   equivalence claim stopped being true).
#     no result   - "DID NOT LAND" (the patch missed), "FAIL  timeout" (the
#                   question went unanswered), a missing summary line (the
#                   run never stated its population), or a summary whose
#                   population differs from design-guards.json (probes were
#                   silently not attempted).
#   mutation floor (scripts/mutation-check.sh --check):
#     regression  - "dropped below the floor": mutants ran, the score is
#                   real, and it is lower than the recorded floor.
#     regression  - "survivor(s) not in scripts/mutation-known-survivors.txt"
#                   (scripts/mutation_survivors.py): mutants ran, the score
#                   is real, and a survivor is one the ledger does not
#                   accept - a decision no test pins, whatever the score.
#                   Until 2026-10-22 this fell through to "no result", and
#                   #110 was titled "produced no result" for a run that had
#                   produced one.
#     no result   - everything else non-green: "no mutants ran", fewer ran
#                   than selected, a missing floor file, a malformed or
#                   unreadable ledger, a killed step. Also a "successful"
#                   step whose log carries no "Killed K/T" line - a pass
#                   that cannot show its score is not a pass.
#
# One run can file BOTH kinds for one gate (three probes survived while one
# patch missed): each kind gets its own truthfully-titled issue rather than
# one issue with a half-true title.
#
# Inputs (env):
#   PROBES_LOG / MUTATION_LOG   log paths (default probes.log / mutation.log)
#   PROBES_OUTCOME / MUTATION_OUTCOME   the step outcomes ("success"/...)
#   RUN_URL, COMMIT             provenance for the issue body
#   DRY_RUN=1                   print "WOULD FILE: <title>" instead of
#                               touching GitHub - how the tests run this
#   GH_TOKEN                    needed only when actually filing
#
# Exit: 0 all green; 1 when anything was (or would be) filed.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

PROBES_LOG="${PROBES_LOG:-probes.log}"
MUTATION_LOG="${MUTATION_LOG:-mutation.log}"
PROBES_OUTCOME="${PROBES_OUTCOME:-unknown}"
MUTATION_OUTCOME="${MUTATION_OUTCOME:-unknown}"
RUN_URL="${RUN_URL:-(no run url)}"
COMMIT="${COMMIT:-(no commit)}"
DRY_RUN="${DRY_RUN:-}"

PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python

file_issue() {  # title, labels, body
  local title="$1" labels="$2" body="$3"
  if [ -n "$DRY_RUN" ]; then
    printf 'WOULD FILE: %s\n' "$title"
    return 0
  fi
  # Dedup by exact open title, not by label: the two "no result" issues share
  # a label, and commenting the probes' failure onto the mutation gate's
  # issue would be its own small mislabeling.
  local existing
  existing=$(gh issue list --state open --json number,title \
    --jq ".[] | select(.title == \"$title\") | .number" | head -n1)
  if [ -n "$existing" ]; then
    gh issue comment "$existing" --body "$body"
  else
    gh issue create --title "$title" --label "$labels" --body "$body"
  fi
}

provenance() {
  printf 'Run: %s\nCommit: %s\n' "$RUN_URL" "$COMMIT"
}

PROBLEMS=0
SUMMARY=""

note() {  # gate, verdict
  SUMMARY="${SUMMARY}| $1 | $2 |"$'\n'
}

# --- design-guard probes ------------------------------------------------------

EXPECTED_PROBES=$("$PY" -c "import json; print(len(json.load(open('scripts/probes/design-guards.json'))))")

probe_regressions=""
probe_noresult=""
if [ ! -f "$PROBES_LOG" ]; then
  probe_noresult="The probes step left no log at all - it was killed before it started, or never ran."
else
  probe_regressions=$(grep -E '^  FAIL +(survived|killed) ' "$PROBES_LOG" || true)
  notland=$(grep -E 'DID NOT LAND' "$PROBES_LOG" || true)
  timeouts=$(grep -E '^  FAIL +timeout' "$PROBES_LOG" || true)
  summary_line=$(grep -E '^[0-9]+/[0-9]+ probes as expected$' "$PROBES_LOG" | tail -n1 || true)
  [ -n "$notland" ] && probe_noresult="Patches that did not land (the probe never became a question):"$'\n'"$notland"
  [ -n "$timeouts" ] && probe_noresult="${probe_noresult:+$probe_noresult$'\n\n'}Probes whose test run timed out (the question went unanswered):"$'\n'"$timeouts"
  if [ -z "$summary_line" ]; then
    probe_noresult="${probe_noresult:+$probe_noresult$'\n\n'}No summary line - the run never stated its population, which reads identically to a truncated run."
  else
    total="${summary_line#*/}"
    total="${total%% *}"
    if [ "$total" != "$EXPECTED_PROBES" ]; then
      probe_noresult="${probe_noresult:+$probe_noresult$'\n\n'}The run reports a population of ${total} probes; scripts/probes/design-guards.json defines ${EXPECTED_PROBES}. Probes were silently not attempted."
    fi
  fi
  if [ -z "$probe_regressions" ] && [ -z "$probe_noresult" ] && [ "$PROBES_OUTCOME" != "success" ]; then
    probe_noresult="The probes step reported '$PROBES_OUTCOME' but its log carries no recognisable failure - the gate is broken in a new way. Log tail:"$'\n'"$(tail -n 15 "$PROBES_LOG")"
  fi
fi

if [ -n "$probe_regressions" ]; then
  PROBLEMS=1
  note "design-guard probes" "REGRESSION"
  file_issue "Design-guard probe no longer killed by any test" \
    "design-guards,epic-10-reliability,priority: p2" \
    "$(printf '%s\n\n```\n%s\n```\n\nA probe landed, ran, and got the wrong verdict: a design guard some test used to pin, no test pins any more (or an `expect: survived` equivalence claim has stopped being true). Reproduce locally: `python scripts/mutation-probe.py scripts/probes/design-guards.json`.\n\n%s' \
      "The weekly scheduled run found probe(s) with the wrong verdict:" \
      "$probe_regressions" "$(provenance)")"
fi
if [ -n "$probe_noresult" ]; then
  PROBLEMS=1
  note "design-guard probes" "NO RESULT"
  file_issue "Design-guard probes produced no result" \
    "gate-no-result,epic-10-reliability,priority: p2" \
    "$(printf '%s\n\n%s\n\n**Fix the gate - do not hunt tests.** A probe that could not run answered nothing; this is not a regression report.\n\n%s' \
      "The weekly scheduled probe run could not fully answer its question:" \
      "$probe_noresult" "$(provenance)")"
fi
[ -z "$probe_regressions" ] && [ -z "$probe_noresult" ] && note "design-guard probes" "pass (${summary_line:-?})"

# --- mutation floor -----------------------------------------------------------

mut_regression=""
mut_unledgered=""
mut_noresult=""
if [ ! -f "$MUTATION_LOG" ]; then
  mut_noresult="The mutation step left no log at all - it was killed before it started, or never ran."
elif grep -q "dropped below the floor" "$MUTATION_LOG"; then
  mut_regression=$(grep -E '^(Killed|Floor)' "$MUTATION_LOG" || true)
elif grep -qE '^[0-9]+ survivor\(s\) not in scripts/mutation-known-survivors\.txt' "$MUTATION_LOG"; then
  # The score line, then each NEW survivor with its diff (the indented lines
  # under it). Accepted rows carry indented lines too, so track which kind
  # of row the indented lines belong to.
  mut_unledgered=$(grep -E '^Killed [0-9]+/[0-9]+' "$MUTATION_LOG" || true)$'\n\n'$(awk '
    /^  NEW /      { keep = 1; print; next }
    /^             / { if (keep) print; next }
                   { keep = 0 }' "$MUTATION_LOG")
elif [ "$MUTATION_OUTCOME" = "success" ] && grep -qE '^Killed [0-9]+/[0-9]+' "$MUTATION_LOG" \
    && grep -qE '^Floor' "$MUTATION_LOG"; then
  :  # a real pass: it ran, scored, and can show both numbers
else
  mut_noresult="$(tail -n 15 "$MUTATION_LOG")"
fi

if [ -n "$mut_regression" ]; then
  PROBLEMS=1
  note "mutation floor" "REGRESSION"
  file_issue "Mutation score dropped below the floor" \
    "mutation-floor,epic-10-reliability,priority: p2" \
    "$(printf '%s\n\n```\n%s\n```\n\nA decision this suite used to pin, it no longer does - some change removed a test'"'"'s grip on a comparison, a boolean, or a continue/break without any test going red. Reproduce locally: `scripts/mutation-check.sh` (the survivor list prints in full).\n\n%s' \
      "The weekly decision-point mutation run scored below the floor in \`scripts/mutation-floor.txt\`:" \
      "$mut_regression" "$(provenance)")"
fi
if [ -n "$mut_unledgered" ]; then
  PROBLEMS=1
  note "mutation floor" "REGRESSION (unledgered survivor)"
  file_issue "Mutation gate found a survivor no test pins" \
    "mutation-floor,epic-10-reliability,priority: p2" \
    "$(printf '%s\n\n```\n%s\n```\n\nEach NEW row is a mutant that survived and that `scripts/mutation-known-survivors.txt` does not accept: a decision no selected test pins - a test lost its grip, or new code arrived unpinned. The score can still clear the floor; the ledger fails the gate on its own. Kill each with a test, or, if it is a proven equivalent, add its fingerprint and the argument to the ledger. Reproduce locally: `scripts/mutation-check.sh --check`.\n\n%s' \
      "The weekly mutation run scored, and found survivor(s) the ledger does not accept:" \
      "$mut_unledgered" "$(provenance)")"
fi
if [ -n "$mut_noresult" ]; then
  PROBLEMS=1
  note "mutation floor" "NO RESULT"
  file_issue "Mutation gate produced no result" \
    "gate-no-result,epic-10-reliability,priority: p2" \
    "$(printf '%s\n\n```\n%s\n```\n\n**Fix the gate - do not hunt tests.** "No mutants ran", fewer-than-selected, a missing floor file, or a killed step all mean the question went unanswered - which is neither a pass nor a regression. scripts/mutation-check.sh'"'"'s header documents the usual causes.\n\n%s' \
      "The weekly mutation gate exited without a scored result. Log tail:" \
      "$mut_noresult" "$(provenance)")"
fi
[ -z "$mut_regression" ] && [ -z "$mut_unledgered" ] && [ -z "$mut_noresult" ] && note "mutation floor" "pass ($(grep -E '^Killed' "$MUTATION_LOG" 2>/dev/null | tail -n1 || echo '?'))"

# The survivor ledger's size, every week, whatever the verdict. The ledger
# turns anonymous slack into named slack; this row is what resists it
# growing - a count that only ever goes up is noticed here, a file is not.
ledger_line=$(grep -E '^  Survivor ledger: [0-9]+ accepted' "$MUTATION_LOG" 2>/dev/null | tail -n1 || true)
if [ -n "$ledger_line" ]; then
  note "survivor ledger" "${ledger_line#*ledger: }"
else
  note "survivor ledger" "? (the gate did not state it)"
fi

# --- summary ------------------------------------------------------------------

TABLE=$(printf '| gate | verdict |\n|---|---|\n%s' "$SUMMARY")
printf '%s\n' "$TABLE"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  printf '## Scheduled gates\n\n%s\n' "$TABLE" >> "$GITHUB_STEP_SUMMARY"
fi

exit "$PROBLEMS"
