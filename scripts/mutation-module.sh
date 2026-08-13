#!/usr/bin/env bash
# Unrestricted mutation score for ONE module, against a chosen test selection.
#
#   scripts/mutation-module.sh sentinel/detectors/withdrawal.py \
#       tests/test_detectors_withdrawal.py tests/test_withdrawal_evidence.py
#
# `scripts/mutation-check.sh` keeps only decision points - comparisons, boolean
# operators, control flow - because an unrestricted run over the whole package
# is ~1,300 mutants and mostly cosmetic. That trade has a hole in it: a module
# with no conditionals produces no decision-point mutants at all, so the score
# says nothing about it and the silence reads like coverage.
#
# `detectors/withdrawal.py` is that module, and it is the CRITICAL path - the
# detector that fires when money leaves. It is a straight-line Detection
# builder. Every one of its mutations is an evidence key or a message string,
# which the restricted run discards as cosmetic and which are nothing of the
# kind: `dispatch._dedup_key` reads `evidence["order_id"]` to decide whether
# two withdrawals are the same withdrawal.
#
# This script is how such a module gets measured. It is deliberately manual and
# not in CI: it is for auditing one file deeply, not for gating every push.
#
# Measured for #65 (2026-08-13), 59 mutants in `detect_new_withdrawals`:
#
#   before  tests/test_detectors_withdrawal.py alone      40 survived  67.8%
#   after   + tests/test_withdrawal_evidence.py            0 survived   0.0%
#
# The automated floor did not move, and cannot: it counts decision-point
# mutants, and this module has none.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
[ $# -ge 2 ] || { echo "usage: $0 <path/to/module.py> <test file> [test file...]" >&2; exit 2; }

MODULE_PATH=$1; shift
MODULE=${MODULE_PATH%.py}; MODULE=${MODULE//\//.}

PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
MUTMUT=mutmut; [ -x .venv/bin/mutmut ] && MUTMUT=.venv/bin/mutmut

{
  echo "[mutmut]"
  echo "source_paths=sentinel"
  echo "pytest_add_cli_args_test_selection="
  for t in "$@"; do echo "    $t"; done
  echo "runner=python -m pytest -x -q -p no:cacheprovider"
} > setup.cfg
trap 'rm -f setup.cfg' EXIT

if [ ! -f "mutants/$MODULE_PATH" ]; then
  echo "Generating mutants (~8 min, cached in mutants/)..."
  "$MUTMUT" run 'zzz.no.such.mutant' >/dev/null 2>&1 || true
fi

# Trailing [0-9][0-9]* on purpose: a bare `x_fn__mutmut_` prefix also matches
# the original and the registration block, and passing that as a mutant name
# makes mutmut run one fewer than it reports - the shrinking-denominator
# problem this project has already been bitten by.
mapfile -t MUTANTS < <(
  grep -o "x_[A-Za-z0-9_]*__mutmut_[0-9][0-9]*" "mutants/$MODULE_PATH" | sort -u | sed "s|^|$MODULE.|"
)
echo "Mutants in $MODULE_PATH: ${#MUTANTS[@]}"
if [ "${#MUTANTS[@]}" -eq 0 ]; then
  echo "none found - has mutants/ been generated for this file?" >&2
  exit 2
fi

LOG=$(mktemp)
"$MUTMUT" run --max-children 6 "${MUTANTS[@]}" > "$LOG" 2>&1 || true
# `|| true` because grep exits 1 when it matches nothing - which here means
# ZERO SURVIVORS, the best possible result. Under `set -o pipefail` that
# aborted the script just as the score reached 100%, so the tool failed
# precisely when the news was good and printed no number at all.
{ tr '\r' '\n' < "$LOG" | grep -E "^🙁" || true; } | sed 's/^🙁 //' | sort > /tmp/module-survivors.txt
KILLED=$(tr '\r' '\n' < "$LOG" | grep -cE "^🎉" || true)
SURVIVED=$(wc -l < /tmp/module-survivors.txt)
TOTAL=$((KILLED + SURVIVED))
if [ "$TOTAL" -eq 0 ]; then
  echo "no mutants ran - check $LOG" >&2
  exit 2
fi

# Same guard as mutation-check.sh: a selected mutant that never runs is counted
# nowhere, and a rate over the ones that did looks exactly like a rate over all
# of them.
if [ "$TOTAL" -ne "${#MUTANTS[@]}" ]; then
  echo "selected ${#MUTANTS[@]} mutants but only $TOTAL ran - the rate would be" >&2
  echo "computed over a subset and read as if it covered all of them. Check $LOG." >&2
  exit 2
fi

echo
if [ "$SURVIVED" -gt 0 ]; then
  echo "SURVIVORS (${SURVIVED}):"
  sed 's/^/  /' /tmp/module-survivors.txt
  echo
fi
"$PY" -c "print(f'Killed $KILLED/$TOTAL - survival rate {$SURVIVED*100/$TOTAL:.1f}%')"
