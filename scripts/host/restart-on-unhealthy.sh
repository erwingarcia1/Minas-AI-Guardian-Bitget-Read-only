#!/usr/bin/env bash
# Restart the sentinel container when its healthcheck says unhealthy (#81,
# decision 12) - the one case `restart: unless-stopped` ignores, because
# Docker's policy acts on exit, not on health. #79 is why this exists: the
# container reported unhealthy 148 consecutive times over six weeks and
# nothing consumed the verdict.
#
# Run by a systemd timer on the HOST (scripts/host/*.timer, every 2 min).
# Deliberately not a docker.sock sidecar: mounting that socket into a
# container hands it root on the host, a large supply-chain surface next to
# a credential-holding product (decision 12).
#
# The cap is the design's heart. A persistent cause makes restart-on-
# unhealthy an infinite loop that LOOKS like uptime - #79's six weeks with
# extra steps. So: at most one restart per CAP_WINDOW; a second unhealthy
# verdict inside the window means the restart did not cure, and the script
# goes LOUD once instead of restarting again - journald at err, the host
# log, and /etc/motd, so the next human session trips over it. A healthy
# verdict resets the state; the motd marker stays until a human removes it,
# because evidence of a flap is evidence.
#
# Every action appends to the host log (DEPLOYMENTS.md style: timestamped,
# with the health state that triggered it) - a restart that fixed something
# silently is a measurement lost.
#
# Paths and knobs are env-overridable so the logic is testable off-host.
set -euo pipefail

COMPOSE_FILE="${MINAS_COMPOSE_FILE:-/opt/minas-guardian/docker-compose.yml}"
SERVICE="${MINAS_SERVICE:-sentinel}"
STATE_FILE="${MINAS_STATE_FILE:-/var/lib/minas-guardian/restart-on-unhealthy.state}"
HOST_LOG="${MINAS_HOST_LOG:-/var/log/minas-guardian-restarts.log}"
MOTD_FILE="${MINAS_MOTD_FILE:-/etc/motd}"
CAP_WINDOW="${MINAS_CAP_WINDOW_SECONDS:-1800}"

now() { date -u +%s; }
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

log_line() {
  # Host log + journald, always both: the log is what a human reviews, the
  # journal is what survives if someone truncates the log.
  mkdir -p "$(dirname "$HOST_LOG")"
  printf '%s %s\n' "$(stamp)" "$1" >> "$HOST_LOG"
  logger -t minas-restart-on-unhealthy -p "daemon.${2:-info}" -- "$1" || true
}

read_state() {
  LAST_RESTART=0; CAPPED=0; KNOWN_CID=""
  if [ -f "$STATE_FILE" ]; then
    # shellcheck disable=SC1090 - values we wrote ourselves
    . "$STATE_FILE"
  fi
}

write_state() {
  mkdir -p "$(dirname "$STATE_FILE")"
  printf 'LAST_RESTART=%s\nCAPPED=%s\nKNOWN_CID=%s\n' "$1" "$2" "${3:-$CID}" > "$STATE_FILE"
}

CID=$(docker compose -f "$COMPOSE_FILE" ps -q "$SERVICE" 2>/dev/null || true)
if [ -z "$CID" ]; then
  # No container is not this script's problem: a stopped service is what
  # `restart: unless-stopped` and deploys govern. Note it and stand down.
  log_line "no running container for service '$SERVICE' - nothing to judge" info
  exit 0
fi

STATUS=$(docker inspect --format '{{.State.Health.Status}}' "$CID" 2>/dev/null || echo "unknown")
read_state

if [ "$STATUS" != "unhealthy" ]; then
  # Only HEALTHY clears the ladder. On 2026-08-30 a deploy's NEW container
  # reported 'starting' two minutes after the cap and the old branch read
  # that as recovery, cleared the state, and bought the flap a second
  # restart the design refuses. "The container recovered" is a healthy
  # verdict; "the container was replaced" is a different id - named as
  # such, and the cap stands until the replacement actually reports
  # healthy. 'starting' and 'unknown' decide nothing either way.
  if [ "$STATUS" = "healthy" ]; then
    if [ "$CAPPED" = "1" ]; then
      log_line "health is 'healthy' again after the restart cap - clearing state (motd marker left for review)" notice
    fi
    { [ "$CAPPED" = "1" ] || [ "$LAST_RESTART" != "0" ]; } && write_state 0 0 "$CID"
  elif [ -n "$KNOWN_CID" ] && [ "$CID" != "$KNOWN_CID" ] && [ "$CAPPED" = "1" ]; then
    log_line "container was REPLACED ($KNOWN_CID -> $CID) while capped; health is '$STATUS' - keeping the cap until it reports healthy" notice
    write_state "$LAST_RESTART" "$CAPPED" "$CID"
  fi
  exit 0
fi

HEALTH_JSON=$(docker inspect --format '{{json .State.Health}}' "$CID" 2>/dev/null || echo '{}')

if [ "$CAPPED" = "1" ]; then
  # Already went loud once. Repeating the noise every 2 minutes would
  # teach whoever reads the log to skim it.
  exit 0
fi

NOW=$(now)
if [ "$LAST_RESTART" != "0" ] && [ $((NOW - LAST_RESTART)) -lt "$CAP_WINDOW" ]; then
  # The restart did not cure it. Mitigation hands off to detection here
  # (decision 12): stop restarting, tell someone - loudly, once. Until #63
  # exists the someone is the next human session, via motd.
  write_state "$LAST_RESTART" 1 "$CID"
  log_line "RESTART CAP REACHED: unhealthy again within ${CAP_WINDOW}s of a restart - NOT restarting again. A human is needed. Health: $HEALTH_JSON" err
  {
    printf '\n*** MINAS GUARDIAN: restart cap reached %s ***\n' "$(stamp)"
    printf '*** The container went unhealthy again within %ss of a restart. ***\n' "$CAP_WINDOW"
    printf '*** See %s and journalctl -t minas-restart-on-unhealthy ***\n' "$HOST_LOG"
    printf '*** Remove this notice from %s once handled. ***\n' "$MOTD_FILE"
  } >> "$MOTD_FILE" || log_line "could not write the motd marker to $MOTD_FILE" err
  exit 0
fi

log_line "container unhealthy - restarting '$SERVICE'. Health: $HEALTH_JSON" warning
if docker compose -f "$COMPOSE_FILE" restart "$SERVICE"; then
  write_state "$NOW" 0 "$CID"
  log_line "restart of '$SERVICE' completed" notice
else
  # A failed restart is worse than an unhealthy container - say so at the
  # same volume as the cap, but do not mark capped: the next tick may
  # succeed, and the cap is for cures that don't hold, not commands that
  # fail.
  log_line "RESTART FAILED for '$SERVICE' - docker compose restart exited non-zero. A human is needed." err
fi
