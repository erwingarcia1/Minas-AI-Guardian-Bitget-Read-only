#!/usr/bin/env bash
# Did the scheduled gates actually RUN recently? (#86)
#
# A scheduled job that silently stops being scheduled - a renamed workflow, a
# disabled cron, a fork where Actions is off - reads as permanently green,
# which is the exact silence-as-success failure the rest of this repo keeps
# closing. This asks only "did it run within the window"; whether it PASSED
# is the job's own issue-filing to report, so a red scheduled run still
# satisfies this gate - the loudness for redness already exists.
#
# Counts schedule AND workflow_dispatch runs: a manual dispatch runs the same
# gates, and refusing to count it would fail a release the day after someone
# proved the gates by hand.
#
#   scripts/scheduled-gates-freshness.sh [window-days]   # default 8
#
# Exit 0 fresh, 1 stale or never ran. Callers guard gh availability
# themselves (health-check.sh skips, never passes, without gh).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

WINDOW_DAYS="${1:-8}"

# Filtered by EVENT on the server, one query per event, newest wins. The
# first version paged through the last 50 runs of any event and picked the
# first scheduled one - and on 2026-08-29 exactly 50 push runs had landed
# since the last scheduled run, so it fell off the page and a release was
# refused for a gate that had run five days earlier. A freshness check
# whose answer depends on how busy the week was is not a freshness check.
LAST=""
for EVENT in schedule workflow_dispatch; do
  ONE=$(gh run list --workflow CI --event "$EVENT" --limit 5 --json createdAt,status \
    --jq 'first(.[] | select(.status == "completed")) | .createdAt // empty')
  if [ -n "$ONE" ] && { [ -z "$LAST" ] || [[ "$ONE" > "$LAST" ]]; }; then LAST="$ONE"; fi
done

if [ -z "$LAST" ]; then
  echo "no completed scheduled or dispatched CI run found at all - the slow gates have never run in CI, or Actions cannot be queried" >&2
  exit 1
fi

python3 - "$LAST" "$WINDOW_DAYS" <<'EOF'
import datetime
import sys

last = datetime.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
age = datetime.datetime.now(datetime.timezone.utc) - last
window = datetime.timedelta(days=int(sys.argv[2]))
print(f"last scheduled-gates run: {last.isoformat()} ({age.days}d ago; window {sys.argv[2]}d)")
if age > window:
    print("the scheduled gates have not run inside the window - the cron is "
          "broken or disabled, and its silence reads as green", file=sys.stderr)
    raise SystemExit(1)
EOF
