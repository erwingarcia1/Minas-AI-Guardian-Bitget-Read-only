"""Incremental mutant regeneration, validated per function (scripts/mutant-refresh.py).

The mechanism that produced the 2026-08-23 hollow pass is "mutmut decides
changed by mtime". These tests pin the replacement: changed by MANIFEST
HASH, proven by per-function hash after regeneration, and everything
unmappable refused to the full rebuild. The end-to-end proofs (a weakened
test on a changed function surfaces; unchanged verdicts stay right; the
full path fires) are recorded in docs/AUTONOMOUS-LOG.md against the real
gate.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "scripts" / "mutant-refresh.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("mutant_refresh", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _manifest(path: Path, entries: dict[str, str]) -> Path:
    path.write_text("".join(f"{h}  {p}\n" for p, h in sorted(entries.items())))
    return path


def _fake_mutants(root: Path, sources: dict[str, str], hashes: dict[str, dict[str, str]] | None = None) -> Path:
    mutants = root / "mutants"
    for source in sources:
        copy = mutants / source
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text("# mutated copy\n")
        copy.with_name(copy.name + ".meta").write_text(json.dumps(
            {"exit_code_by_key": {}, "hash_by_function_name": (hashes or {}).get(source, {})}))
        copy.with_name(copy.name + ".spans").write_text("{}")
    (mutants / "mutmut-stats.json").write_text("{}")
    return mutants


# --- plan ------------------------------------------------------------------------

def test_only_the_changed_added_and_removed_files_are_regenerated(tool, tmp_path):
    old = {"sentinel/a.py": _sha("a1"), "sentinel/b.py": _sha("b1"), "sentinel/gone.py": _sha("g")}
    current = {"sentinel/a.py": _sha("a2"), "sentinel/b.py": _sha("b1"), "sentinel/new.py": _sha("n")}
    mutants = _fake_mutants(tmp_path, {"sentinel/a.py": "", "sentinel/b.py": "", "sentinel/gone.py": ""})
    planned = tool.plan(old, current, mutants, patterns=[])
    assert planned == {"changed": ["sentinel/a.py"], "added": ["sentinel/new.py"],
                       "removed": ["sentinel/gone.py"], "unchanged": ["sentinel/b.py"]}
    tool.apply(planned, mutants)
    assert not (mutants / "sentinel/a.py").exists() and not (mutants / "sentinel/a.py.meta").exists()
    assert not (mutants / "sentinel/gone.py").exists()
    assert (mutants / "sentinel/b.py").exists() and (mutants / "sentinel/b.py.meta").exists()
    assert not (mutants / "mutmut-stats.json").exists(), "the test map is always re-collected"


def test_nothing_changed_regenerates_nothing_but_still_recollects_the_map(tool, tmp_path):
    same = {"sentinel/a.py": _sha("a1")}
    mutants = _fake_mutants(tmp_path, {"sentinel/a.py": ""})
    planned = tool.plan(same, same, mutants, patterns=[])
    assert planned["changed"] == planned["added"] == planned["removed"] == []
    tool.apply(planned, mutants)
    assert (mutants / "sentinel/a.py.meta").exists()


def test_an_unchanged_file_with_no_meta_cannot_be_planned(tool, tmp_path):
    """Its verdicts cannot be trusted and its regeneration cannot be proven:
    unmappable goes to the full rebuild, never to a plan."""
    same = {"sentinel/a.py": _sha("a1"), "sentinel/b.py": _sha("b1")}
    mutants = _fake_mutants(tmp_path, {"sentinel/a.py": "", "sentinel/b.py": ""})
    (mutants / "sentinel/b.py.meta").unlink()
    with pytest.raises(RuntimeError, match="cannot be trusted"):
        tool.plan(same, same, mutants, patterns=[])


def test_an_unreadable_manifest_line_is_refused(tool, tmp_path):
    bad = tmp_path / "manifest"
    bad.write_text("notahash  sentinel/a.py\n")
    with pytest.raises(ValueError, match="unreadable"):
        tool.read_manifest(bad)


def test_the_cli_plan_exits_3_on_anything_it_cannot_map(tool, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tool, "MUTANTS", tmp_path / "mutants")
    current = _manifest(tmp_path / "current", {"sentinel/a.py": _sha("a")})
    assert tool.main(["x", "plan", str(tmp_path / "missing-old"), str(current)]) == 3
    assert "no recorded manifest" in capsys.readouterr().err
    bad = tmp_path / "old"
    bad.write_text("garbage\n")
    assert tool.main(["x", "plan", str(bad), str(current)]) == 3


# --- validate --------------------------------------------------------------------

SOURCE_V1 = "def guard(x):\n    if x > 1:\n        return True\n    return False\n"
SOURCE_V2 = "def guard(x):\n    if x >= 1:\n        return True\n    return False\n"


def _hashes_of(source: str) -> dict[str, str]:
    """mutmut's own hash - skipped where mutmut is not installed (CI's test
    job); the gate job runs the real thing against the real population."""
    mutmut = pytest.importorskip("mutmut.mutation.file_mutation",
                                 reason="mutmut not installed here; the gate job validates for real")
    return mutmut.compute_function_hashes(source)


def test_validation_passes_when_every_recorded_hash_matches_the_current_source(tool, tmp_path):
    repo = tmp_path / "repo"
    (repo / "sentinel").mkdir(parents=True)
    (repo / "sentinel/g.py").write_text(SOURCE_V1)
    mutants = _fake_mutants(repo, {"sentinel/g.py": ""}, {"sentinel/g.py": _hashes_of(SOURCE_V1)})
    assert tool.validate({"sentinel/g.py": _sha(SOURCE_V1)}, mutants, repo, patterns=[]) == []


def test_validation_fails_when_the_mutants_are_of_other_code(tool, tmp_path):
    """The hollow pass, as a validation failure: the source moved, the
    copy did not."""
    repo = tmp_path / "repo"
    (repo / "sentinel").mkdir(parents=True)
    (repo / "sentinel/g.py").write_text(SOURCE_V2)
    mutants = _fake_mutants(repo, {"sentinel/g.py": ""}, {"sentinel/g.py": _hashes_of(SOURCE_V1)})
    problems = tool.validate({"sentinel/g.py": _sha(SOURCE_V2)}, mutants, repo, patterns=[])
    assert len(problems) == 1 and "the mutants are of other code" in problems[0]


@pytest.mark.parametrize("missing", ["copy", "meta"])
def test_validation_fails_on_a_missing_artifact(tool, tmp_path, missing):
    repo = tmp_path / "repo"
    (repo / "sentinel").mkdir(parents=True)
    (repo / "sentinel/g.py").write_text(SOURCE_V1)
    mutants = _fake_mutants(repo, {"sentinel/g.py": ""}, {"sentinel/g.py": _hashes_of(SOURCE_V1)})
    (mutants / ("sentinel/g.py" if missing == "copy" else "sentinel/g.py.meta")).unlink()
    problems = tool.validate({"sentinel/g.py": _sha(SOURCE_V1)}, mutants, repo, patterns=[])
    assert len(problems) == 1 and f"no {'mutant copy' if missing == 'copy' else '.meta'}" in problems[0]


def test_a_function_recorded_but_absent_from_the_source_fails(tool, tmp_path):
    repo = tmp_path / "repo"
    (repo / "sentinel").mkdir(parents=True)
    (repo / "sentinel/g.py").write_text("def other():\n    return 1\n")
    mutants = _fake_mutants(repo, {"sentinel/g.py": ""}, {"sentinel/g.py": _hashes_of(SOURCE_V1)})
    problems = tool.validate({"sentinel/g.py": _sha("x")}, mutants, repo, patterns=[])
    assert problems and "ABSENT" in problems[0]


# --- the gate's use of it --------------------------------------------------------

def test_the_gate_plans_then_validates_and_falls_back_to_the_full_rebuild():
    gate = (ROOT / "scripts" / "mutation-check.sh").read_text()
    assert "scripts/mutant-refresh.py plan" in gate
    assert "scripts/mutant-refresh.py validate" in gate
    call = 'scripts/mutant-refresh.py validate "$CURRENT_MANIFEST_FILE"'
    assert call in gate
    after_validate = gate.split(call, 1)[1]
    assert "rm -rf mutants" in after_validate[:1500], "a failed validation must rebuild from scratch"
    # The manifest is written only after validation, never before.
    assert gate.index(call) < gate.rindex("> mutants/.source-manifest")


# --- plain copies: files outside the mutation scope -----------------------------

SCOPE = ["sentinel/alerts/*", "sentinel/config.py"]


def test_a_plain_copy_proves_itself_by_byte_equality(tool, tmp_path):
    """portal.py is copied, never mutated (it is the entire from-scratch
    cost and never in scope). A stale plain copy runs the tests against
    old code - the hollow shape without a mutant in sight - so it is
    checked too, byte for byte."""
    repo = tmp_path / "repo"
    (repo / "sentinel").mkdir(parents=True)
    (repo / "sentinel/portal.py").write_text("VERSION = 2\n")
    mutants = repo / "mutants" / "sentinel"
    mutants.mkdir(parents=True)
    (mutants / "portal.py").write_text("VERSION = 2\n")
    assert tool.validate({"sentinel/portal.py": _sha("VERSION = 2\n")}, repo / "mutants", repo, patterns=SCOPE) == []
    (mutants / "portal.py").write_text("VERSION = 1\n")
    problems = tool.validate({"sentinel/portal.py": _sha("VERSION = 2\n")}, repo / "mutants", repo, patterns=SCOPE)
    assert len(problems) == 1 and "plain copy differs" in problems[0]


def test_a_plain_copy_needs_no_meta_to_be_planned_but_a_mutated_file_does(tool, tmp_path):
    same = {"sentinel/portal.py": _sha("p"), "sentinel/alerts/sms.py": _sha("s")}
    mutants = _fake_mutants(tmp_path, {"sentinel/portal.py": "", "sentinel/alerts/sms.py": ""})
    (mutants / "sentinel/portal.py.meta").unlink()
    assert tool.plan(same, same, mutants, patterns=SCOPE)["unchanged"] == ["sentinel/alerts/sms.py", "sentinel/portal.py"]
    (mutants / "sentinel/alerts/sms.py.meta").unlink()
    with pytest.raises(RuntimeError, match="cannot be trusted"):
        tool.plan(same, same, mutants, patterns=SCOPE)


def test_the_scope_patterns_are_read_from_the_gates_setup_cfg(tool, tmp_path):
    cfg = tmp_path / "setup.cfg"
    cfg.write_text("[mutmut]\nsource_paths=sentinel\nonly_mutate=\n    sentinel/alerts/*\n    sentinel/config.py\n")
    assert tool.mutated_patterns(cfg) == ["sentinel/alerts/*", "sentinel/config.py"]
    assert tool.is_mutated("sentinel/alerts/sms.py", ["sentinel/alerts/*"])
    assert not tool.is_mutated("sentinel/portal.py", ["sentinel/alerts/*"])
    assert tool.is_mutated("sentinel/portal.py", [])  # no patterns: everything is mutated


def test_generation_is_scoped_to_what_the_gate_scores():
    gate = (ROOT / "scripts" / "mutation-check.sh").read_text()
    block = gate.split("only_mutate=", 1)[1].split("pytest_add_cli_args_test_selection", 1)[0]
    assert "sentinel/detectors/*" in block and "sentinel/alerts/*" in block and "sentinel/config.py" in block
    assert "classify_mutants.py sentinel.detectors sentinel.alerts sentinel.config" in gate
