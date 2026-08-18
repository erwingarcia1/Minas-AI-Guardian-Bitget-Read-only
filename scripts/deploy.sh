#!/usr/bin/env bash
# One-command production deploy for Minas AI Guardian. Run on the server:
#
#   ssh ubuntu@minas-guardian
#   sudo /opt/minas-guardian/scripts/deploy.sh            # latest release tag
#   sudo /opt/minas-guardian/scripts/deploy.sh v1.5.0     # that specific tag
#
# (Plain `scripts/deploy.sh` works too — it re-runs itself under sudo.)
#
# It deploys a RELEASE TAG, never a branch tip. The old version pulled main
# and handed HEAD to the deploy gate, which blocked two consecutive deploys
# (v1.4.1, v1.5.0): our own workflow commits the AUTONOMOUS-LOG handoff after
# the tag, so main's tip is routinely one commit past the release and the gate
# — correctly — refused it. Deploying a release means deploying that release,
# so this fetches tags and checks the tag out; what is at the end of the
# branch is irrelevant.
#
# ## The server stays DETACHED at the deployed tag
#
# Deliberate. `git describe --tags` then answers "what is deployed" with
# exactly one name and no "-2-g1234abc" drift suffix, which is the property
# docs/DEPLOYMENTS.md exists to protect. Nothing on the server needs a branch:
# deploys go tag to tag, and the next `git fetch --tags` works fine from a
# detached HEAD (the old `git pull --ff-only` did not — that is what forced
# today's deploy to be typed by hand). To put the clone back on a branch,
# should anyone need to: `sudo git -C /opt/minas-guardian checkout main`.
#
# Note the repo checkout and the running container are separate facts: a
# failure after checkout but before the switchover leaves the repo at the new
# tag while the old version keeps running. That is fine — the gate re-derives
# everything from HEAD on the next run — and it is why DEPLOYMENTS.md measures
# both (`git describe` on disk, `sentinel version` in the container).
#
# Failure semantics worth knowing:
#   - Any failure BEFORE "Switching over" leaves the previous version running
#     untouched: the build and the config check happen off to the side.
#   - A failure AFTER it means the new container came up broken; the script
#     prints its logs, and `restart: unless-stopped` keeps retrying it.
#   - After the switchover the deployed version is ASSERTED, not reported:
#     the container's own `sentinel version` must name the tag this script
#     was asked to deploy, or the script exits non-zero. A deploy that ran
#     the wrong image should fail, not print.
#
# Deploying a tag OLDER than what is running asks for confirmation, typed at
# the terminal. An accidental downgrade of a monitoring product should not be
# one typo away; a deliberate one is sometimes right (rolling back a bad
# release) and stays possible. That read needs a real TTY: a one-liner like
# `ssh host 'sudo … deploy.sh v1.4.2'` allocates none and is refused — use
# `ssh -t` for a rollback typed from your machine (docs/deployment.md).
#
# If the tag you want is missing: cut a release first (`scripts/release.sh
# X.Y.Z`, then `git push && git push --tags`; thirty seconds) and run this
# again. There is no --force, on purpose (scripts/deploy-gate.sh says why).
set -euo pipefail

# Everything here needs root: the checkout and the deploy key are root's, and
# the ubuntu user is deliberately not in the docker group (docs/deployment.md).
if [ "$(id -u)" -ne 0 ]; then
  exec sudo -- "$0" "$@"
fi

cd "$(dirname "${BASH_SOURCE[0]}")/.."

die() { printf 'deploy: %s\n' "$1" >&2; exit 1; }

case "${1:-}" in
  -h|--help) sed -n '2,7p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
esac
[ $# -le 1 ] || die "at most one argument (a release tag, e.g. v1.5.0)"

trap 'printf "\ndeploy: FAILED — see output above. A failure before \"Switching over\" leaves the previous version running.\n" >&2' ERR

step() { printf '\n== %s\n' "$*"; }

# One field ('version' or 'tag') of what the running container reports.
# `sentinel version --machine` (key=value lines) is the contract, pinned by
# tests on the CLI side; images predating the flag fall back to their human
# output, which is safe to parse precisely because those images are already
# built — their format is frozen. Empty output means no container answered.
container_field() {
  local machine
  machine=$(docker compose exec -T sentinel sentinel version --machine 2>/dev/null || true)
  if [ -n "$machine" ]; then
    printf '%s\n' "$machine" | sed -n "s/^$1=//p" | head -n1
    return 0
  fi
  case "$1" in
    version) docker compose exec -T sentinel sentinel version 2>/dev/null | sed -n 's/^Minas AI Guardian //p' | head -n1 || true ;;
    tag)     docker compose exec -T sentinel sentinel version 2>/dev/null | sed -n 's/^  tag  *//p'          | head -n1 || true ;;
  esac
  return 0
}

step "Preflight: clean tree"
# The server must never carry local edits. If this refuses, someone changed
# files on the box — fix it in git, never on the server (docs/deployment.md).
if [ -n "$(git status --porcelain)" ]; then
  echo "deploy: REFUSED — the working tree has local changes:" >&2
  git status --short >&2
  die "revert them (or land them in git and cut a release); never edit on the server"
