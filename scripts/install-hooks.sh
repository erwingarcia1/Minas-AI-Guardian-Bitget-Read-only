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

# Push over HTTPS, whatever the remote URL says. `git push` over SSH opens the
# connection BEFORE it runs the pre-push hook (it needs the remote refs to
# hand to the hook), and the full suite now outlasts GitHub's SSH idle
# window: the hook passes, then the push dies with exit 141 - "Connection
# to github.com closed by remote host". Seen as a "transient" failure for a
# month before it was read. Over HTTPS the upload is a fresh request after
# the hook finishes, so the hook can take as long as it needs. Repo-local,
# push only: fetch keeps using whatever the remote is configured with, and
# `gh auth setup-git` (or `gh auth login`) supplies the HTTPS credential.
git config --local url."https://github.com/".pushInsteadOf "git@github.com:"

echo "Installed:"
echo "  pre-commit  ruff check (milliseconds)"
echo "  pre-push    ruff check + pytest -q (~9 minutes)"
echo "  push URL    HTTPS for github.com pushes (the hook outlasts SSH's idle window)"
echo
echo "Bypass with --no-verify. It prints a warning when you do; that is the point."
