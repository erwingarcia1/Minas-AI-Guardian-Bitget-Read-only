"""#93: release.sh may skip the slow gates ONLY when scheduled CI provably
ran them on this exact tree and was green.

The predicate fails CLOSED. Every path it cannot prove - gh missing or
unauthenticated, GitHub unreachable, no covering run, the run's commit not
an ancestor, gate-relevant paths changed since, garbage output - lands on
"run the gates locally". Covered-but-red is its own exit (3): the gates
FAILED on this code, and the caller must refuse rather than re-roll the
dice on a laptop. These tests prove the fallbacks and the refusal, not the
fast path alone - the fast path is the one case that only makes releases
cheaper; the fallbacks are what keep them from getting weaker.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
COVER = ROOT / "scripts" / "scheduled-gates-cover.sh"

GH_STUB = """#!/usr/bin/env bash
case "$1 $2" in
  "auth status") [ "${GH_STUB_AUTH:-ok}" = ok ] || exit 1 ;;
  "run list") [ -f "$GH_STUB_RUNS" ] && cat "$GH_STUB_RUNS" || exit 1 ;;
  *) exit 1 ;;
esac
"""


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid",
                    "-c", "user.name=t", *args], check=True, capture_output=True, text=True)


def _sha(repo, ref="HEAD"):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", ref],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A repo shaped like this one where it matters: gate-relevant paths
    exist and can change, plus a stub gh on PATH the test controls."""
    repo = tmp_path / "r"
    (repo / "scripts" / "probes").mkdir(parents=True)
    (repo / "sentinel").mkdir()
    (repo / "tests").mkdir()
    (repo / "docs").mkdir()
    shutil.copy(COVER, repo / "scripts" / "scheduled-gates-cover.sh")
    (repo / "scripts" / "scheduled-gates-cover.sh").chmod(0o755)
    (repo / "sentinel" / "x.py").write_text("A = 1\n")
    (repo / "tests" / "t.py").write_text("def test_a(): pass\n")
    (repo / "scripts" / "probes" / "p.json").write_text("[]\n")
    (repo / "docs" / "d.md").write_text("docs\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the commit the scheduled gates ran on")

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "gh").write_text(GH_STUB)
    (stubs / "gh").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{stubs}:{env['PATH']}"
    env["GH_STUB_RUNS"] = str(tmp_path / "runs.json")
    return {"repo": repo, "env": env, "runs": tmp_path / "runs.json", "stubs": stubs}


def _runs(fx, *entries):
    fx["runs"].write_text(json.dumps([
        {"headSha": sha, "event": "schedule", "status": "completed", "conclusion": conclusion}
        for sha, conclusion in entries
    ]))


def _cover(fx, env_extra=None):
    env = dict(fx["env"])
    env.update(env_extra or {})
    return subprocess.run([str(fx["repo"] / "scripts" / "scheduled-gates-cover.sh")],
                         capture_output=True, text=True, env=env,
                         cwd=str(fx["repo"]), timeout=30)


# --- the one cheap path -------------------------------------------------------

def test_a_green_run_on_this_exact_commit_covers(repo):
    _runs(repo, (_sha(repo["repo"]), "success"))
    result = _cover(repo)
    assert result.returncode == 0, result.stderr


def test_a_green_run_on_an_ancestor_with_only_doc_changes_since_covers(repo):
    base = _sha(repo["repo"])
    (repo["repo"] / "docs" / "d.md").write_text("docs changed\n")
    _git(repo["repo"], "commit", "-qam", "docs only")
    _runs(repo, (base, "success"))
    result = _cover(repo)
    assert result.returncode == 0, (
        "a docs-only change must not force the slow gates - otherwise the "
        "predicate never fires and this is dead code:\n" + result.stderr)


# --- the fallbacks (fail closed) ----------------------------------------------

def test_a_gate_relevant_change_since_the_run_falls_back(repo):
    base = _sha(repo["repo"])
    (repo["repo"] / "sentinel" / "x.py").write_text("A = 2\n")
    _git(repo["repo"], "commit", "-qam", "sentinel changed")
    _runs(repo, (base, "success"))
    result = _cover(repo)
    assert result.returncode == 1
    assert "locally" in result.stderr


def test_a_run_on_an_unrelated_line_falls_back(repo):
    _git(repo["repo"], "checkout", "-qb", "side")
    (repo["repo"] / "sentinel" / "x.py").write_text("A = 3\n")
    _git(repo["repo"], "commit", "-qam", "divergent")
    side = _sha(repo["repo"])
    _git(repo["repo"], "checkout", "-q", "main")
    _runs(repo, (side, "success"))
    result = _cover(repo)
    assert result.returncode == 1


def test_gh_missing_falls_back(repo, tmp_path):
    """One of the four required proofs - it may not skip. A PATH containing
    only git and python3 guarantees gh is absent regardless of the host."""
    bare = tmp_path / "bare-bin"
    bare.mkdir()
    for tool in ("git", "python3", "bash", "dirname", "cat"):
        real = shutil.which(tool)
        assert real, f"{tool} missing from the host"
        (bare / tool).symlink_to(real)
    env = dict(os.environ)
    env["PATH"] = str(bare)
    result = subprocess.run(["bash", str(repo["repo"] / "scripts" / "scheduled-gates-cover.sh")],
                            capture_output=True, text=True, env=env,
                            cwd=str(repo["repo"]), timeout=30)
    assert result.returncode == 1
    assert "gh is not installed" in result.stderr
    assert "locally" in result.stderr


def test_gh_unauthenticated_falls_back(repo):
    _runs(repo, (_sha(repo["repo"]), "success"))
    result = _cover(repo, {"GH_STUB_AUTH": "no"})
    assert result.returncode == 1


def test_an_unknown_commit_falls_back(repo):
    _runs(repo, ("0" * 40, "success"))
    result = _cover(repo)
    assert result.returncode == 1


def test_garbage_from_gh_falls_back(repo):
    repo["runs"].write_text("this is not json")
    result = _cover(repo)
    assert result.returncode == 1


def test_no_scheduled_runs_at_all_falls_back(repo):
    repo["runs"].write_text("[]")
    result = _cover(repo)
    assert result.returncode == 1


# --- the refusal ---------------------------------------------------------------

def test_a_red_covering_run_refuses_with_its_own_exit(repo):
    """Covered-but-red is not "try locally": the gates already failed on
    this code, and a laptop re-roll is the weakening this change must not
    allow."""
    _runs(repo, (_sha(repo["repo"]), "failure"))
    result = _cover(repo)
    assert result.returncode == 3
    assert "FAILED" in result.stderr


def test_a_newer_red_outvotes_an_older_green_on_the_same_code(repo):
    """The most recent covering run decides. An older green must not rescue
    a tree the latest run failed on."""
    sha = _sha(repo["repo"])
    _runs(repo, (sha, "failure"), (sha, "success"))  # newest first, as gh lists
    result = _cover(repo)
    assert result.returncode == 3
