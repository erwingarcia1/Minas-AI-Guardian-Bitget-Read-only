#!/usr/bin/env bash
# Every gate this project has, in one command, with a summary table.
#
#   scripts/health-check.sh          # all seven gates (~20 min, mutation dominates)
#   scripts/health-check.sh --fast   # skips the two multi-minute gates (~3 min)
#
# The gates already existed; what did not existed was a way to ask "is
# everything okay" and get one answer. Seven commands, three of them with
# arguments nobody remembers, means in practice people run two of them and
# infer the rest - and an ungated gate is indistinguishable from a passing one,
# which is the failure mode this repo keeps rediscovering.
#
# ## What it will not do
#
# Report a gate it did not run as anything other than skipped, and never in
# green. A gate whose tool is missing (no mutmut, no authenticated gh) is
# `skipped`, the run exits 2, and the summary says so in the notes column. Only
# `--fast`, where the operator chose the skip, exits 0 with gates unrun.
#
# Guess at a floor. The coverage floor is read out of the CI workflow and the
# mutation floor out of scripts/mutation-floor.txt, so this cannot drift into
# checking something laxer than CI does while still printing "pass".
#
# Believe stale coverage data. `.coverage` from an earlier run sits in the
# working tree and `coverage report` will happily read it, so the floor gate
# refuses to run unless pytest just rewrote the file.
#
# ## Side effects worth knowing about
#
# `mutation-check.sh --check` ratchets: a score well above the floor rewrites
# scripts/mutation-floor.txt. That is a tracked file, so a clean health check
# can leave you with a change to commit. It also generates `mutants/` on first
# run (~8 min, cached afterwards).
#
# Every run leaves its logs in .health-check-logs/<utc-stamp>-<sha>/, including
# runs that passed - see the note by LOGDIR. They are pruned after 14 days.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

FAST=false
for arg in "$@"; do
  case "$arg" in
    --fast) FAST=true ;;
    -h|--help) sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown argument: $arg (try --fast, --help)" >&2; exit 2 ;;
  esac
done

# Prefer the project venv over whatever is on PATH, so this behaves the same
# from a shell, a hook and CI - the same rule the other scripts here follow.
PY=python3;        [ -x .venv/bin/python ]    && PY=.venv/bin/python
RUFF=ruff;         [ -x .venv/bin/ruff ]      && RUFF=.venv/bin/ruff
COVERAGE=coverage; [ -x .venv/bin/coverage ]  && COVERAGE=.venv/bin/coverage
PIP_AUDIT=pip-audit; [ -x .venv/bin/pip-audit ] && PIP_AUDIT=.venv/bin/pip-audit

# Both floors come from the file that already owns them. A number retyped here
# would pass while CI failed, which is worse than having no gate: it is a gate
# that lies.
COV_FLOOR=$(grep -oE -- '--cov-fail-under=[0-9]+' .github/workflows/ci.yml | head -1 | cut -d= -f2 || true)
[ -n "$COV_FLOOR" ] || {
  echo "could not read --cov-fail-under from .github/workflows/ci.yml." >&2
  echo "Refusing to guess a coverage floor - fix the grep or the workflow." >&2
  exit 2; }
MUT_FLOOR=$(cat scripts/mutation-floor.txt 2>/dev/null || echo "?")

# Kept on success, not deleted. A green run is precisely the result worth being
# able to audit later: an 8-second mutation "pass" is indistinguishable from a
# hollow one until you read what it actually did, and this gate exists to catch
# hollow greens. Throwing the evidence away on success meant the only way to
# re-check a pass was to run it again for twenty minutes.
LOGROOT=.health-check-logs
LOGDIR="$LOGROOT/$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"
mkdir -p "$LOGDIR"
# Pruned by age rather than by count, so a burst of runs in one afternoon does
# not evict the run from last week you actually wanted. Fourteen days is long
# enough to get back to whichever run last said yes, short enough that this
# never becomes something anyone has to clean up by hand.
find "$LOGROOT" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} + 2>/dev/null || true

