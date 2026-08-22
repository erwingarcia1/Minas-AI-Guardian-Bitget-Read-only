"""#94: every version tag has a GitHub Release object, checked by a machine.

The drift this gate closes sat unnoticed from v1.1.0 to v1.6.2: tags read
fine from git and were invisible on the Releases page, and the discovery
was the owner looking at a screenshot. The gate fails on any vX.Y.Z tag
with no Release - and, like every gate here, an unanswered question is
never a pass: gh missing, unauthenticated, or unreachable is exit 1.

Non-version tags (working-tree snapshots like pre-autonomous-*) are out of
scope by design; demanding a Release for them would force publishing one
or failing forever.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "scripts" / "release-objects.sh"

# The gate asks gh one question: the tag names of existing Releases. The
# stub answers with $GH_STUB_RELEASES (newline-separated), or fails when it
# is unset - the "GitHub unreachable" shape.
GH_STUB = """#!/usr/bin/env bash
case "$1 $2" in
  "release list") [ -n "${GH_STUB_RELEASES+x}" ] && printf '%s' "$GH_STUB_RELEASES" || exit 1 ;;
  *) exit 1 ;;
esac
"""


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid",
                    "-c", "user.name=t", *args], check=True, capture_output=True, text=True)


@pytest.fixture
def fx(tmp_path):
    repo = tmp_path / "r"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(GATE, repo / "scripts" / "release-objects.sh")
    (repo / "scripts" / "release-objects.sh").chmod(0o755)
    (repo / "x.txt").write_text("x\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial")
    _git(repo, "tag", "v1.0.0")
    _git(repo, "tag", "v1.1.0")
    # The snapshot tag the real repo carries - a tag that is not a release.
    _git(repo, "tag", "pre-autonomous-2026-01-01")

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "gh").write_text(GH_STUB)
    (stubs / "gh").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{stubs}:{env['PATH']}"
    return {"repo": repo, "env": env}


def _gate(fx, releases=None):
    env = dict(fx["env"])
    if releases is not None:
        env["GH_STUB_RELEASES"] = releases
    return subprocess.run([str(fx["repo"] / "scripts" / "release-objects.sh")],
                          capture_output=True, text=True, env=env,
                          cwd=str(fx["repo"]), timeout=30)


def test_a_tag_with_no_release_fails_and_names_it(fx):
    result = _gate(fx, releases="v1.0.0\n")
    assert result.returncode == 1
    assert "v1.1.0" in result.stderr
    assert "no GitHub Release" in result.stderr
    assert "github-release.sh" in result.stderr, "the failure should say how to fix it"


def test_every_version_tag_covered_passes(fx):
    result = _gate(fx, releases="v1.0.0\nv1.1.0\n")
    assert result.returncode == 0, result.stderr
    assert "2 checked" in result.stdout


def test_snapshot_tags_are_not_demanded_a_release(fx):
    """pre-autonomous-* has no Release and never will; the gate must not
    fail forever over it - version tags only."""
    result = _gate(fx, releases="v1.0.0\nv1.1.0\n")
    assert result.returncode == 0, result.stderr
    assert "pre-autonomous" not in result.stdout + result.stderr


def test_gh_failing_to_answer_is_nonzero_not_zero(fx):
    """The unanswered case. A gate that treats 'could not ask' as 'all
    covered' is the silence-as-success shape this repo keeps closing."""
    result = _gate(fx, releases=None)
    assert result.returncode == 1
    assert "not a pass" in result.stderr


def test_gh_missing_entirely_is_nonzero(fx, tmp_path):
    bare = tmp_path / "bare-bin"
    bare.mkdir()
    for tool in ("git", "bash", "dirname", "grep", "cat"):
        real = shutil.which(tool)
        assert real, f"{tool} missing from the host"
        (bare / tool).symlink_to(real)
    env = dict(os.environ)
    env["PATH"] = str(bare)
    result = subprocess.run(["bash", str(fx["repo"] / "scripts" / "release-objects.sh")],
                            capture_output=True, text=True, env=env,
                            cwd=str(fx["repo"]), timeout=30)
    assert result.returncode == 1
    assert "not a pass" in result.stderr


def test_a_repo_with_no_version_tags_is_nonzero(fx):
    """Zero tags checked proves nothing. This repo has nineteen; a gate
    reporting green on an empty list is checking the wrong repo."""
    for tag in ("v1.0.0", "v1.1.0"):
        _git(fx["repo"], "tag", "-d", tag)
    result = _gate(fx, releases="")
    assert result.returncode == 1
    assert "nothing to check is not a pass" in result.stderr
