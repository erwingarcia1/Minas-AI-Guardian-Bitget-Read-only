#!/usr/bin/env bash
# Publish the GitHub Release object for a tag that already exists (#94).
#
#   scripts/github-release.sh v1.2.3            # backfill / repair
#   scripts/github-release.sh v1.2.3 --latest   # what release.sh passes for a new cut
#
# Body = that version's section of CHANGELOG.md, verbatim; title = the tag.
# The Release is presentation for a tag, never the other way round: this
# script NEVER creates or moves a tag (`--verify-tag` makes gh refuse if the
# tag is not already on the remote).
#
# Idempotent on purpose: a Release that already exists is "nothing to do",
# exit 0 - so re-running a cut, or the backfill, cannot fail on its own
# earlier success. Everything unprovable is exit 1: gh missing or
# unauthenticated, no CHANGELOG section for the version, tag absent.
# Without --latest the Release is created with --latest=false, so
# backfilling an old tag can never steal the "Latest" badge from the
# newest release.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

die() { printf 'github-release: %s\n' "$1" >&2; exit 1; }

[ $# -ge 1 ] || die "usage: scripts/github-release.sh vX.Y.Z [--latest]"
TAG="$1"
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "tag must be vX.Y.Z, got: $TAG"
VERSION="${TAG#v}"

LATEST="--latest=false"
[ "${2:-}" = "--latest" ] && LATEST="--latest"

command -v gh >/dev/null 2>&1 || die "gh is not installed - cannot publish a Release"
gh auth status >/dev/null 2>&1 || die "gh is not authenticated - cannot publish a Release"

if gh release view "$TAG" --json tagName >/dev/null 2>&1; then
  echo "github-release: $TAG already has a Release - nothing to do"
  exit 0
fi

NOTES=$(mktemp)
trap 'rm -f "$NOTES"' EXIT

python3 - "$VERSION" > "$NOTES" <<'EOF'
import re
import sys

version = sys.argv[1]
lines = open("CHANGELOG.md").read().splitlines()
try:
    start = next(i for i, line in enumerate(lines)
                 if re.match(rf"## \[{re.escape(version)}\]", line))
except StopIteration:
    sys.stderr.write(f"CHANGELOG.md has no '## [{version}]' section - "
                     "a Release with no notes is the thing this repo refuses\n")
    raise SystemExit(1)
end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
           len(lines))
body = lines[start + 1:end]
while body and not body[0].strip():
    body.pop(0)
while body and not body[-1].strip():
    body.pop()
print("\n".join(body))
EOF

gh release create "$TAG" --verify-tag --title "$TAG" --notes-file "$NOTES" "$LATEST" \
  || die "gh refused to create the Release for $TAG (is the tag pushed?)"

echo "github-release: created $TAG"
