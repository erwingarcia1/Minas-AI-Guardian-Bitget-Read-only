"""#77: the image shipped no scripts/, so v1.4.1's release note promised a
reading the deployment could not take. The fix is per-file COPY lines plus
matching .dockerignore re-includes — two lists in two files that mean the
same thing, which is this repo's recurring one-directional-containment
shape: each direction gets its own assertion, because a COPY without its
re-include fails the build loudly, while a re-include without its COPY is
dead config that reads as coverage.

Honest limit: these are registration guards. The execution guard — the
file actually present in a built image — needs `docker build`, which CI
does not run; the deploy's own `validate-config` step exercises the image,
and the boundary reading itself is the proof on the VM.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _copied_scripts() -> set[str]:
    text = (ROOT / "Dockerfile").read_text()
    return set(re.findall(r"^COPY (scripts/\S+) ", text, re.M))


def _reincluded_scripts() -> set[str]:
    text = (ROOT / ".dockerignore").read_text()
    return set(re.findall(r"^!(scripts/\S+)$", text, re.M))


def test_the_boundary_report_ships_in_the_image():
    assert "scripts/retention-boundary.py" in _copied_scripts()


def test_every_copied_script_is_readmitted_past_dockerignore():
    """scripts/ is excluded wholesale, so a COPY without its `!` line fails
    the build only at build time — on the server, mid-deploy. Catch it here."""
    assert _copied_scripts() <= _reincluded_scripts()


def test_every_readmitted_script_is_actually_copied():
    """The quieter direction: a stale `!` line after a script is renamed
    keeps looking like coverage while shipping nothing."""
    assert _reincluded_scripts() <= _copied_scripts()


def test_every_copied_script_exists():
    for script in _copied_scripts():
        assert (ROOT / script).is_file(), f"{script} is copied but does not exist"
