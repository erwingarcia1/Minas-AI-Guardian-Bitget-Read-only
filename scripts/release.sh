#!/usr/bin/env bash
# Cut a release: notes, version, tag - in one command that refuses to do half of it.
#
#   scripts/release.sh 1.2.0
#
# The rule this enforces is that there is no release without notes. Not "please
# remember to write notes" - the script will not proceed past an empty
# `[Unreleased]`, and the deploy gate (scripts/deploy-gate.sh) will not ship a
# commit that is not exactly a tag. So the only path to production runs through
# here, and running through here produces the note as a side effect. That is
# the whole design: the note is not paperwork that follows the release, it is
# the thing being released.
#
# What it does, in order (cheap refusals first - none of them should cost you
# twenty minutes of health-check to discover):
#
#   1. version looks like X.Y.Z
#   2. working tree is clean
#   3. tag vX.Y.Z does not already exist
#   4. `[Unreleased]` has actual content
#   5. full health-check passes            <- the expensive one, last
#   6. CHANGELOG surgery + pyproject bump
#   7. commit, annotated tag
#
# Nothing is written until every check has passed, so a refusal leaves the tree
# exactly as it found it.
#
# ## What counts as "content" in [Unreleased]
#
# The `### Added` / `### Fixed` / ... subsections, and only those. The italic
# `_Pending customer/ops items (not code): ..._` paragraph directly under the
# `## [Unreleased]` heading is a standing note about work that is not code; it
# is carried forward into the new empty `[Unreleased]` rather than released,
# and it does not count towards emptiness. Without that distinction the
# emptiness check could never fire, because that paragraph is always there.
#
# ## Pushing
#
# Deliberately not done here. `git push && git push --tags` is yours to run,
# after you have looked at what the commit says.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python

die() { printf '\nrelease: %s\n' "$1" >&2; exit 1; }

# --- 1. argument -------------------------------------------------------------

