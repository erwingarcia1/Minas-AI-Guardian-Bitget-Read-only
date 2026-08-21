"""The release gate and the deploy gate, exercised as scripts against real repos.

These enforce "no deployment without release notes" structurally rather than by
convention, so what matters is that they *refuse* — and a refusal is only worth
anything if something proves it happens. Each test below breaks one specific
thing and asserts the script stops for that reason.

Run against throwaway git repos in `tmp_path`, not against this checkout: a
test that creates tags and commits has no business doing it here, and a test
that can only pass in a repo shaped exactly like this one is testing the repo.

One deliberate stub: the temp repos get a `scripts/health-check.sh` that exits
0 (or 1, where that is the thing under test) and a trivial
`tests/test_project_consistency.py`. Running the real twenty-minute gate per
test case would mean nobody ever runs these. The real gates are exercised for
real when a release is actually cut — which is the success path's evidence.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "scripts" / "release.sh"
DEPLOY_GATE = ROOT / "scripts" / "deploy-gate.sh"

CHANGELOG = """\
# Changelog

## [Unreleased]

_Pending customer/ops items (not code): a standing note that is not a release._

### Added
- **A thing that happened.** With a body.

## [1.0.0] — 2026-07-05

First release.

[Unreleased]: https://example.invalid/o/r/compare/v1.0.0...HEAD
[1.0.0]: https://example.invalid/o/r/releases/tag/v1.0.0
"""

PYPROJECT = '[project]\nname = "sentinel"\nversion = "1.0.0"\n'


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal repo shaped like this one: changelog, pyproject, scripts, tag."""
    repo = tmp_path / "r"
    (repo / "scripts").mkdir(parents=True)
    (repo / "tests").mkdir()

    (repo / "CHANGELOG.md").write_text(CHANGELOG)
    (repo / "pyproject.toml").write_text(PYPROJECT)
    # As in the real repo. Without it the stubbed pytest run leaves
    # `tests/__pycache__/` behind and the *next* release refuses for a dirty
    # tree rather than the reason under test.
    (repo / ".gitignore").write_text("__pycache__/\n.venv/\n")
    shutil.copy(RELEASE, repo / "scripts" / "release.sh")
    shutil.copy(DEPLOY_GATE, repo / "scripts" / "deploy-gate.sh")

    # Stubs — see the module docstring for why.
    hc = repo / "scripts" / "health-check.sh"
    hc.write_text("#!/usr/bin/env bash\nexit 0\n")
    hc.chmod(0o755)
    (repo / "tests" / "test_project_consistency.py").write_text("def test_stub():\n    pass\n")

    # release.sh prefers .venv/bin/python; point it at the one running these
    # tests so `python -m pytest` inside the script resolves to a real pytest.
    # A wrapper rather than a symlink: a venv interpreter locates its own root
    # from the path it is invoked through, so a symlink into a different
    # directory produces a python with no site-packages and no pytest.
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    #
    # The shim also swallows `-m pip`: release.sh refreshes the editable
    # install after bumping the version, and a fixture repo that declares
    # itself as `sentinel` must never be installed into the developer's venv —
    # it would replace the real package with an empty one.
    shim = venv_bin / "python"
    shim.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  '-m pip '*) exit 0 ;;\n"
        "esac\n"
        f'exec "{sys.executable}" "$@"\n'
    )
    shim.chmod(0o755)

    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    _git(repo, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    return repo


def _run(repo: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        [str(repo / "scripts" / script), *args],
        capture_output=True, text=True, cwd=repo, env=env,
    )


# --- release.sh refuses ------------------------------------------------------

def test_release_refuses_an_empty_unreleased(repo: Path):
    """The rule the whole design rests on: no release without notes.

    Note what "empty" has to mean. The standing `_Pending customer/ops items_`
    paragraph lives under `[Unreleased]` permanently, so a naive "is the
    section blank" check would never fire and this gate would be decorative.
    Emptiness is the absence of `###` subsections.
    """
    text = (repo / "CHANGELOG.md").read_text()
    start = text.index("### Added")
    end = text.index("## [1.0.0]")
    (repo / "CHANGELOG.md").write_text(text[:start] + text[end:])
    _git(repo, "commit", "-qam", "empty the unreleased section")

    result = _run(repo, "release.sh", "1.1.0")

    assert result.returncode == 1
    assert "[Unreleased] is empty" in result.stderr
    assert "Changed detection behaviour" in result.stderr, "the refusal should say what to write"
    assert _git(repo, "tag", "-l").stdout.strip() == "v1.0.0", "no tag on a refusal"


def test_release_refuses_a_dirty_tree(repo: Path):
    """A release commit carries the release and nothing else."""
    (repo / "stray.txt").write_text("uncommitted\n")

    result = _run(repo, "release.sh", "1.1.0")

    assert result.returncode == 1
    assert "working tree is dirty" in result.stderr
    assert _git(repo, "tag", "-l").stdout.strip() == "v1.0.0"


def test_release_refuses_a_duplicate_tag(repo: Path):
    """v1.0.0 already exists — re-cutting it would move what a released
    version means, and the deploy gate trusts tags to be immutable."""
    result = _run(repo, "release.sh", "1.0.0")

    assert result.returncode == 1
    assert "already exists" in result.stderr


def test_release_refuses_a_failing_health_check(repo: Path):
    """The expensive gate still stops the release, and stops it before any
    file is touched — a refusal must leave the tree as it found it."""
    (repo / "scripts" / "health-check.sh").write_text("#!/usr/bin/env bash\nexit 1\n")
    (repo / "scripts" / "health-check.sh").chmod(0o755)
    _git(repo, "commit", "-qam", "health-check now fails")

    result = _run(repo, "release.sh", "1.1.0")

    assert result.returncode == 1
    assert "health-check failed" in result.stderr
    assert 'version = "1.0.0"' in (repo / "pyproject.toml").read_text()
    assert "## [1.1.0]" not in (repo / "CHANGELOG.md").read_text()


def test_release_refuses_a_version_that_is_not_x_y_z(repo: Path):
    for bad in ("1.1", "v1.1.0", "1.1.0-rc1", "latest"):
        result = _run(repo, "release.sh", bad)
        assert result.returncode == 1, bad
        assert "must be X.Y.Z" in result.stderr, bad


# --- release.sh, when it does proceed ---------------------------------------

def test_release_moves_the_notes_and_leaves_unreleased_ready(repo: Path):
    """The surgery, end to end: heading dated, notes moved, standing note kept
    where it belongs, pyproject bumped, compare links rewritten, annotated tag."""
    result = _run(repo, "release.sh", "1.1.0")
    assert result.returncode == 0, result.stderr

    text = (repo / "CHANGELOG.md").read_text()

    assert "## [1.1.0] — " in text
    assert "A thing that happened" in text.split("## [1.1.0]")[1].split("## [1.0.0]")[0]

    # The standing ops note is carried forward, not released. Releasing it
    # would claim those pending items shipped in 1.1.0.
    unreleased = text.split("## [Unreleased]")[1].split("## [1.1.0]")[0]
    assert "Pending customer/ops items" in unreleased
    assert "### " not in unreleased, "the new [Unreleased] should have no content sections"

    assert 'version = "1.1.0"' in (repo / "pyproject.toml").read_text()
    assert "[Unreleased]: https://example.invalid/o/r/compare/v1.1.0...HEAD" in text
    assert "[1.1.0]: https://example.invalid/o/r/compare/v1.0.0...v1.1.0" in text

    assert "v1.1.0" in _git(repo, "tag", "-l").stdout
    assert _git(repo, "cat-file", "-t", "v1.1.0").stdout.strip() == "tag", "must be annotated"


def test_a_second_release_is_refused_until_there_are_new_notes(repo: Path):
    """The loop closes: cutting a release empties [Unreleased], so the next
    one cannot be cut until somebody writes something."""
    assert _run(repo, "release.sh", "1.1.0").returncode == 0

    result = _run(repo, "release.sh", "1.2.0")

    assert result.returncode == 1
    assert "[Unreleased] is empty" in result.stderr


def test_release_refreshes_the_install_before_checking_consistency():
    """Pins the bug the first real release hit.

    `sentinel.__version__` comes from installed metadata on purpose — a copy
    inside the package would be a third place to disagree with pyproject.toml.
    The consequence is that bumping the version leaves an editable install
    still reporting the old one, so `test_the_importable_version_matches_the_
    package` fails on a developer machine no matter how correct the surgery
    was. release.sh refused to tag, which was the right call and a useless
    release.

    Asserted as an ordering rather than behaviour because the fixture repos
    stub `pip` out — they declare themselves as `sentinel`, and letting them
    install would replace the real package with an empty one.
    """
    script = RELEASE.read_text()
    refresh = script.index("pip install -e .")
    check = script.index("pytest tests/test_project_consistency.py")
    assert refresh < check, "the install must be refreshed before the versions are compared"


# --- the deploy gate ---------------------------------------------------------

def test_deploy_gate_refuses_an_untagged_commit(repo: Path):
    """The part that does the enforcing. An untagged commit is one nobody
    wrote notes for; the deploy stops."""
    (repo / "code.py").write_text("# shipped without a release\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "an ordinary commit")

    result = _run(repo, "deploy-gate.sh")

    assert result.returncode == 1
    assert "REFUSED" in result.stderr
    assert "not a release" in result.stderr
    assert "v1.0.0" in result.stderr, "should say which release production is behind"


def test_deploy_gate_accepts_a_tagged_commit(repo: Path):
    result = _run(repo, "deploy-gate.sh")

    assert result.returncode == 0, result.stderr
    assert "v1.0.0" in result.stdout


def test_deploy_gate_offers_no_force_flag(repo: Path):
    """Stated as a test because it is the most likely thing for a future
    reader to add during an incident, when it will look justified.

    Asserted behaviourally. An earlier version of this also scanned the
    script's source for the string `--force`, stripping the prose that
    explains its absence — which made rewording a comment a test failure, and
    told you nothing the checks below don't.
    """
    for flag in ("--force", "-f", "--yes"):
        result = _run(repo, "deploy-gate.sh", flag)
        assert result.returncode == 2, flag
        assert "unknown argument" in result.stderr, flag


def test_deploy_gate_export_emits_the_build_args(repo: Path):
    """What makes `sentinel version` able to name the running commit."""
    result = _run(repo, "deploy-gate.sh", "--export")

    assert result.returncode == 0, result.stderr
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert f"export GIT_SHA={head}" in result.stdout
    assert "export GIT_TAG=v1.0.0" in result.stdout


def test_deploy_gate_exports_nothing_when_it_refuses(repo: Path):
    """--export must not become the way around the gate."""
    (repo / "code.py").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "untagged")

    result = _run(repo, "deploy-gate.sh", "--export")

    assert result.returncode == 1
    assert "export GIT_SHA" not in result.stdout


# --- #93: the slow gates skip ONLY on proven scheduled-CI coverage ------------
#
# release.sh consults scripts/scheduled-gates-cover.sh: exit 0 means fast
# gates only, exit 3 means the scheduled gates FAILED on this code and the
# release refuses outright, anything else - including the script being
# absent or crashing - means the full local health-check, exactly as before.
# The dispatch itself is what these prove; the predicate's own logic is
# proven in tests/test_scheduled_gates_cover.py.

def _recording_health_check(repo: Path) -> Path:
    """A health-check stub that records how it was invoked. The record lives
    OUTSIDE the repo: release.sh refuses a tree the health check dirtied."""
    args_file = repo.parent / "hc-args.txt"
    hc = repo / "scripts" / "health-check.sh"
    hc.write_text(f"#!/usr/bin/env bash\necho \"$@\" > {args_file}\nexit 0\n")
    hc.chmod(0o755)
    return args_file


def _cover_stub(repo: Path, exit_code: int) -> None:
    cover = repo / "scripts" / "scheduled-gates-cover.sh"
    cover.write_text(f"#!/usr/bin/env bash\nexit {exit_code}\n")
    cover.chmod(0o755)


def test_proven_coverage_runs_only_the_fast_gates(repo):
    args_file = _recording_health_check(repo)
    _cover_stub(repo, 0)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "stubs")

    result = _run(repo, "release.sh", "1.1.0")

    assert result.returncode == 0, result.stderr
    assert args_file.read_text().strip() == "--fast"