MARKER="$LOGDIR/started"
NAMES=(); RESULTS=(); TIMES=(); NOTES=(); LOGS=()
FAILED=0; UNAVAILABLE=0; LAST_STATUS=0

# Runs one gate, captures its output, records the outcome. Never aborts the
# run: the point of a summary table is knowing all seven answers, not the first
# one that went wrong.
gate() {
  local name="$1" log="$LOGDIR/${2}.log"; shift 2
  local start=$SECONDS status=0
  printf '  running %-24s' "$name"
  "$@" > "$log" 2>&1 || status=$?
  LAST_STATUS=$status
  local elapsed=$((SECONDS - start))
  NAMES+=("$name"); TIMES+=("${elapsed}s"); LOGS+=("$log"); NOTES+=("")
  if [ "$status" -eq 0 ]; then RESULTS+=("pass"); printf 'pass  %ds\n' "$elapsed"
  else RESULTS+=("FAIL"); FAILED=$((FAILED + 1)); printf 'FAIL  %ds  (%s)\n' "$elapsed" "$log"; fi
  return 0
}

# A gate that could not run. `requested` means the operator asked for the skip
# and it does not affect the exit status; anything else means the environment
# could not answer the question, and an unanswered question is not a pass.
skip() {
  local name="$1" why="$2" reason="${3:-unavailable}"
  NAMES+=("$name"); RESULTS+=("skipped"); TIMES+=("-"); LOGS+=(""); NOTES+=("$why")
  [ "$reason" = "requested" ] || UNAVAILABLE=$((UNAVAILABLE + 1))
  printf '  skipped %-24s%s\n' "$name" "$why"
}

echo "Gates for $(git rev-parse --short HEAD 2>/dev/null || echo 'working tree'); logs in $LOGDIR"
echo

# Ordered cheapest-first, so a typo fails in a second instead of after the
# suite. The table below prints in the same order.
gate "ruff" ruff "$RUFF" check sentinel tests

# One pytest run answers two gates. Splitting them matters: with
# --cov-fail-under baked in, a coverage shortfall and a failing test are the
# same red, and the first question anyone asks is which one it was.
touch "$MARKER"
gate "tests" tests "$PY" -m pytest -q --cov=sentinel --cov-report=term-missing

if [ -f .coverage ] && [ .coverage -nt "$MARKER" ]; then
  gate "coverage floor (${COV_FLOOR}%)" coverage "$COVERAGE" report "--fail-under=$COV_FLOOR"
else
  # There is a `.coverage` in this repo from previous runs. Reporting off it
  # would print a number with nothing behind it.
  skip "coverage floor (${COV_FLOOR}%)" "pytest wrote no fresh .coverage"
fi

gate "pip-audit" pip-audit "$PIP_AUDIT" .

if ! command -v gh >/dev/null 2>&1; then
  skip "issue-status audit" "gh not installed"
elif ! gh auth status >/dev/null 2>&1; then
  skip "issue-status audit" "gh not authenticated"
else
  gate "issue-status audit" issue-status scripts/audit-issue-status.sh
fi

if [ "$FAST" = true ]; then
  skip "design-guard probes" "--fast" requested
  skip "mutation floor (${MUT_FLOOR}%)" "--fast" requested
