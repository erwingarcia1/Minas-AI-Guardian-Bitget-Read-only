#!/usr/bin/env bash
# The liveness gate's host half (#103 item 4). The container can judge the
# database and its own environment; three facts only the HOST knows are
# gathered here and handed in: whether root's crontab schedules the digest,
# how old the newest backup is, whether the restart timer is active. Then
# `sentinel liveness` runs inside the container and exits non-zero on any
# mechanism with no evidence of running.
#
# How the verdict travels: the container pages an operator (Telegram ops
# chat + ops email) - a path proven live independently and not one this
# gate checks. This script adds the network-free path: journald at err and
# a marker in /etc/motd, written ONCE per failing state (#81's loud-once)
# so the next human session trips over it, and cleared when the gate goes
# green. It never reports through the digest or SMS, because those are
# among the things being judged.
#
# Run hourly by minas-liveness.timer (scripts/host/minas-liveness.{service,timer}).
# Paths and knobs are env-overridable so the logic is testable off-host.
set -uo pipefail

COMPOSE_FILE="${MINAS_COMPOSE_FILE:-/opt/minas-guardian/docker-compose.yml}"
SERVICE="${MINAS_SERVICE:-sentinel}"
BACKUP_DIR="${MINAS_BACKUP_DIR:-/var/backups/minas-guardian}"
STATE_FILE="${MINAS_STATE_FILE:-/var/lib/minas-guardian/liveness.state}"
HOST_LOG="${MINAS_HOST_LOG:-/var/log/minas-guardian-liveness.log}"
MOTD_FILE="${MINAS_MOTD_FILE:-/etc/motd}"
MARK="MINAS LIVENESS"

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log_line() {
  mkdir -p "$(dirname "$HOST_LOG")" 2>/dev/null
  printf '%s %s\n' "$(stamp)" "$1" >> "$HOST_LOG"
  logger -t minas-liveness -p "daemon.${2:-info}" -- "$1" || true
}

# --- the three host facts ---------------------------------------------------------------
CRON=no
if sudo crontab -l 2>/dev/null | grep -q 'sentinel digest'; then CRON=yes; fi

AGE=-1
if [ -d "$BACKUP_DIR" ]; then
  NEWEST=$(sudo find "$BACKUP_DIR" -type f -printf '%T@\n' 2>/dev/null | sort -n | tail -1)
  if [ -n "${NEWEST:-}" ]; then AGE=$(( $(date -u +%s) - ${NEWEST%.*} )); fi
fi

TIMER=no
if systemctl is-active --quiet minas-restart-on-unhealthy.timer 2>/dev/null; then TIMER=yes; fi
SELF=no
if systemctl is-active --quiet minas-liveness.timer 2>/dev/null; then SELF=yes; fi

log_line "facts: --crontab-has-digest $CRON --newest-backup-age-seconds $AGE --restart-timer-active $TIMER --liveness-timer-active $SELF"

# --- the gate, inside the container ----------------------------------------------------------
OUT=$(docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" sentinel liveness --page \
        --crontab-has-digest "$CRON" --newest-backup-age-seconds "$AGE" --restart-timer-active "$TIMER" \
        --liveness-timer-active "$SELF" 2>&1)
RC=$?

# To a human at a prompt, "quiet by design" and "broken" are the same
# silence - the exact confusion this product exists to prevent. When stdout
# is a TTY the report is printed; from the timer (no TTY) it stays quiet
# and journald keeps the record, exactly as before. MINAS_FORCE_TTY=1 is
# the test's handle on the interactive branch.
if [ -t 1 ] || [ "${MINAS_FORCE_TTY:-0}" = "1" ]; then
  printf '%s\n' "$OUT"
fi

# The state file holds the FAILING SET itself (sorted DEAD/???? lines), not
# just red/green: red-to-red used to be silent, so at 16:28:21 on 2026-08-31
# the set going 7 -> 6 (this gate's own timer switching on) left nothing in
# journald but the facts line. Any CHANGE in the set is reported now - what
# went dead, what came live - because a gate that never acknowledges
# progress gets ignored.
PREV=""
[ -f "$STATE_FILE" ] && PREV=$(cat "$STATE_FILE")
CUR=$(printf '%s\n' "$OUT" | grep -E '^(DEAD|\?\?\?\?)' | sort || true)
mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null; printf '%s\n' "$CUR" > "$STATE_FILE"

if [ "$RC" -eq 0 ]; then
  if [ -n "$(printf '%s' "$PREV" | tr -d '[:space:]')" ]; then
    log_line "liveness: every declared mechanism is live or held; clearing the marker"
    if [ -f "$MOTD_FILE" ]; then sed -i "/$MARK/d" "$MOTD_FILE" 2>/dev/null || true; fi
  fi
  exit 0
fi

FAILS=$(printf '%s\n' "$CUR" | grep -c . || true)
if [ "$CUR" != "$PREV" ]; then
  NEW_DEAD=$(comm -13 <(printf '%s\n' "$PREV") <(printf '%s\n' "$CUR") | grep -c . || true)
  NOW_LIVE=$(comm -23 <(printf '%s\n' "$PREV") <(printf '%s\n' "$CUR") | grep -c . || true)
  log_line "LIVENESS: $FAILS mechanism(s) built but not running ($NEW_DEAD newly dead, $NOW_LIVE now live) - run 'sentinel liveness' on this host for the switch-on acts" err
  comm -13 <(printf '%s\n' "$PREV") <(printf '%s\n' "$CUR") | while read -r line; do [ -n "$line" ] && log_line "  newly dead: $line" err; done
  comm -23 <(printf '%s\n' "$PREV") <(printf '%s\n' "$CUR") | while read -r line; do [ -n "$line" ] && log_line "  now live:   $line"; done
  mkdir -p "$(dirname "$MOTD_FILE")" 2>/dev/null
  if [ -f "$MOTD_FILE" ]; then sed -i "/$MARK/d" "$MOTD_FILE" 2>/dev/null || true; fi
  printf '*** %s: %s mechanism(s) built but not running (%s). Run: sentinel liveness ***\n' \
    "$MARK" "$FAILS" "$(stamp)" >> "$MOTD_FILE"
fi
exit 1
