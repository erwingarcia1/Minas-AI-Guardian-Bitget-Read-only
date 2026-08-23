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
# 3. Mutant *generation* is independent of the tests, and slow: ~25 minutes for
#    this package as of #70, plus a stats-collection pass of similar length
#    before the first mutant runs. It was ~8 minutes when this note was
#    written and the package has roughly doubled since; the number is recorded
#    because a run that prints nothing for the best part of an hour looks
#    exactly like a hung one, and guessing at the cause costs more than the
#    wait does. It is cached in `mutants/` and only redone with --regenerate,
#    when `mutants/` is absent, or when the drift check below finds a stale
#    non-Python file. Generation always starts by deleting `mutants/`, because
#    mutmut refreshes only *some* of what it copied - see 6.
# 6. `copy_src_dir()` copies everything under `source_paths` with
#    `if target_path.exists(): continue`. Python files are refreshed separately
#    by the mutation pass, which compares mtimes, so *code* stays current. Every
#    other file under `sentinel/` is copied the first time `mutants/` is built
#    and never again - and `also_copy` (tests/, setup.cfg, pyproject.toml) *is*
#    refreshed every run, which is what makes this invisible: the tests in
#    `mutants/` are always current, so a stale copy there looks impossible.
#    `sentinel/schema.sql` is the only non-Python file we have, and it is the
#    schema every test's database is built from. When it lagged behind a new
#    table, the ~80 tests that predated the table passed and the ones that
#    touched it failed with `no such table` - which, with the runner's `-x`,
#    aborted stats collection and ran zero mutants. `--regenerate` did not fix
#    it, because it did not delete anything. Only `rm -rf mutants/` did.
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
#   Decided, and pinned (#70): it is. The metric an operator is alarmed on says
#   an exchange stopped stamping times; this line is where whoever was paged
#   goes next, and a warning that cannot say *which* record lost its timestamp
#   gives them nothing to look at. `tests/test_detectors_quantity_drop.py` pins
#   the property rather than the wording - an id where there is one, the symbol
#   where there is not, and an explicit "<unidentified>" where there is
#   neither - across both warnings, since the expression appears twice. Six
#   survivors, one decision.
#
# The floor is set below the measured score to leave room for exactly these:
# it is a ratchet against decisions *becoming* unpinned, not a target to reach.
#
# As of #70 those two equivalents are the *only* survivors: 117/119 = 98%. The
# nine that were addressable are pinned - three in `build_sms_channel` (a
# provider configured half way), six in the `_in_window` log line. A survivor
# appearing here now is therefore news, not backlog.
#
# ## What this gate does not cover
#
# The scope is the two packages named in the `classify_mutants.py` call below:
# `sentinel.detectors` and `sentinel.alerts`. Everything else in the package -
# `keyreplacement.py`, `net.py`, `portal.py`, `exchange.py` - produces no
# mutants here however many decisions it makes, and adding its tests to the
# selection below buys nothing but a slower run. Those modules are covered by
# `scripts/probes/design-guards.json` instead. Worth knowing before concluding
# from a 98% that the security-critical paths are measured: they are, by the
# other tool.
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
#
# Narrow is not the same as arbitrary. A mutant that no selected test reaches
# is scored nowhere, so getting this list wrong does not make the gate slower,
# it makes it quieter - and this script fails outright rather than report a
# score over a subset. The last four were added after a clean run left 9
# mutants unreached, each in a function these files are the only tests for:
# portal_action_line and monitoring_note (the customer email), the ClickSend
# and per-account branches of build_sms_channel, and acknowledge_operator_page.
# Alerting code added under sentinel/ needs its tests added here too.
#
# Keep this a plain list. It is a configparser multi-line value, so an indented
# `#` comment is read as another test path, not as a comment.
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
    tests/test_customer_email_template.py
    tests/test_clicksend_sms.py
    tests/test_contact_router.py
    tests/test_operator_alerting.py
    tests/test_escalation_reach.py
    tests/test_secondary_contact_escalation.py
    tests/test_telegram_self_serve.py
    tests/test_alert_repeat_suppression.py
    tests/test_customer_digest.py
    tests/test_watch_alarms.py
runner=python -m pytest -x -q -p no:cacheprovider
EOF
trap 'rm -f setup.cfg' EXIT

# mutmut will not notice a changed non-Python source file (quirk 6 above), so
# notice it here. Cheap: sentinel/schema.sql is currently the only such file,
# and `cmp` on it costs nothing next to an 8-minute generation. Compare contents
# rather than mtimes - mutmut copies with `copy2`, so the stale copy carries the
# *source's* old mtime and looks freshly written.
STALE=false
if [ -d mutants ]; then
  while IFS= read -r src; do
    if ! cmp -s "$src" "mutants/$src"; then
      echo "mutants/$src does not match $src - the copy mutmut runs the tests" >&2
      echo "against is stale, and it will not refresh it itself. Rebuilding." >&2
      STALE=true
      break
    fi
  done < <(find sentinel -type f ! -name '*.py' ! -path '*__pycache__*')
