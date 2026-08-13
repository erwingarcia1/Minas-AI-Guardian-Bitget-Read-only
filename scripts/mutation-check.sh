#!/usr/bin/env bash
# Decision-point mutation testing for the detection and alerting paths.
#
#   scripts/mutation-check.sh              # run, print survivors, print the score
#   scripts/mutation-check.sh --check      # also fail if the score drops below the floor
#   scripts/mutation-check.sh --regenerate # force regeneration of mutants/ (see below)
#
# ## Why decision points only
#
# An unrestricted mutmut run over this codebase produced 588 survivors from
# 1,322 mutants - and roughly 60% were cosmetic: mutated log text, SQL keyword
# casing, string literals. Chasing all of them is a project that stalls. This
# keeps only mutations that change what the code *decides*: comparison
# operators, boolean operators, and continue/break/return. 115 mutants instead
# of ~1,000, and every survivor is a behaviour a test could reasonably pin.
#
# Known blind spot: a module with no conditionals produces no decision-point
# mutants at all. `detectors/withdrawal.py` is the case - it is a straight-line
# Detection builder, so this tool reports nothing for it even though its
# unrestricted survival rate is the worst in the codebase (62.7%). Its
# mutations are evidence-key and message changes, which matter because
# `dispatch._dedup_key` reads `evidence["order_id"]`. Cover that module by
# hand - tracked as issue #65. This script will not do it for you, and the
# floor below says nothing about that file either way.
#
# ## mutmut invocation quirks, found the hard way
#
# 1. `source_paths` must be the whole package (`sentinel`), not the subset you
#    want mutated. mutmut copies only `source_paths` into `mutants/` and runs
#    the tests from there, so a narrower path leaves the rest of the package
#    missing and every import fails.
# 2. `pytest_add_cli_args_test_selection` must be NEWLINE-separated. Space
#    separation is passed to pytest as a single argument, and pytest reports it
#    as one unfindable path.
# 3. Mutant *generation* takes ~8 minutes for this package and is independent of
#    the tests. It is cached in `mutants/` and only redone with --regenerate or
#    when a source file is newer.
# 4. `mutmut run <pattern>` filters which mutants RUN, not which are generated.
# 5. Mutant numbering is not stable across regenerations. Never record a
#    survivor by number; record the module and the diff.
#
# ## Survivors that are not gaps
#
# Not every survivor is a missing test. Two kinds show up here and should be
# recognised rather than chased:
#
# - **Equivalent mutants.** `off_market.py`'s `if not reference or reference <=
#   0` has one: `<= 0` mutated to `< 0` survives, because `not reference` is
#   already true for a zero Decimal, so no input distinguishes them. The `<= 0`
#   arm only fires on a *negative* reference price, which no ticker produces.
#   Killing it would need an unreachable input, and a test that manufactures one
#   pins a fiction. Leave it. `quantity_drop.py`'s `if drop <= 0: continue` is
#   the same: with a drop of exactly zero, `unexplained` cannot exceed a
#   non-negative tolerance, so `< 0` reaches the same no-detection by a longer
#   route through a pure function.
# - **Decisions inside log arguments.** `_in_window`'s `record.get("id") or
#   record.get("symbol") or "<unidentified>"` is a chain of `or`s the classifier
#   counts as decision points, but it only chooses wording for a warning line.
#   Worth a test only if the log line is something an operator is expected to
#   act on by name.
#
# The floor is set below the measured score to leave room for exactly these:
# it is a ratchet against decisions *becoming* unpinned, not a target to reach.
#
# ## Guards that mutmut cannot generate
#
# Design guards - "the account scope is inside the UPDATE", "a failed audit
# write must not stop the reload", "attribution is required once operators
# exist" - have no operator to flip, so nothing here covers them. Those are
# probed by hand with `scripts/mutation-probe.py`, which refuses to believe a
# result until it has proved the source actually changed. A hand-written
# mutation that fails to apply passes its tests, which is indistinguishable
# from a test killing it; that tool exists because this one cannot help.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
FLOOR_FILE="scripts/mutation-floor.txt"
CHECK=false; REGENERATE=false
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=true ;;
    --regenerate) REGENERATE=true ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# Prefer the project venv over whatever is on PATH, so this behaves the same