def test_unproven_coverage_runs_the_full_gates(repo):
    args_file = _recording_health_check(repo)
    _cover_stub(repo, 1)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "stubs")

    result = _run(repo, "release.sh", "1.1.0")

    assert result.returncode == 0, result.stderr
    assert args_file.read_text().strip() == ""  # no --fast: everything, locally


def test_a_missing_cover_script_runs_the_full_gates(repo):
    """Fail closed even against this feature's own absence: an old checkout
    or a broken copy must degrade to the pre-#93 behaviour."""
    args_file = _recording_health_check(repo)
    (repo / "scripts" / "scheduled-gates-cover.sh").unlink(missing_ok=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "stubs")

    result = _run(repo, "release.sh", "1.1.0")

    assert result.returncode == 0, result.stderr
    assert args_file.read_text().strip() == ""


def test_red_scheduled_gates_refuse_the_release_before_any_health_check(repo):
    args_file = _recording_health_check(repo)
    _cover_stub(repo, 3)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "stubs")

    result = _run(repo, "release.sh", "1.1.0")

    assert result.returncode != 0
    assert "FAILED on code this release contains" in result.stderr
    assert not args_file.exists(), "the health check ran despite a known-red tree"
    # And nothing was written: a refusal leaves the tree as it found it.
    assert "1.1.0" not in (repo / "pyproject.toml").read_text()
