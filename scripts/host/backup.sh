#!/usr/bin/env bash
# The daily database backup (#85; the owner, 2026-10-27: "daily backups
# that lasts 28 days"). Until this script there was no backup at all: the
# liveness gate had reported the backup mechanism dead since it was built.
#
# What it does, once a day from minas-backup.timer:
#
#   1. SQLite's own backup API copies the live database INSIDE the
#      container (the data lives in the `sentinel-data` volume, at
#      $SENTINEL_DB_PATH). It is a consistent snapshot while the engine
#      keeps writing, which `cp` of the file is not. The copy must pass
#      `PRAGMA integrity_check` or nothing is kept.
#   2. The copy comes out with `docker compose cp`, is compressed, and is
#      written root-only (0600, in a 0700 directory): it holds every
#      customer's contacts, alerts and encrypted keys. The keys stay useless
#      without SENTINEL_MASTER_KEY, which is in .env and NOT in the backup.
#   3. Copies older than KEEP_DAYS are deleted - only files this script
#      names (sentinel-*.db.gz), never anything else put in the directory.
#
# A copy is assembled outside the backup directory and moved in whole, so
# a run that fails half-way leaves nothing the liveness gate could mistake
# for a fresh backup. A failed run exits non-zero (systemd marks the unit
# failed) and the newest backup simply ages: the liveness gate pages once
# it is older than 48 hours.
#
# Paths and knobs are env-overridable so the logic is testable off-host,
# as in liveness.sh.
set -euo pipefail

COMPOSE_FILE="${MINAS_COMPOSE_FILE:-/opt/minas-guardian/docker-compose.yml}"
SERVICE="${MINAS_SERVICE:-sentinel}"
BACKUP_DIR="${MINAS_BACKUP_DIR:-/var/backups/minas-guardian}"
KEEP_DAYS="${MINAS_BACKUP_KEEP_DAYS:-28}"
WORK_PARENT="${MINAS_BACKUP_WORK_DIR:-/var/tmp}"
INSIDE="/tmp/minas-backup.db"  # the copy's path inside the container

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

stamp=$(date -u +%Y-%m-%dT%H%M%SZ)
final="$BACKUP_DIR/sentinel-$stamp.db.gz"
work=$(mktemp -d -p "$WORK_PARENT" minas-backup.XXXXXX)
cleanup() {
  rm -rf "$work"
  compose exec -T "$SERVICE" rm -f "$INSIDE" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# 1. The snapshot, and its integrity check, inside the container.
verdict=$(compose exec -T "$SERVICE" python - "$INSIDE" <<'PY'
import os
import sqlite3
import sys

source = sqlite3.connect(os.environ["SENTINEL_DB_PATH"])
copy = sqlite3.connect(sys.argv[1])
source.backup(copy)
source.close()
print(copy.execute("PRAGMA integrity_check").fetchone()[0])
copy.close()
PY
)
if [ "$verdict" != "ok" ]; then
  echo "backup refused: the copy failed PRAGMA integrity_check: $verdict" >&2
  exit 1
fi

# 2. Out of the container, compressed, root-only, moved in whole.
compose cp "$SERVICE:$INSIDE" "$work/sentinel.db"
gzip -9 "$work/sentinel.db"
gzip -t "$work/sentinel.db.gz"
chmod 600 "$work/sentinel.db.gz"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
mv "$work/sentinel.db.gz" "$final"

# 3. Retention: this script's own files, older than KEEP_DAYS.
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'sentinel-*.db.gz' \
  -mmin +$((KEEP_DAYS * 24 * 60)) -print -delete | sed 's/^/deleted (older than retention): /'

echo "backup written: $final ($(stat -c %s "$final") bytes); kept for $KEEP_DAYS days"
