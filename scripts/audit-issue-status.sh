#!/usr/bin/env bash
# Audit the `status:` label on every issue against what the code actually is.
#
# Report-only by default; changes nothing. Exits non-zero when it finds drift,
# so it can be wired into CI or a pre-commit hook later.
#
#   scripts/audit-issue-status.sh            # report
#   scripts/audit-issue-status.sh --apply    # also apply the evidenced recommendations
#
# The rules are documented in CONTRIBUTING.md ("Status labels"). The logic lives
# in the Python beside this file rather than in bash: it makes per-issue API
# calls, parses JSON, and is unit-tested in tests/test_issue_status_audit.py.
# A bash reimplementation would be neither.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

command -v gh >/dev/null 2>&1 || {
  echo "gh (GitHub CLI) is required: https://cli.github.com" >&2; exit 2; }
gh auth status >/dev/null 2>&1 || {
  echo "gh is not authenticated. Run: gh auth login" >&2; exit 2; }
command -v git >/dev/null 2>&1 || { echo "git is required" >&2; exit 2; }

python="python3"
[ -x "$here/../.venv/bin/python" ] && python="$here/../.venv/bin/python"

# Run from the repo root so `git log` sees the history and `grep tests/` resolves.
cd "$here/.."
exec "$python" "$here/audit_issue_status.py" "$@"