fi

# The .py files above are deliberately excluded: mutmut REWRITES its copies
# with the mutants embedded, so a byte comparison always differs. The
# assumption that replaced it - "mutmut notices a changed source itself" -
# is FALSE, proven 2026-08-23: a changed detector produced a 30-second pass
# judged entirely against the old code. So generation records a manifest of
# source hashes, and any mismatch (or a cache from before manifests existed
# - unverifiable is stale, fail closed) rebuilds.
if [ -d mutants ] && [ "$STALE" = false ]; then
  MANIFEST=mutants/.source-manifest
  CURRENT=$(find sentinel -name '*.py' ! -path '*__pycache__*' -type f | sort | xargs sha256sum)
  if [ ! -f "$MANIFEST" ] || [ "$CURRENT" != "$(cat "$MANIFEST")" ]; then
    echo "the Python sources have changed since mutants/ was generated (or the" >&2
    echo "cache predates source manifests) - a score over the old code would" >&2
    echo "read as a pass on the new. Rebuilding." >&2
    STALE=true
  fi
fi

if [ "$REGENERATE" = true ] || [ ! -d mutants ] || [ "$STALE" = true ]; then
  # Delete first. Generating over a surviving mutants/ is how every staleness
  # bug in this gate has started: mutmut keeps whatever is already there, both
  # the non-Python files above and mutmut-stats.json, which caches the
  # test-to-function map and will happily reuse one built by a broken run.
  # A regeneration that leaves anything behind is not a regeneration.
  rm -rf mutants
  echo "Generating mutants (~8 min, cached afterwards)..."
  "$MUTMUT" run 'zzz.no.such.mutant' >/dev/null 2>&1 || true
  # The staleness manifest: what the sources looked like when this cache was
  # built. Written AFTER generation so a crash mid-generate leaves no
  # manifest and the next run rebuilds rather than trusting a half-cache.
  find sentinel -name '*.py' ! -path '*__pycache__*' -type f | sort | xargs sha256sum > mutants/.source-manifest
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
# Zero is not a score, it is a gate that measured nothing - and in CI a silent
# pass here reads exactly like coverage. Spelled as an `if` so the failure is
# unconditional rather than riding on an AND-list's status.
if [ "$TOTAL" -eq 0 ]; then
  echo "no mutants ran - this gate measured nothing. Check $LOG." >&2
  echo "Usually the baseline suite fails inside mutants/, so stats collection" >&2
  echo "aborts before any mutant is tested. Run the selection there by hand:" >&2
  echo "  (cd mutants && ../.venv/bin/python -m pytest -x -q tests/)" >&2
  exit 2
fi

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
  # A missing floor file used to default to 0, so `--check` passed whatever it
  # was handed and printed "Floor: 0%" - a gate that cannot fail, in the one
  # mode whose entire job is failing. There is nothing to compare against, so
  # refuse rather than compare against nothing.
  FLOOR=$(cat "$FLOOR_FILE" 2>/dev/null || true)
  case "$FLOOR" in
    ''|*[!0-9]*)
      echo "--check has no floor to compare against: $FLOOR_FILE is missing or" >&2
      echo "not a plain number (read: '${FLOOR}'). Refusing to pass a gate with" >&2
      echo "no threshold. Restore it from git, or seed it with the current score" >&2
      echo "by running this script without --check first." >&2
      exit 2 ;;
  esac
  echo "Floor: ${FLOOR}%"
  if [ "$SCORE" -lt "$FLOOR" ]; then
    echo "Mutation score dropped below the floor. A decision this suite used to pin, it no longer does." >&2
    exit 1
  fi
  # Ratchet, with headroom: floor = max(floor, score - BUFFER). A better score
  # still raises the floor, so a gain cannot be quietly given back, and the
  # floor still only ever moves up.
  #
  # The buffer is the point. Ratcheting to the score *exactly* guarantees zero
  # slack forever - the floor is dragged up to meet every improvement, so the
  # steady state is score == floor and the next honest change to any function
  # this measures trips the gate. A tripwire that fires during legitimate work
  # is one people learn to route around with --no-verify, and a gate everyone
  # bypasses protects nothing. Two points buys room for ordinary change without
  # letting a real regression through.
  #
  # Spelled as an `if` rather than `[ ... ] && { ... }` because that form was
  # once the last command in the script: when the score merely equalled the
  # floor the test was false and its status became the script's, so a passing
  # run exited 1, identically to a real breach. Under the old zero-headroom
  # rule equality was the steady state, so that was every healthy run.
  BUFFER=2
  TARGET=$((SCORE - BUFFER))
  if [ "$TARGET" -lt 0 ]; then TARGET=0; fi
  if [ "$TARGET" -gt "$FLOOR" ]; then
    echo "$TARGET" > "$FLOOR_FILE"
    echo "Floor raised to ${TARGET}% (score ${SCORE}%, ${BUFFER} points of headroom)."
  fi
fi

# Explicit, so no future last-command status can be mistaken for a verdict.
exit 0
