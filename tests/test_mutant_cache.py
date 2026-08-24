"""#97: the mutation gate's cache is durable - and a restored cache MUST
revalidate against current sources, because a durable wrong cache is worse
than a slow honest one. These drive scripts/mutant-cache.sh with fixture
trees; the integration (mutation-check.sh restoring before regenerating,
archiving after a scored run) is observed on the next real health check.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "mutant-cache.sh"
KEY = "a" * 32


@pytest.fixture
def work(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    mutants = tree / "mutants"
    mutants.mkdir()
    (mutants / ".source-manifest").write_text("hash-one  sentinel/a.py\n")
    (mutants / "payload.py").write_text("the expensive artifact\n")
    manifest = tmp_path / "current-manifest"
    manifest.write_text("hash-one  sentinel/a.py\n")
    return {"tree": tree, "cache": tmp_path / "cache", "manifest": manifest}


def _run(work, *args):
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True,
        cwd=work["tree"], timeout=30,
        env={"PATH": "/usr/bin:/bin", "HOME": str(work["tree"]),
             "MINAS_MUTANT_CACHE_DIR": str(work["cache"]),
             "MINAS_MUTANT_CACHE_KEEP": "3"},
    )


def test_save_then_restore_round_trips_outside_the_tree(work):
    assert _run(work, "save", KEY).returncode == 0
    assert (work["cache"] / f"{KEY}.tar.gz").exists(), "the archive lives OUTSIDE the tree"

    # git clean / fresh clone: the tree loses mutants entirely.
    subprocess.run(["rm", "-rf", str(work["tree"] / "mutants")], check=True)
    result = _run(work, "restore", KEY, str(work["manifest"]))
    assert result.returncode == 0, result.stderr
    assert "validated against current sources" in result.stdout
    assert (work["tree"] / "mutants" / "payload.py").read_text() == "the expensive artifact\n"


def test_a_restore_that_does_not_match_current_sources_is_refused_and_destroyed(work):
    """THE #97 rule. The sources moved after the archive was made (or the
    archive lies): restoring it would judge new code by old mutants - the
    exact hollow green the manifest check exists to kill."""
    _run(work, "save", KEY)
    work["manifest"].write_text("hash-two  sentinel/a.py\n")  # sources changed

    result = _run(work, "restore", KEY, str(work["manifest"]))

    assert result.returncode == 3, "the caller must fall through to an honest rebuild"
    assert "worse than a slow honest one" in result.stderr
    assert not (work["tree"] / "mutants").exists(), "the poisoned extraction is removed"
    assert not (work["cache"] / f"{KEY}.tar.gz").exists(), (
        "the lying archive is destroyed, not left to lie again next run")


def test_a_manifest_less_mutants_dir_cannot_be_saved(work):
    (work["tree"] / "mutants" / ".source-manifest").unlink()
    result = _run(work, "save", KEY)
    assert result.returncode == 2
    assert "unverifiable is unrestorable" in result.stderr


def test_a_corrupt_archive_is_deleted_and_falls_through(work):
    work["cache"].mkdir(parents=True)
    (work["cache"] / f"{KEY}.tar.gz").write_text("this is not a tarball")
    result = _run(work, "restore", KEY, str(work["manifest"]))
    assert result.returncode == 3
    assert not (work["cache"] / f"{KEY}.tar.gz").exists()


def test_missing_archive_exits_3_so_callers_regenerate(work):
    assert _run(work, "restore", "b" * 32, str(work["manifest"])).returncode == 3


def test_the_cache_keeps_only_the_newest_three(work):
    import time

    for i in range(5):
        key = str(i) * 32
        _run(work, "save", key)
        time.sleep(0.05)  # distinct mtimes for the prune's ls -t
    kept = sorted(p.name for p in work["cache"].glob("*.tar.gz"))
    assert len(kept) == 3
    assert f"{'0' * 32}.tar.gz" not in kept and f"{'1' * 32}.tar.gz" not in kept


def test_a_malformed_key_is_refused(work):
    assert _run(work, "save", "../../etc/passwd").returncode == 2


def test_mutation_check_wires_restore_before_generation_and_save_after_score():
    """Registration: the integration exists in the gate itself, in the
    right order - restore as the cheap path before the rebuild, save only
    after the completeness guards have accepted the run."""
    text = (SCRIPT.parent / "mutation-check.sh").read_text()
    restore = text.index('mutant-cache.sh restore')
    generate = text.index('Generating mutants')
    guards = text.index('selected ${#MUTANTS[@]} mutants but only')
    save = text.index('mutant-cache.sh save')
    assert restore < generate, "restore must be tried before paying the rebuild"
    assert guards < save, "a partial run must never be archived"
    assert '"$REGENERATE" != true ]' in text, "--regenerate must bypass the cache"
