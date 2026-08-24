#!/usr/bin/env bash
# Durable archive for the mutation gate's expensive artifact (#97).
#
#   scripts/mutant-cache.sh save    <key>                  # mutants/ -> archive
#   scripts/mutant-cache.sh restore <key> <manifest-file>  # archive -> mutants/, validated
#
# `mutants/` holds the generated population, the test-to-mutant stats, AND
# mutmut's per-mutant results - losing it to a `git clean`, a fresh clone,
# or CI's fresh checkout costs 1.5-3 hours of honest rebuild. This keeps
# the newest archives under ~/.cache (outside the tree, so nothing in the
# repo's lifecycle can destroy them) keyed by a hash of the exact sources
# and gate config they were built from.
#
# THE RULE THAT MAKES THIS SAFE (#97's own words: a durable wrong cache is
# worse than a slow honest one): restore VALIDATES. The archive must carry
# a .source-manifest, and it must byte-match the manifest computed from the
# CURRENT sources - the same check mutation-check.sh applies to any
# mutants/ it finds. A mismatched or manifest-less archive is deleted from
# disk and the restore fails, forcing the honest rebuild. The key already
# encodes the sources, so a mismatch means corruption or a hash collision -
# either way, never trusted.
#
# Exit codes: 0 done; 2 usage; 3 nothing restorable (no archive, or the
# archive failed validation) - callers fall through to regeneration.
set -euo pipefail

CACHE_DIR="${MINAS_MUTANT_CACHE_DIR:-$HOME/.cache/minas-guardian/mutants}"
KEEP="${MINAS_MUTANT_CACHE_KEEP:-3}"

usage() { echo "usage: mutant-cache.sh save <key> | restore <key> <manifest-file>" >&2; exit 2; }

[ $# -ge 2 ] || usage
CMD="$1"; KEY="$2"
[[ "$KEY" =~ ^[0-9a-f]{16,64}$ ]] || { echo "mutant-cache: key must be a hex digest, got: $KEY" >&2; exit 2; }
ARCHIVE="$CACHE_DIR/$KEY.tar.gz"

case "$CMD" in
  save)
    [ -d mutants ] || { echo "mutant-cache: no mutants/ to save" >&2; exit 2; }
    [ -f mutants/.source-manifest ] || { echo "mutant-cache: refusing to save a mutants/ with no manifest - unverifiable is unrestorable" >&2; exit 2; }
    mkdir -p "$CACHE_DIR"
    tar -czf "$ARCHIVE.tmp" mutants
    mv "$ARCHIVE.tmp" "$ARCHIVE"
    # Bound the disk: newest $KEEP archives survive. ls -t is fine here -
    # these files are only ever written whole via the rename above.
    ls -t "$CACHE_DIR"/*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while IFS= read -r old; do
      rm -f "$old"
    done
    echo "mutant-cache: saved $(du -h "$ARCHIVE" | cut -f1) as $KEY"
    ;;
  restore)
    MANIFEST="${3:-}"
    [ -n "$MANIFEST" ] && [ -f "$MANIFEST" ] || usage
    [ -f "$ARCHIVE" ] || { echo "mutant-cache: no archive for $KEY" >&2; exit 3; }
    rm -rf mutants
    if ! tar -xzf "$ARCHIVE"; then
      echo "mutant-cache: archive for $KEY is unreadable - deleting it" >&2
      rm -rf mutants "$ARCHIVE"
      exit 3
    fi
    if [ ! -f mutants/.source-manifest ] || ! cmp -s mutants/.source-manifest "$MANIFEST"; then
      echo "mutant-cache: restored archive does NOT match the current sources - a durable" >&2
      echo "wrong cache is worse than a slow honest one. Deleting it and rebuilding." >&2
      rm -rf mutants "$ARCHIVE"
      exit 3
    fi
    echo "mutant-cache: restored $KEY, manifest validated against current sources"
    ;;
  *) usage ;;
esac
