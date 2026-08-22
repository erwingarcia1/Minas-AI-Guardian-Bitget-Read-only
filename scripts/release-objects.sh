#!/usr/bin/env bash
# Does every version tag have a GitHub Release object? (#94)
#
# A tag with no Release reads fine from git and invisible from the repo's
# Releases page - the drift that sat unnoticed from v1.1.0 to v1.6.2 until
# the owner spotted it on a screenshot. This makes a machine notice first:
# list every vX.Y.Z tag, fail if any has no Release object.
#
# Non-version tags (working-tree snapshots like pre-autonomous-*) are
# deliberately out of scope: they are not releases, and demanding a Release
# object for them would force publishing one or failing forever.
#
#   scripts/release-objects.sh
#
# Exit 0 every version tag covered, 1 on any missing Release - or on any
# question this could not answer (gh missing, unauthenticated, GitHub
# unreachable). An unanswered question is not a pass. Callers guard gh
# availability themselves (health-check.sh skips, never passes, without gh)
# - same contract as scheduled-gates-freshness.sh.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

RELEASES=$(gh release list --limit 200 --json tagName --jq '.[].tagName') || {
  echo "could not list GitHub Releases (gh missing, unauthenticated, or GitHub unreachable) - unanswered is not a pass" >&2
  exit 1
}

MISSING=0
CHECKED=0
while read -r tag; do
  CHECKED=$((CHECKED + 1))
  if ! grep -qxF "$tag" <<<"$RELEASES"; then
    echo "tag $tag has no GitHub Release object - scripts/github-release.sh $tag backfills it" >&2
    MISSING=$((MISSING + 1))
  fi
done < <(git tag --list | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$')

if [ "$CHECKED" -eq 0 ]; then
  echo "no version tags found at all - nothing to check is not a pass" >&2
  exit 1
fi

if [ "$MISSING" -gt 0 ]; then
  echo "$MISSING of $CHECKED version tags have no Release" >&2
  exit 1
fi
echo "every version tag has a GitHub Release ($CHECKED checked)"
