"""G-46 (#50): version numbers agree across the project.

They didn't. `pyproject.toml` said `0.1.0` while `CHANGELOG.md`'s most recent
released section was `[1.0.0] — 2026-07-05` — the package version was simply
never bumped with the releases. Nothing anywhere would have said so, which is
the whole point of the criterion.

This is a test rather than a CI-only script so it fails on the machine of
whoever forgot, at the moment they forgot, rather than three minutes later in a
pipeline they've already stopped watching.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def _changelog_latest_release() -> str:
    """The newest `## [x.y.z]` heading, skipping `[Unreleased]`."""
    for line in (ROOT / "CHANGELOG.md").read_text().splitlines():
        match = re.match(r"^## \[(\d+\.\d+\.\d+)\]", line)
        if match:
            return match.group(1)
    raise AssertionError("CHANGELOG.md has no released version heading")


def test_the_package_version_matches_the_changelog():
    assert _pyproject_version() == _changelog_latest_release(), (
        "pyproject.toml and CHANGELOG.md disagree about the current version. "
        "Bump pyproject when you add a release heading, or the artefact people "
        "install is labelled with a version that shipped months ago."
    )


def test_the_importable_version_matches_the_package():
    """`sentinel.__version__` is derived from installed metadata rather than
    written down a second time — a third hand-maintained copy is a third thing
    to forget. This asserts the derivation still works, since an editable
    install that loses its metadata would silently report 'unknown'."""
    import sentinel

    assert sentinel.__version__ == _pyproject_version()


def test_the_changelog_has_an_unreleased_section():
    """Where in-flight work is written down. Without it, changes land with
    nowhere to be recorded and the habit quietly stops."""
    assert "## [Unreleased]" in (ROOT / "CHANGELOG.md").read_text()


def test_every_changelog_version_has_a_link_definition():
    """The compare links at the bottom. A heading without one renders as plain
    text and the reader can't get to the diff."""
    text = (ROOT / "CHANGELOG.md").read_text()
    headings = set(re.findall(r"^## \[(\d+\.\d+\.\d+)\]", text, re.MULTILINE))
    linked = set(re.findall(r"^\[(\d+\.\d+\.\d+)\]:", text, re.MULTILINE))
    assert headings <= linked, f"CHANGELOG headings with no link definition: {sorted(headings - linked)}"
