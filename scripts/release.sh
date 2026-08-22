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
#   4. gh is installed and authenticated - the cut ends by publishing the
#      tag's GitHub Release (#94), and discovering it cannot AFTER the tag
#      is pushed would leave exactly the tag-without-Release drift the
#      release-objects gate exists to catch. Refused here, cheaply.
#   5. `[Unreleased]` has actual content
#   6. the gates pass - fast gates locally when scheduled CI already ran
#      the slow ones on this exact tree and was green (#93); the full
#      local health-check in every case that cannot be proven
#   7. CHANGELOG surgery + pyproject bump
#   8. commit, annotated tag, push, GitHub Release (#94)
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
# Done here since #94: the GitHub Release must be created after the tag is
# pushed, and splitting "push" from "publish" is how a tag ends up with no
# Release. Read the notes BEFORE cutting - the diff this commits is the
# [Unreleased] section you already wrote.
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

# --- 4. gh can publish the Release --------------------------------------------
#
# Fail closed, and fail EARLY: without this, gh's absence surfaces only after
# the tag is pushed, when the cut can no longer stop cleanly.

command -v gh >/dev/null 2>&1 || die "gh is not installed. The cut publishes a GitHub Release for the tag (#94);
pushing a tag it cannot publish is the exact drift the release-objects gate
refuses. Install gh, run 'gh auth login', and run this again."
gh auth status >/dev/null 2>&1 || die "gh is not authenticated. The cut publishes a GitHub Release for the tag
(#94); pushing a tag it cannot publish is the exact drift the release-objects
gate refuses. Run 'gh auth login' and run this again."

# --- 5. [Unreleased] has content --------------------------------------------

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

# --- 6. health-check ---------------------------------------------------------
#
# The slow gates (design-guard probes, mutation floor) run canonically in
# scheduled CI (#86). If the most recent scheduled run covers this exact
# tree - its commit an ancestor of HEAD, no gate-relevant path changed
# since - and is green, this release runs only the fast gates locally
# (#93; up to ~50 minutes saved, and the cache is cold exactly when a
# release is worth cutting). FAIL CLOSED everywhere else: cannot prove
# coverage means run everything locally exactly as before - gh missing,
# unauthenticated, or GitHub down is "prove it locally", never "assume it
# passed" - and covered-but-RED means the gates already failed on this
# code, so the release refuses rather than re-rolling the dice on a
# laptop. This predicate may only make a release cheaper, never weaker.

set +e
scripts/scheduled-gates-cover.sh
COVERAGE=$?
set -e
if [ "$COVERAGE" -eq 3 ]; then
  die "the scheduled slow gates FAILED on code this release contains - not releasing.
Fix what the scheduled run found (it filed an issue with the specifics), push,
and run this again once the scheduled gates are green."
elif [ "$COVERAGE" -eq 0 ]; then
  echo "release: scheduled CI already ran the slow gates on this code - fast gates only (~4 min)"
  scripts/health-check.sh --fast || die "health-check failed - not releasing.
Logs are in .health-check-logs/. Fix, commit, and run this again."
else
  echo "release: running the full health-check (this takes ~20 minutes)"
  scripts/health-check.sh || die "health-check failed - not releasing.
Logs are in .health-check-logs/. Fix, commit, and run this again."
fi

# Health-check can ratchet scripts/mutation-floor.txt, which is tracked. If it
# did, that is a real change and it belongs in its own commit, not swept into
# the release.
if [ -n "$(git status --porcelain)" ]; then
  git status --short >&2
  die "health-check left changes in the tree (the mutation floor ratchets).
Commit those separately, then run this again."
fi

# --- 7. CHANGELOG + pyproject ------------------------------------------------

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

# `sentinel.__version__` is read from *installed* metadata, deliberately - a
# hand-maintained copy in the package would be a third place to disagree. The
# cost is that bumping pyproject.toml leaves an editable install still
# reporting the old version, so refresh it before asserting the two agree.
# Without this the consistency check below can never pass on a developer
# machine, which the first real release caught.
#
# --no-deps because nothing about a version bump changes the dependency set,
# and resolving them would turn a thirty-second release into a slow one.
echo "release: refreshing the editable install so the version metadata matches"
"$PY" -m pip install -e . --no-deps -q \
  || die "could not refresh the install, so 'sentinel --version' and
pyproject.toml cannot be shown to agree. The tree has been modified;
'git checkout -- .' to undo."

# The consistency test is the one that proves the surgery above agreed with
# itself. Cheap, and it runs against what is about to be committed.
"$PY" -m pytest tests/test_project_consistency.py -q \
  || die "the version-consistency tests fail after the edit - CHANGELOG.md and
pyproject.toml disagree. The tree has been modified; 'git checkout -- .' to undo."

# --- 8. commit + tag + push + Release -----------------------------------------

git add CHANGELOG.md pyproject.toml
git commit -q -m "Release $TAG" -m "$(printf 'Notes in CHANGELOG.md under [%s].\n\nCut with scripts/release.sh, which refuses an empty [Unreleased], a dirty\ntree, a duplicate tag, unpublishable gh, or a failing health-check.' "$VERSION")"
git tag -a "$TAG" -m "Minas AI Guardian $TAG"

git push
git push origin "$TAG"

# Idempotent (an existing Release is "nothing to do"), so re-running a cut
# that died between push and publish finishes instead of erroring.
scripts/github-release.sh "$TAG" --latest || die "the tag is pushed but its GitHub Release was not created.
Run 'scripts/github-release.sh $TAG --latest' to finish - until then the
release-objects gate will (rightly) fail."

cat <<EOF

release: done.
  commit  $(git rev-parse --short HEAD)  (pushed)
  tag     $TAG (annotated, pushed, GitHub Release published)

Next: confirm CI on the release commit is green (gh run list --limit 1).

Then deploy it, on the VM:
  sudo /opt/minas-guardian/scripts/deploy.sh $TAG

deploy.sh checks the tag out itself, so commits made after this one (a log
handoff, say) cannot block the deploy.
EOF
