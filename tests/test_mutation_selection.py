"""The mutation gate's test selection is complete by construction (item 6, 2026-09-22).

scripts/mutation-check.sh runs a FIXED list of test files. Twice in one day a
new test file landed outside it: item 2's fleet tests (twelve survivors and
three mutants with the "no tests" mark) and, on 2026-09-08, the
familiar-destination tests. Both were patched by hand. A habit that has to be
remembered twice is a guard that was never written - this is the guard.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts/mutation-check.sh"
EXCLUDED = ROOT / "scripts/mutation-selection-excluded.txt"
MUTATED = ("sentinel.alerts", "sentinel.detectors", "sentinel.config")
_IMPORT = re.compile(r"^\s*(?:from\s+(sentinel(?:\.[\w.]+)?)\s+import|import\s+(sentinel(?:\.[\w.]+)?))", re.M)


def _selection() -> list[str]:
    t = CHECK.read_text()
    block = t[t.index("pytest_add_cli_args_test_selection="):t.index("runner=")]
    return [line.strip() for line in block.splitlines()[1:] if line.strip()]


def _excluded() -> dict[str, str]:
    out = {}
    for line in EXCLUDED.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        path, _, reason = line.partition(":")
        out[path.strip()] = reason.strip()
    return out


def _test_files() -> list[str]:
    return sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py"))


def _imports_a_mutated_module(path: str) -> bool:
    mods = {m[0] or m[1] for m in _IMPORT.findall((ROOT / path).read_text())}
    return any(m.startswith(MUTATED) for m in mods)


def test_every_test_file_is_selected_or_excluded_with_a_reason():
    selected, excluded, files = set(_selection()), _excluded(), _test_files()
    assert len(files) > 100, "the scan found too few test files to be believed"
    unclassified = [f for f in files if f not in selected and f not in excluded]
    assert unclassified == [], (
        f"test file(s) in neither the mutation selection nor the exclusion list: {unclassified} - "
        "add each to scripts/mutation-check.sh's selection, or to scripts/mutation-selection-excluded.txt with a reason")
    assert not (selected & set(excluded)), f"both selected and excluded: {sorted(selected & set(excluded))}"


def test_every_exclusion_has_a_reason_and_names_a_file_that_exists():
    excluded, files = _excluded(), set(_test_files())
    assert excluded, "the exclusion list is empty - the gate would run every test file, which it does not"
    for path, reason in excluded.items():
        assert path in files, f"stale exclusion: {path} does not exist"
        assert len(reason) > 15, f"{path}: an exclusion must say why"


def test_every_selected_file_exists():
    files = set(_test_files())
    missing = [s for s in _selection() if s not in files]
    assert missing == [], f"selected but absent: {missing}"


def test_a_file_that_exercises_a_mutated_module_is_not_excluded_by_the_default_reason():
    """The default reason is "imports nothing under the mutated paths". A
    file that DOES import one and sits in the exclusion list must carry a
    written reason of its own - never the default."""
    for path, reason in _excluded().items():
        if _imports_a_mutated_module(path):
            assert "nothing under the mutated paths" not in reason, (
                f"{path} imports a mutated module and is excluded by the default reason - "
                "select it, or write why it must stay out")


def test_the_gate_names_every_mutant_no_selected_test_reached():
    """An unjudged mutant is not a passed mutant. mutmut marks a mutant in a
    function no selected test calls with 🫥 and judges it against nothing;
    the gate already refused the run on the count (selected N, ran M), and
    since 2026-09-22 it also NAMES them, so the fix is one line away. A
    registration guard over the script's text; the execution side is the
    gate's own count check, proven twice that day."""
    text = CHECK.read_text()
    assert "UNJUDGED" in text and "🫥" in text
    assert text.index("UNJUDGED") < text.index('if [ "$TOTAL" -ne "${#MUTANTS[@]}" ]'), (
        "the unjudged mutants are named BEFORE the count check refuses the run")


def test_the_mutants_copy_carries_the_repo_files_tests_read():
    """17 of the 39 files the selection gained on 2026-09-22 read the repo
    relative to the cwd and could not run inside mutants/ until also_copy
    carried those paths. The list is additive to mutmut's own defaults."""
    text = CHECK.read_text()
    block = text[text.index("also_copy="):text.index("pytest_add_cli_args_test_selection=")]
    for path in ("config", "docs", "scripts", "docker-compose.yml", ".env.example"):
        assert f"\n    {path}\n" in block, f"also_copy is missing {path}"