fi

step "Fetching tags"
git fetch --tags --quiet

# --- Resolve the target tag --------------------------------------------------
# No argument: the latest release by VERSION order (not commit date — an old
# patch tag pushed late must not shadow the real latest). The glob is the
# release-tag shape; the deploy gate re-verifies HEAD is exactly a tag anyway.
if [ $# -eq 1 ]; then
  TARGET_TAG="$1"
  case "$TARGET_TAG" in [0-9]*) TARGET_TAG="v$TARGET_TAG" ;; esac
  [[ "$TARGET_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || die "'$1' is not a release tag (expected vX.Y.Z)"
  git rev-parse -q --verify "refs/tags/$TARGET_TAG" >/dev/null \
    || die "tag $TARGET_TAG does not exist. Releases available (newest first):
$(git tag --list 'v[0-9]*.[0-9]*.[0-9]*' --sort=-version:refname | head -5 | sed 's/^/  /')"
else
  TARGET_TAG=$(git tag --list 'v[0-9]*.[0-9]*.[0-9]*' --sort=-version:refname | head -n1)
  [ -n "$TARGET_TAG" ] || die "no release tags exist — cut one with scripts/release.sh"
fi
TARGET_VERSION="${TARGET_TAG#v}"
echo "deploy: target is $TARGET_TAG$([ $# -eq 0 ] && echo ' (latest release)')"

step "Currently running"
RUNNING_REPORT=$(docker compose exec -T sentinel sentinel version 2>/dev/null || true)
printf '%s\n' "${RUNNING_REPORT:-  (no running container answered)}"
RUNNING_VERSION=$(container_field version)

# --- Downgrade guard ---------------------------------------------------------
# Confirmation comes from the terminal, not stdin, so it cannot be piped past;
# with no terminal a downgrade is refused outright.
if [ -z "$RUNNING_VERSION" ]; then
  echo "deploy: cannot read the running version — downgrade guard skipped (first deploy, or the container is down)"
elif [ "$RUNNING_VERSION" != "$TARGET_VERSION" ] \
  && [ "$(printf '%s\n%s\n' "$RUNNING_VERSION" "$TARGET_VERSION" | sort -V | tail -n1)" = "$RUNNING_VERSION" ]; then
  printf '\ndeploy: WARNING — %s is OLDER than the running %s. This is a downgrade.\n' \
    "$TARGET_TAG" "v$RUNNING_VERSION" >&2
  if ! { [ -r /dev/tty ] && read -r -p "Type 'downgrade' to proceed: " REPLY </dev/tty; } 2>/dev/null; then
    die "downgrade refused: no terminal to confirm on. Run interactively, or deploy v$RUNNING_VERSION or newer."
  fi
  [ "${REPLY:-}" = "downgrade" ] || die "downgrade not confirmed"
fi

step "Checking out $TARGET_TAG (detached — see the header for why)"
git checkout --detach --quiet "refs/tags/$TARGET_TAG"
git log --oneline -1

step "Deploy gate"
scripts/deploy-gate.sh
eval "$(scripts/deploy-gate.sh --export)"   # GIT_SHA/GIT_TAG → image build args

step "Building image ($GIT_TAG)"
docker compose build sentinel

step "Validating the live config against the new image"
docker compose run --rm sentinel sentinel validate-config /app/config/rules.yaml

step "Switching over"
docker compose up -d

step "Waiting for healthy"
# Healthcheck cadence (docker-compose.yml): 20s start period, 30s interval,
# 3 retries. 180s is comfortably past a slow start and well past "broken".
CID=$(docker compose ps -q sentinel)
DEADLINE=$((SECONDS + 180))
while :; do
  STATE=$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$CID")
  case "$STATE" in
    "running healthy")
      break ;;
    "running starting"|"running"|"running ")
      ;;  # still starting
    *)
      echo "deploy: container is '$STATE'" >&2
      docker compose logs --tail 40 sentinel >&2
      exit 1 ;;
  esac
  if [ "$SECONDS" -ge "$DEADLINE" ]; then
    echo "deploy: not healthy after 180s" >&2
    docker compose logs --tail 40 sentinel >&2
    exit 1
  fi
  sleep 5
done

step "Asserting the deployed version"
# Assert, don't report: the running container must name the tag we were asked
# to deploy. The human block is printed for the operator; the comparison uses
# container_field, so a reformat of the human output cannot fail a deploy.
docker compose exec -T sentinel sentinel version || true
DEPLOYED_TAG=$(container_field tag)
if [ "$DEPLOYED_TAG" != "$TARGET_TAG" ]; then
  die "the running container reports tag '${DEPLOYED_TAG:-unknown}', not the $TARGET_TAG this deploy was asked for. The wrong image is serving."
fi

printf '\ndeploy: OK — %s is live and verified\n' "$TARGET_TAG"