[ $# -eq 1 ] || die "usage: scripts/release.sh <version>   (e.g. 1.2.0)

Which level? See CONTRIBUTING.md 'Releases and versioning'. Briefly:
  MAJOR  an operator must act before upgrading (config rejected, irreversible
         migration, removed setting)
  MINOR  new detectors, features, alert channels
  PATCH  bug fixes, including detector fixes"

VERSION="$1"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must be X.Y.Z, got: $VERSION"
TAG="v$VERSION"

# --- 2. clean tree -----------------------------------------------------------
#
# A release commit must contain the release and nothing else. Sweeping up
# whatever happened to be in the tree is how a release ends up containing a
# change nobody wrote a note for.

if [ -n "$(git status --porcelain)" ]; then
  git status --short >&2
  die "working tree is dirty. Commit or stash first - a release commit carries
the release and nothing else."
fi

# --- 3. tag does not exist ---------------------------------------------------

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  die "tag $TAG already exists (points at $(git rev-parse --short "$TAG")).
Pick the next version, or delete the tag if it was never pushed."
fi

# --- 4. [Unreleased] has content --------------------------------------------

UNRELEASED_LINES=$("$PY" - "$VERSION" <<'EOF'
import re, sys
text = open("CHANGELOG.md").read().splitlines()
try:
    start = next(i for i, l in enumerate(text) if l.strip() == "## [Unreleased]")
except StopIteration:
    print("NO_UNRELEASED_HEADING"); raise SystemExit(0)
end = next((i for i in range(start + 1, len(text)) if text[i].startswith("## ")), len(text))
# Content = the ### subsections only. The preamble above the first ### is the
# standing ops note, which is carried forward, not released.
body = text[start + 1:end]
first_sub = next((i for i, l in enumerate(body) if l.startswith("### ")), None)
if first_sub is None:
    print(0); raise SystemExit(0)
print(sum(1 for l in body[first_sub:] if l.strip()))
EOF
)

[ "$UNRELEASED_LINES" != "NO_UNRELEASED_HEADING" ] || die "CHANGELOG.md has no '## [Unreleased]' heading."

if [ "$UNRELEASED_LINES" -eq 0 ]; then
  die "[Unreleased] is empty - there is nothing to release.

This is not a formality to work around. Write what changed. If it alters which
alerts an operator sees, it goes under the heading
'### Changed detection behaviour'
(CONTRIBUTING.md says what belongs there). Then run this again."
fi

echo "release: $TAG - [Unreleased] has $UNRELEASED_LINES lines of notes"

# --- 5. health-check ---------------------------------------------------------
#
# Every gate, not --fast. A release is exactly the moment the slow gates are
# worth their twenty minutes; skipping them here is skipping them entirely,
# because nothing downstream re-runs them.

echo "release: running the full health-check (this takes ~20 minutes)"
scripts/health-check.sh || die "health-check failed - not releasing.
Logs are in .health-check-logs/. Fix, commit, and run this again."

# Health-check can ratchet scripts/mutation-floor.txt, which is tracked. If it
# did, that is a real change and it belongs in its own commit, not swept into
# the release.
if [ -n "$(git status --porcelain)" ]; then
  git status --short >&2
  die "health-check left changes in the tree (the mutation floor ratchets).
Commit those separately, then run this again."
fi

# --- 6. CHANGELOG + pyproject ------------------------------------------------

"$PY" - "$VERSION" <<'EOF'
import datetime, re, sys

version = sys.argv[1]
today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
path = "CHANGELOG.md"
lines = open(path).read().splitlines()

start = next(i for i, l in enumerate(lines) if l.strip() == "## [Unreleased]")
end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
body = lines[start + 1:end]

first_sub = next(i for i, l in enumerate(body) if l.startswith("### "))
carried, released = body[:first_sub], body[first_sub:]

def trimmed(block):
    while block and not block[0].strip():
        block.pop(0)
    while block and not block[-1].strip():
        block.pop()
    return block

carried, released = trimmed(list(carried)), trimmed(list(released))

new_head = ["## [Unreleased]", ""]
if carried:
    new_head += carried + [""]
new_head += [f"## [{version}] — {today}", ""] + released + [""]

lines[start:end] = new_head

# Compare links. The previous release is the newest remaining heading; the
# repo URL is taken from the existing [Unreleased] link rather than written
# down a second time, so a fork or a rename cannot leave this pointing at the
# wrong repository.
text = "\n".join(lines)
prev = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", text, re.M)
prev = next((v for v in prev if v != version), None)
if prev is None:
    raise SystemExit("release: no previous version heading to compare against")

m = re.search(r"^\[Unreleased\]: (\S+)/compare/\S+\.\.\.HEAD$", text, re.M)
if not m:
    raise SystemExit("release: could not find the [Unreleased] compare link to derive the repo URL")
base = m.group(1)

text = re.sub(r"^\[Unreleased\]: .*$",
              f"[Unreleased]: {base}/compare/v{version}...HEAD", text, count=1, flags=re.M)
text = re.sub(r"^(\[Unreleased\]: .*)$",
              rf"\1\n[{version}]: {base}/compare/v{prev}...v{version}", text, count=1, flags=re.M)

open(path, "w").write(text + "\n")

# pyproject, in the [project] table only - a version pin in a dependency must
# not be rewritten by this.
pp = open("pyproject.toml").read()
new_pp, n = re.subn(r'(?m)^(version = )"[^"]+"', rf'\1"{version}"', pp, count=1)
if n != 1:
    raise SystemExit("release: could not find the version line in pyproject.toml")
open("pyproject.toml", "w").write(new_pp)

print(f"release: CHANGELOG heading [{version}] — {today}, compare link vs v{prev}, pyproject {version}")
EOF

# The consistency test is the one that proves the surgery above agreed with
# itself. Cheap, and it runs against what is about to be committed.
"$PY" -m pytest tests/test_project_consistency.py -q \
  || die "the version-consistency tests fail after the edit - CHANGELOG.md and
pyproject.toml disagree. The tree has been modified; 'git checkout -- .' to undo."

# --- 7. commit + tag ---------------------------------------------------------

git add CHANGELOG.md pyproject.toml
git commit -q -m "Release $TAG" -m "$(printf 'Notes in CHANGELOG.md under [%s].\n\nCut with scripts/release.sh, which refuses an empty [Unreleased], a dirty\ntree, a duplicate tag, or a failing health-check.' "$VERSION")"
git tag -a "$TAG" -m "Minas AI Guardian $TAG"

cat <<EOF

release: done.
  commit  $(git rev-parse --short HEAD)
  tag     $TAG (annotated)

Next:
  git show $TAG          # read it before it leaves the machine
  git push && git push --tags

The deploy gate accepts this commit because it is exactly a tag. It will
refuse the next one until you cut another release.
EOF
