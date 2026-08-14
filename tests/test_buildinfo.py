"""`sentinel version`: what is running, and how it knows.

The point of this is to replace trust with verification — "the deploy printed
no errors, so the new image must be serving" is the same assume-rather-than-
verify shape as a poll that stamps success before doing the work. So the thing
worth testing is not that it prints a version; it is that it never prints a
*wrong* one, and says so plainly when it cannot tell.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from sentinel import buildinfo


@pytest.fixture(autouse=True)
def _clear_cache():
    buildinfo.build_info.cache_clear()
    yield
    buildinfo.build_info.cache_clear()


def test_the_build_arg_is_believed_over_the_checkout(monkeypatch):
    """In the image there is no checkout, and in a source tree the checkout
    could disagree with what the image was built from. The value baked in at
    build time is the one that describes the running artefact."""
    monkeypatch.setenv("SENTINEL_GIT_SHA", "a" * 40)
    monkeypatch.setenv("SENTINEL_GIT_TAG", "v9.9.9")

    info = buildinfo.build_info()

    assert info["commit"] == "a" * 40
    assert info["tag"] == "v9.9.9"
    assert info["source"] == "build-arg"


def test_an_unsupplied_commit_reports_unknown_rather_than_guessing(monkeypatch):
    """The failure this exists to prevent: an image confidently naming a commit
    it was not built from. `unknown` is the honest answer and the useful one —
    the same rule health-check.sh follows when it reports an unrun gate as
    skipped rather than as passing."""
    monkeypatch.setenv("SENTINEL_GIT_SHA", "")
    monkeypatch.setenv("SENTINEL_GIT_TAG", "")
    monkeypatch.setattr(buildinfo, "_from_git", lambda *a: None)

    info = buildinfo.build_info()

    assert info["commit"] == "unknown"
    assert info["tag"] == "unknown"
    assert info["source"] == "unknown"
    assert buildinfo.short_commit() == "unknown"


def test_a_source_checkout_falls_back_to_git(monkeypatch):
    monkeypatch.delenv("SENTINEL_GIT_SHA", raising=False)
    monkeypatch.delenv("SENTINEL_GIT_TAG", raising=False)
    monkeypatch.setattr(buildinfo, "_from_git", lambda *a: "b" * 40 if a[0] == "rev-parse" else None)

    info = buildinfo.build_info()

    assert info["commit"] == "b" * 40
    assert info["source"] == "git"


def test_the_version_line_is_short_enough_for_a_footer(monkeypatch):
    monkeypatch.setenv("SENTINEL_GIT_SHA", "c" * 40)
    monkeypatch.setenv("SENTINEL_GIT_TAG", "v1.2.3")

    line = buildinfo.version_line()

    assert line.startswith("v")
    assert "c" * 12 in line
    assert len(line) < 60, line


def test_the_tag_is_not_repeated_when_it_matches_the_version(monkeypatch):
    """`v1.1.0 · abc… (tag v1.1.0)` says the same thing twice."""
    monkeypatch.setenv("SENTINEL_GIT_SHA", "d" * 40)
    monkeypatch.setenv("SENTINEL_GIT_TAG", f"v{buildinfo.build_info()['version']}")
    buildinfo.build_info.cache_clear()

    assert "(tag" not in buildinfo.version_line()


# --- the CLI command ---------------------------------------------------------

def _run_version(env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    import os

    return subprocess.run(
        [sys.executable, "-c", "from sentinel.cli import main; main()", "version"],
        capture_output=True, text=True, env={**os.environ, **env_extra},
    )


def test_version_command_reports_the_commit_it_was_built_from():
    result = _run_version({"SENTINEL_GIT_SHA": "e" * 40, "SENTINEL_GIT_TAG": "v1.2.3"})

    assert result.returncode == 0, result.stderr
    assert "Minas AI Guardian" in result.stdout
    assert "e" * 40 in result.stdout
    assert "v1.2.3" in result.stdout
    assert "build-arg" in result.stdout


def test_version_command_exits_nonzero_when_it_cannot_say(tmp_path):
    """A script checking whether a deploy landed must not read 'unknown' as
    success. Run from a directory with no checkout so the git fallback finds
    nothing either."""
    import os

    result = subprocess.run(
        [sys.executable, "-c", "from sentinel.cli import main; main()", "version"],
        capture_output=True, text=True, cwd=tmp_path,
        env={**os.environ, "SENTINEL_GIT_SHA": "", "SENTINEL_GIT_TAG": "",
             "PATH": "/nonexistent"},
    )

    assert result.returncode == 1
    assert "unknown" in result.stdout
    assert "does not know which commit" in result.stderr
