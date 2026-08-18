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


def test_the_dockerfile_placeholder_is_not_mistaken_for_an_answer(monkeypatch):
    """`ARG GIT_SHA=unknown` reaches the process as the literal string.

    Found by the CI image-build gate on its first run: an image built with no
    build arg reported `commit unknown` and exited 1 — both right — while
    saying `source build-arg`, claiming a provenance that never happened. The
    exit code was already correct, so nothing behavioural was broken; the lie
    was in the field that exists to say how we know.
    """
    monkeypatch.setenv("SENTINEL_GIT_SHA", "unknown")
    monkeypatch.setenv("SENTINEL_GIT_TAG", "unknown")
    monkeypatch.setattr(buildinfo, "_from_git", lambda *a: None)

    info = buildinfo.build_info()

    assert info["commit"] == "unknown"
    assert info["source"] == "unknown", "a placeholder is not a build arg"


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


# --- the --machine contract with scripts/deploy.sh ---------------------------

def _run_version_machine(env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    import os

    return subprocess.run(
        [sys.executable, "-c", "from sentinel.cli import main; main()",
         "version", "--machine"],
        capture_output=True, text=True, env={**os.environ, **env_extra},
    )


def test_version_machine_emits_exactly_the_lines_deploy_sh_parses():
    """scripts/deploy.sh asserts a deploy landed by grepping this output
    (`sed -n 's/^tag=//p'`). Pinned HERE, on the CLI side, because this is
    the side a reformat would happen on: without this test, a change to the
    output would make every future deploy die at its last step with a false
    "wrong image is serving". The human block is deliberately not pinned —
    freeing it to change is the point of having a machine form."""
    import re

    result = _run_version_machine(
        {"SENTINEL_GIT_SHA": "e" * 40, "SENTINEL_GIT_TAG": "v1.2.3"})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert [line.split("=", 1)[0] for line in lines] == [
        "version", "commit", "tag", "source"]
    values = dict(line.split("=", 1) for line in lines)
    assert values["commit"] == "e" * 40
    assert values["tag"] == "v1.2.3"
    assert values["source"] == "build-arg"
    for line in lines:  # each line must survive `sed -n "s/^key=//p"` intact
        assert re.fullmatch(r"[a-z]+=\S+", line), f"unparseable line: {line!r}"


def test_version_machine_still_exits_nonzero_when_it_cannot_say(tmp_path):
    """Same rule as the human form: a build that cannot say what it is must
    not hand a zero exit to a script checking whether the deploy landed."""
    import os

    result = subprocess.run(
        [sys.executable, "-c", "from sentinel.cli import main; main()",
         "version", "--machine"],
        capture_output=True, text=True, cwd=tmp_path,
        env={**os.environ, "SENTINEL_GIT_SHA": "", "SENTINEL_GIT_TAG": "",
             "PATH": "/nonexistent"},
    )

    assert result.returncode == 1
    assert "commit=unknown" in result.stdout


def test_deploy_sh_actually_consumes_the_machine_form():
    """The contract's other direction (one-directional containment, again):
    the format pin above is only worth keeping while deploy.sh reads this
    form. If the consumer ever moves off it, this fails and both halves get
    reconsidered together instead of the pin outliving its purpose."""
    from pathlib import Path

    text = (Path(__file__).parent.parent / "scripts" / "deploy.sh").read_text()
    assert "sentinel version --machine" in text
    assert 'sed -n "s/^$1=//p"' in text
