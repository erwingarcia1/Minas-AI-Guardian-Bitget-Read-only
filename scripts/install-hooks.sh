#!/usr/bin/env bash
# Install the local git hooks. One command, and it says what it installed.
#
#   scripts/install-hooks.sh
#
# Deliberately NOT the `pre-commit` tool, despite what #62 suggested. That was
# the right instinct - hand-rolled shell in .git/hooks is not inherited by a
# clone - but it buys hook *management* at the price of a dev dependency that
# downloads and pins its own tool environments on first run. This repository
# has two hooks running two commands that are already dependencies. The
# management layer would be larger than the thing managed.
#
# What matters from that reasoning is that a clone does not silently have no
# hooks. This script is that, plus a line in CONTRIBUTING.md, plus - and this
# is the part a hook manager would not have given us - a CI job that fails if
# the hook and CI ever check different things.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
HOOKS=$(git rev-parse --git-path hooks)
mkdir -p "$HOOKS"

install -m 0755 scripts/hooks/pre-commit "$HOOKS/pre-commit"
install -m 0755 scripts/hooks/pre-push "$HOOKS/pre-push"

echo "Installed:"
echo "  pre-commit  ruff check (milliseconds)"
echo "  pre-push    ruff check + pytest -q (~2 minutes)"
echo
echo "Bypass with --no-verify. It prints a warning when you do; that is the point."
