#!/usr/bin/env bash
# The deploy gate: refuse to ship a commit that is not exactly a release tag.
#
#   scripts/deploy-gate.sh              # check; exit 0 to proceed, 1 to stop
#   scripts/deploy-gate.sh --export     # same check, then print the build vars
#
# Step one of every deploy, before config validation and before the build. See
# docs/deployment.md "Deploying code changes".
#
# ## Why this exists
#
# Release notes that depend on someone remembering are not enforcement. This is
# the part that makes them structural: production can only run a commit that
# carries a tag, tags are only created by scripts/release.sh, and release.sh
# refuses an empty `[Unreleased]`. So every deployed commit has notes, by
# construction rather than by discipline.
#
# It also answers "what is running in production" with a name instead of a
# guess, which is the same question `sentinel version` answers from inside the
# container.
#
# ## There is no --force
#
# Deliberate, and the most likely thing for a future reader to want to add.
# Don't. An escape hatch used once during an incident is an escape hatch used
# every time afterwards, and the first use is always justified. If something is
# urgent, cut a PATCH release - `scripts/release.sh X.Y.Z+1` - which takes
# about thirty seconds of typing and produces the note as a side effect. The
# note is most valuable precisely for the changes that felt too urgent to write
# one for.
#
# The bypass that does exist is `git tag`, by hand, which requires deciding to
# lie about what a release is. That is a different act from passing a flag, and
# it leaves a tag behind that says who did it.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

EXPORT=false
case "${1:-}" in
  --export) EXPORT=true ;;
  -h|--help) sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
  "") ;;
  *) echo "deploy-gate: unknown argument: $1 (try --export, --help)" >&2; exit 2 ;;
esac

if ! TAG=$(git describe --exact-match --tags HEAD 2>/dev/null); then
  SHA=$(git rev-parse --short HEAD)
  # --match 'v*': only release tags count as "the last release". The repo
  # carries non-release tags (a pre-autonomous-work marker), and reporting one
  # of those as the last release would misstate how far production has drifted.
  DESC=$(git describe --tags --abbrev=0 --match 'v*' HEAD 2>/dev/null || echo "none")
  AHEAD=$([ "$DESC" != "none" ] && git rev-list --count "$DESC..HEAD" || echo "?")
  cat >&2 <<EOF

deploy-gate: REFUSED - HEAD is not a release.

  HEAD            $SHA
  last release    $DESC ($AHEAD commits behind HEAD)

This commit has no tag, which means nobody wrote release notes for what is in
it. Deploying it would put changes into production that are described nowhere.

Cut a release instead - it takes about thirty seconds and writes the note as a
side effect:

  scripts/release.sh <version>     # see CONTRIBUTING.md for the level
  git push && git push --tags
  sudo git pull                    # on the server, then run this again

There is no --force. If this is urgent, it is a PATCH release, not an
exception - the note matters most for the changes that felt too urgent to
write one for.

EOF
  exit 1
fi

SHA=$(git rev-parse HEAD)

if [ "$EXPORT" = true ]; then
  # Consumed as: eval "$(scripts/deploy-gate.sh --export)"
  # docker-compose.yml passes both through to the image as build args, so
  # `sentinel version` can report what it was built from rather than assuming.
  printf 'export GIT_SHA=%s\n' "$SHA"
  printf 'export GIT_TAG=%s\n' "$TAG"
  exit 0
fi

echo "deploy-gate: OK - HEAD is $TAG ($(git rev-parse --short HEAD))"
