"""The local hooks, and the thing that makes them worth having (#62).

CI ran red on `main` for ten hours across about ten pushes and nobody noticed.
The standing rule — check `gh run list --limit 1` after every push — closes the
*detection* gap and depends on a human remembering. The hooks close the
*prevention* gap and do not.

The failure mode of a hook is that it drifts from CI: it goes on passing while
checking something narrower, and the first anyone knows is a red build for a
thing the hook was supposed to catch. That is a silent failure of exactly the
kind this project keeps finding, so it gets a test rather than a comment.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "scripts" / "hooks"
CI = (REPO / ".github" / "workflows" / "ci.yml").read_text()


def test_the_hooks_exist_and_are_executable():
    """An installer that copies a non-executable file installs nothing, and
    git says nothing about it."""
    for name in ("pre-commit", "pre-push"):
        hook = HOOKS / name
        assert hook.is_file(), name
        assert os.stat(hook).st_mode & stat.S_IXUSR, f"{name} is not executable"


@pytest.mark.parametrize("command", ["ruff check sentinel tests", "pytest -q"])
def test_the_hooks_run_what_ci_runs(command):
    """The drift test.

    A hook that lints a narrower path than CI, or skips a check CI has, passes
    locally and fails on push — which teaches people that the hook is
    unreliable and that `--no-verify` is the normal way to commit. Both
    commands have to appear in the hooks *and* in the workflow."""
    hook_text = "\n".join((HOOKS / n).read_text() for n in ("pre-commit", "pre-push"))
    binary, _, args = command.partition(" ")

    assert args in hook_text, f"{command} is not run by any hook"
    assert args in CI, f"{command} is not run by CI"


def test_the_installer_installs_both_hooks():
    installer = (REPO / "scripts" / "install-hooks.sh").read_text()

    assert "pre-commit" in installer
    assert "pre-push" in installer


def test_bypassing_is_documented_rather_than_secret():
    """"Bypassing is possible and obvious, not silent." A hook that cannot be
    bypassed blocks an urgent fix and gets uninstalled; one that is bypassed
    quietly is theatre. It is named in the installer output and in the hook's
    own output."""
    installer = (REPO / "scripts" / "install-hooks.sh").read_text()
    pre_push = (HOOKS / "pre-push").read_text()

    assert "--no-verify" in installer
    assert "--no-verify" in pre_push


def test_the_pre_commit_hook_says_what_it_actually_checks():
    """"The hook tests what is being committed, or says clearly that it
    doesn't." It lints the working tree, which differs from the staged
    snapshot when there are unstaged changes — so it says so."""
    text = (HOOKS / "pre-commit").read_text()

    assert "unstaged" in text
    assert "WORKING TREE" in text


def test_a_missing_ruff_does_not_block_a_commit():
    """A contributor who has not installed dev dependencies must not be unable
    to commit. The hook is a guard rail, not a gate — CI is the gate."""
    assert "skipping lint" in (HOOKS / "pre-commit").read_text()