else
  gate "design-guard probes" design-guards "$PY" scripts/mutation-probe.py scripts/probes/design-guards.json

  # The probes patch the working tree, so mutation-probe.py refuses to start
  # while another run holds its lock rather than interleaving with it. That is
  # a question we could not ask, not an answer - so it is a skip, and it still
  # costs the run its green (UNAVAILABLE below), because the guards went
  # unchecked either way.
  if [ "${RESULTS[-1]}" = "FAIL" ] && [ "$LAST_STATUS" -eq 2 ] \
     && grep -q "holds .mutation-probe.lock" "$LOGDIR/design-guards.log" 2>/dev/null; then
    RESULTS[-1]="skipped"; TIMES[-1]="-"
    NOTES[-1]="another probe run holds the lock"
    FAILED=$((FAILED - 1)); UNAVAILABLE=$((UNAVAILABLE + 1))
  fi

  if ! { command -v mutmut >/dev/null 2>&1 || [ -x .venv/bin/mutmut ]; }; then
    skip "mutation floor (${MUT_FLOOR}%)" "mutmut not installed"
  else
    gate "mutation floor (${MUT_FLOOR}%)" mutation scripts/mutation-check.sh --check

    # mutmut runs the tests from its cached `mutants/` copy of the package, and
    # regenerates that copy only when it is missing. If source has moved since
    # the copy was made, the tests run against a stale package and fail for
    # reasons that have nothing to do with any mutant - which surfaces as
    # "no mutants ran", a message that sends you looking in the wrong place.
    # Seen for real: a `mutants/schema.sql` predating a table the suite needs.
    #
    # Anchor on a file mutmut writes when it *generates*, not on `mutants/`
    # itself: a run drops mutmut-stats.json and a .pytest_cache in there, so
    # the directory's mtime is refreshed by the very run whose staleness we
    # are asking about, and the note could never fire on the case it exists
    # for. mutants/sentinel/__init__.py is written at generation and left
    # alone afterwards.
    STAMP=mutants/sentinel/__init__.py
    if [ "${RESULTS[-1]}" = "FAIL" ]; then
      if [ -f "$STAMP" ] && [ -n "$(find sentinel -name '*.py' -newer "$STAMP" -print -quit)" ]; then
        NOTES[-1]="mutants/ is older than sentinel/ — rerun with --regenerate"
      elif grep -q "no mutants ran" "$LOGDIR/mutation.log" 2>/dev/null; then
        # Almost always the baseline suite failing inside mutants/: the runner
        # uses -x, so one failure aborts stats collection and no mutant is ever
        # tested. mutation-check.sh prints the command to reproduce it there.
        # (--regenerate now deletes mutants/ before rebuilding, so a stale copy
        # or a stats map cached from a broken run is no longer a candidate.)
        NOTES[-1]="no mutants ran — baseline fails in mutants/; see the log"
      fi
    fi
  fi
fi

echo
printf '%-28s %-8s %6s  %s\n' "GATE" "RESULT" "TIME" "NOTES"
printf '%-28s %-8s %6s  %s\n' "----------------------------" "--------" "------" "-----"
for i in "${!NAMES[@]}"; do
  printf '%-28s %-8s %6s  %s\n' "${NAMES[$i]}" "${RESULTS[$i]}" "${TIMES[$i]}" "${NOTES[$i]}"
done
echo

# The tail of each failure, inline. Otherwise the table sends you to seven log
# files to find out what the one red line meant.
for i in "${!NAMES[@]}"; do
  [ "${RESULTS[$i]}" = "FAIL" ] || continue
  echo "--- ${NAMES[$i]} (last 30 lines of ${LOGS[$i]}) ---"
  tail -n 30 "${LOGS[$i]}" | sed 's/^/  /'
  echo
done

if git status --porcelain scripts/mutation-floor.txt 2>/dev/null | grep -q .; then
  echo "Note: the mutation floor ratcheted up - scripts/mutation-floor.txt is modified. Commit it."
  echo
fi

if [ "$FAILED" -gt 0 ]; then
  echo "$FAILED gate(s) failed. Full output in $LOGDIR"
  exit 1
fi
if [ "$UNAVAILABLE" -gt 0 ]; then
  echo "All gates that ran passed, but $UNAVAILABLE could not run. That is not a green run."
  exit 2
fi
echo "Everything okay."
echo "Evidence kept in $LOGDIR (pruned after 14 days)."
