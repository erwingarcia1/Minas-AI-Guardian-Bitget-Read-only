#!/usr/bin/env bash
# One-command production deploy for Minas AI Guardian. Run on the server:
#
#   ssh ubuntu@minas-guardian
#   sudo /opt/minas-guardian/scripts/deploy.sh
#
# (Plain `scripts/deploy.sh` works too — it re-runs itself under sudo.)
#
# It automates the exact sequence documented in docs/deployment.md "Deploying
# code changes" — pull → deploy gate → build → validate-config → up → wait
# healthy → report version — and decides nothing new; the reasoning for each
# step and its ordering lives in that doc, not here.
#
# Failure semantics worth knowing:
#   - Any failure BEFORE "Switching over" leaves the previous version running
#     untouched: the build and the config check happen off to the side.
#   - A failure AFTER it means the new container came up broken; the script
#     prints its logs, and `restart: unless-stopped` keeps retrying it.
#
# If the deploy gate refuses: your commit has no release tag. That is the
# designed outcome, not a malfunction — cut a release first
# (`scripts/release.sh X.Y.Z`, then `git push && git push --tags`; thirty
# seconds) and run this again. There is no --force, on purpose.
set -euo pipefail

# Everything here needs root: the checkout and the deploy key are root's, and
# the ubuntu user is deliberately not in the docker group (docs/deployment.md).
if [ "$(id -u)" -ne 0 ]; then
  exec sudo -- "$0" "$@"
fi

cd "$(dirname "${BASH_SOURCE[0]}")/.."

trap 'printf "\ndeploy: FAILED — see output above. A failure before \"Switching over\" leaves the previous version running.\n" >&2' ERR

step() { printf '\n== %s\n' "$*"; }

step "Currently running"
docker compose exec -T sentinel sentinel version || true

step "Pulling main"
# --ff-only: the server must never carry local commits or edited tracked
# files. If this refuses, someone changed files on the box — fix it in git,
# never by merging on the server (docs/deployment.md).
git pull --ff-only

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

step "Deployed"
docker compose exec -T sentinel sentinel version
printf '\ndeploy: OK — %s is live\n' "$GIT_TAG"