# from a shell, a hook and CI.
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
MUTMUT=mutmut; [ -x .venv/bin/mutmut ] && MUTMUT=.venv/bin/mutmut
command -v "$MUTMUT" >/dev/null 2>&1 || [ -x "$MUTMUT" ] || {
  echo "mutmut not installed: pip install mutmut" >&2; exit 2; }

# The test selection is deliberately narrow: mutation testing runs it once per
# mutant, so the full 70-second suite would make this unusable. These are the
# tests that exercise detectors and alerting.
cat > setup.cfg <<'EOF'
[mutmut]
source_paths=sentinel
pytest_add_cli_args_test_selection=
    tests/test_detectors_phase2.py
    tests/test_detectors_withdrawal.py
    tests/test_detectors_quantity_drop.py
    tests/test_detectors_tamper.py
    tests/test_detectors_unseen_address.py
    tests/test_detectors_new_api_key.py
    tests/test_detector_wiring.py
    tests/test_dispatch.py
    tests/test_dispatch_delivery_failure.py
    tests/test_falsy_sentinels.py
    tests/test_connection_broken.py
    tests/test_outflow_attribution.py
    tests/test_skip_does_not_stop.py
    tests/test_thresholds_and_evidence.py
    tests/test_alert_stack.py
    tests/test_sms_notifications.py
runner=python -m pytest -x -q -p no:cacheprovider
EOF
trap 'rm -f setup.cfg' EXIT

if [ "$REGENERATE" = true ] || [ ! -d mutants ]; then
  echo "Generating mutants (~8 min, cached afterwards)..."
  "$MUTMUT" run 'zzz.no.such.mutant' >/dev/null 2>&1 || true
fi

mapfile -t MUTANTS < <("$PY" scripts/classify_mutants.py sentinel.detectors sentinel.alerts)
echo "Decision-point mutants: ${#MUTANTS[@]}"
[ "${#MUTANTS[@]}" -eq 0 ] && { echo "none selected - has mutants/ been generated?" >&2; exit 2; }

LOG=$(mktemp)
"$MUTMUT" run --max-children 6 "${MUTANTS[@]}" > "$LOG" 2>&1 || true
# `|| true` because grep exits 1 on no match, and no match here means zero
# survivors - a perfect score. Under `set -o pipefail` that aborted the run
# just as it succeeded completely, so this gate would have failed on the one
# result nobody would think to test for.
{ tr '\r' '\n' < "$LOG" | grep -E "^🙁" || true; } | sed 's/^🙁 //' | sort > /tmp/mutation-survivors.txt
KILLED=$(tr '\r' '\n' < "$LOG" | grep -cE "^🎉" || true)
SURVIVED=$(wc -l < /tmp/mutation-survivors.txt)
TOTAL=$((KILLED + SURVIVED))
[ "$TOTAL" -eq 0 ] && { echo "no mutants ran - check $LOG" >&2; exit 2; }

# A selected mutant that never ran is counted nowhere, and a score computed
# over the ones that did looks exactly like a score over all of them - the same
# trap as a hand-written mutation that fails to apply (see
# scripts/mutation-probe.py). A stale name from a regeneration, or a mutmut
# version that quietly skips what it cannot parse, would otherwise raise the
# score by shrinking the denominator.
if [ "$TOTAL" -ne "${#MUTANTS[@]}" ]; then
  echo "selected ${#MUTANTS[@]} mutants but only $TOTAL ran - the score would be" >&2
  echo "computed over a subset and read as if it covered all of them. Check $LOG." >&2
  exit 2
fi
SCORE=$(( KILLED * 100 / TOTAL ))

echo
echo "SURVIVORS (${SURVIVED}):"
sed 's/^/  /' /tmp/mutation-survivors.txt
echo
echo "Killed ${KILLED}/${TOTAL} = ${SCORE}%"

if [ "$CHECK" = true ]; then
  FLOOR=$(cat "$FLOOR_FILE" 2>/dev/null || echo 0)
  echo "Floor: ${FLOOR}%"
  if [ "$SCORE" -lt "$FLOOR" ]; then
    echo "Mutation score dropped below the floor. A decision this suite used to pin, it no longer does." >&2
    exit 1
  fi
  # Ratchet: a better score raises the floor, so the gain can't be given back.
  [ "$SCORE" -gt "$FLOOR" ] && { echo "$SCORE" > "$FLOOR_FILE"; echo "Floor raised to ${SCORE}%."; }
fi
