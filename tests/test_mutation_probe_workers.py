"""#96: design-guard probes across N workers, each in its own copy of the tree.

The one property that matters is the one that would fail silently: a worker
whose `import sentinel` resolves to the REAL tree runs every probe against
unmutated code. Its kill tests pass, every probe reports `survived`, and the
run reads as a set of design guards that stopped working - or, with
`expect: survived` probes, as guards that work. So the worker proves the
import resolves inside its copy before it touches a probe, and these tests
prove that proof can fail.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "scripts" / "mutation-probe.py"


def _load():
    spec = importlib.util.spec_from_file_location("mutation_probe", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return _load()


# --- the flag ------------------------------------------------------------------

def test_no_flag_is_one_worker_the_original_in_place_path(tool):
    assert tool._parse(["x", "probes.json"]) == (Path("probes.json"), 1)


def test_the_flag_takes_a_count_or_auto(tool):
    assert tool._parse(["x", "--workers", "3", "probes.json"])[1] == 3
    assert tool._parse(["x", "probes.json", "--workers", "3"])[1] == 3
    auto = tool._parse(["x", "probes.json", "--workers", "auto"])[1]
    assert 1 <= auto <= 8


def test_nonsense_worker_counts_are_refused(tool):
    with pytest.raises(ValueError):
        tool._parse(["x", "probes.json", "--workers", "0"])
    with pytest.raises(ValueError):
        tool._parse(["x", "probes.json", "--workers"])
    with pytest.raises(ValueError):
        tool._parse(["x", "probes.json", "extra"])


# --- the copy ------------------------------------------------------------------

def test_the_copy_is_the_tree_without_the_things_that_must_not_be_shared(tool):
    files = set(tool._tree_files())
    assert "sentinel/alerts/siblings.py" in files
    assert "tests/conftest.py" in files
    assert "scripts/probes/design-guards.json" in files
    assert not any(f.startswith((".venv/", "mutants/", ".health-check-logs/")) for f in files)
    assert not any("__pycache__" in f for f in files)


def test_a_worker_proves_its_import_resolves_inside_its_copy(tool, tmp_path):
    root = tmp_path / "worker"
    root.mkdir()
    tool._copy_tree(tool._tree_files(), root)
    tool._prove_isolation(root)  # must not raise


def test_a_copy_that_would_import_the_real_tree_fails_the_proof(tool, tmp_path):
    """Only tests/ copied: `import sentinel` still succeeds - through the
    editable install, from the real tree. That is exactly the worker that
    must refuse to run."""
    root = tmp_path / "hollow"
    root.mkdir()
    tool._copy_tree([f for f in tool._tree_files() if f.startswith("tests/")], root)
    with pytest.raises(RuntimeError, match="not from itself"):
        tool._prove_isolation(root)


# --- the run -------------------------------------------------------------------

TARGET = "sentinel/alerts/siblings.py"

PROBES = [
    {"name": "window edge", "file": TARGET,
     "old": "- anchor) > window:", "new": "- anchor) >= window:",
     "tests": ["tests/test_sibling_acknowledgement.py"], "expect": "killed"},
    {"name": "stale pattern", "file": TARGET,
     "old": "THIS PATTERN DOES NOT EXIST", "new": "x",
     "tests": ["tests/test_sibling_acknowledgement.py"]},
    {"name": "equivalent claim", "file": TARGET,
     "old": "siblings = []", "new": "siblings = list()",
     "tests": ["tests/test_sibling_acknowledgement.py"], "expect": "survived"},
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_workers_keep_every_semantic_and_never_write_the_real_tree(tool, tmp_path):
    probes = tmp_path / "probes.json"
    probes.write_text(json.dumps(PROBES))
    before = _sha(ROOT / TARGET)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        status = tool.main(["mutation-probe.py", str(probes), "--workers", "2"])
    text = out.getvalue()
    assert status == 1, text
    assert "DID NOT LAND  stale pattern" in text
    assert "ok    killed    (want killed   ) window edge" in text
    assert "ok    survived  (want survived ) equivalent claim" in text
    assert "2/3 probes as expected" in text
    assert "not a probe that passed" in err.getvalue()
    assert _sha(ROOT / TARGET) == before
    assert not (ROOT / TARGET).read_text().count(">= window")


def test_a_worker_that_cannot_prove_isolation_fails_the_whole_run(tool, tmp_path, monkeypatch):
    probes = tmp_path / "probes.json"
    probes.write_text(json.dumps(PROBES[:1]))

    def refuse(root):
        raise RuntimeError(f"worker copy at {root} imports sentinel from elsewhere, not from itself")

    monkeypatch.setattr(tool, "_prove_isolation", refuse)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        status = tool.main(["mutation-probe.py", str(probes), "--workers", "2"])
    assert status == 1
    assert "WORKER FAILED" in err.getvalue()
    assert "1 probe(s) never ran" in err.getvalue()
    assert "0/1 probes as expected" in out.getvalue()


def test_the_lock_is_held_in_worker_mode_too(tool, tmp_path):
    """A copy taken while an in-place run has a mutation applied carries
    that mutant into every worker - so the two modes exclude each other."""
    probes = tmp_path / "probes.json"
    probes.write_text(json.dumps(PROBES[:1]))
    held = tool._take_the_lock()
    assert held is not None
    try:
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            status = tool.main(["mutation-probe.py", str(probes), "--workers", "2"])
        assert status == 2
        assert "holds .mutation-probe.lock" in err.getvalue()
    finally:
        held.close()


# --- the wiring ----------------------------------------------------------------

def test_the_health_check_and_ci_fan_the_probes_out():
    hc = (ROOT / "scripts" / "health-check.sh").read_text()
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "scripts/probes/design-guards.json --workers auto" in hc
    assert "scripts/probes/design-guards.json --workers auto" in ci
    assert shutil.which("git") is not None  # the copy is built from git ls-files
