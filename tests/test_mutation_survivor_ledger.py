"""The mutation gate's survivor ledger (scripts/mutation_survivors.py).

The hole it closes, proven 2026-08-25 on a warm mutants/ and on a #97
restore alike: delete one assertion from a selected test, change nothing
under sentinel/, and the gate re-judges the mutant into a survivor - then
passes anyway, because 98% clears a 96% floor. The ratchet's headroom is
deliberate; what was missing is a hard stop on a survivor nobody has
accepted. These tests pin the ledger's shape and the gate's use of it. The
end-to-end shape (weakened test -> NEW survivor -> exit 1) costs a full
gate run and is recorded in docs/AUTONOMOUS-LOG.md, not run here.
"""
from __future__ import annotations

import importlib.util
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "scripts" / "mutation_survivors.py"
LEDGER = ROOT / "scripts" / "mutation-known-survivors.txt"
GATE = ROOT / "scripts" / "mutation-check.sh"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("mutation_survivors", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DIFF_47 = """# sentinel.alerts.siblings.x_sibling_alert_ids__mutmut_47: survived
--- sentinel/alerts/siblings.py
+++ sentinel/alerts/siblings.py
@@ -28,7 +28,7 @@
     siblings = []
     for candidate in candidates:
         try:
-            if abs(datetime.fromisoformat(candidate["sent_at"]) - anchor) > window:
+            if abs(datetime.fromisoformat(candidate["sent_at"]) - anchor) >= window:
                 continue
"""

DIFF_OFF_MARKET = """# sentinel.detectors.off_market.x_detect_off_market_fills__mutmut_10: survived
--- sentinel/detectors/off_market.py
+++ sentinel/detectors/off_market.py
@@ -14,7 +14,7 @@
         reference = reference_prices.get(symbol) if symbol else None
-        if not reference or reference <= 0:
+        if not reference or reference < 0:
             continue
"""


def test_the_fingerprint_ignores_mutant_numbers_and_line_numbers(tool):
    """Numbering is not stable across regenerations; hunk headers move when
    anything above the mutation is edited. Neither may change the key."""
    a = tool.fingerprint(DIFF_47)
    renumbered = DIFF_47.replace("__mutmut_47", "__mutmut_112").replace("@@ -28,7 +28,7 @@", "@@ -41,7 +41,7 @@")
    assert tool.fingerprint(renumbered) == a
    assert a[1] == "sentinel/alerts/siblings.py"
    assert a[2] == ['-if abs(datetime.fromisoformat(candidate["sent_at"]) - anchor) > window:',
                    '+if abs(datetime.fromisoformat(candidate["sent_at"]) - anchor) >= window:']


def test_a_different_mutation_is_a_different_fingerprint(tool):
    assert tool.fingerprint(DIFF_47)[0] != tool.fingerprint(DIFF_OFF_MARKET)[0]
    assert tool.fingerprint(DIFF_47.replace(">= window", "< window"))[0] != tool.fingerprint(DIFF_47)[0]


def test_the_ledger_holds_exactly_the_two_argued_equivalents(tool):
    ledger = tool.load_ledger(LEDGER)
    assert tool.fingerprint(DIFF_OFF_MARKET)[0] in ledger
    assert len(ledger) == 2
    for reason in ledger.values():
        assert "#70" in reason  # every acceptance names its argument


def test_a_survivor_off_the_ledger_is_news_and_fails_check(tool, tmp_path, monkeypatch):
    """The proven shape: the 24h-edge mutant, once killed, surviving again."""
    survivors = tmp_path / "survivors.txt"
    survivors.write_text("sentinel.alerts.siblings.x_sibling_alert_ids__mutmut_47\n"
                         "sentinel.detectors.off_market.x_detect_off_market_fills__mutmut_10\n")
    diffs = {"sentinel.alerts.siblings.x_sibling_alert_ids__mutmut_47": DIFF_47,
             "sentinel.detectors.off_market.x_detect_off_market_fills__mutmut_10": DIFF_OFF_MARKET}
    monkeypatch.setattr(tool, "show", lambda name: diffs[name])
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        status = tool.main(["x", str(survivors), "--check"])
    assert status == 1
    assert "NEW " in out.getvalue() and "mutmut_47" in out.getvalue()
    assert "+if abs(" in out.getvalue()  # the diff is printed, so the reader sees what unpinned
    assert "accepted" in out.getvalue() and "mutmut_10" in out.getvalue()
    assert "no longer does" in err.getvalue()


def test_without_check_the_same_survivor_is_reported_but_not_fatal(tool, tmp_path, monkeypatch):
    survivors = tmp_path / "survivors.txt"
    survivors.write_text("sentinel.alerts.siblings.x_sibling_alert_ids__mutmut_47\n")
    monkeypatch.setattr(tool, "show", lambda name: DIFF_47)
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert tool.main(["x", str(survivors)]) == 0


def test_only_ledgered_survivors_pass(tool, tmp_path, monkeypatch):
    survivors = tmp_path / "survivors.txt"
    survivors.write_text("sentinel.detectors.off_market.x_detect_off_market_fills__mutmut_10\n")
    monkeypatch.setattr(tool, "show", lambda name: DIFF_OFF_MARKET)
    with redirect_stdout(io.StringIO()):
        assert tool.main(["x", str(survivors), "--check"]) == 0


def test_a_survivor_whose_diff_cannot_be_read_fails_closed(tool, tmp_path, monkeypatch):
    """Unmatched is unverified is unaccepted - the same rule as a manifest
    that cannot be checked."""
    survivors = tmp_path / "survivors.txt"
    survivors.write_text("sentinel.alerts.siblings.x_sibling_alert_ids__mutmut_999\n")

    def broken(name):
        raise RuntimeError("mutmut show failed")

    monkeypatch.setattr(tool, "show", broken)
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        assert tool.main(["x", str(survivors), "--check"]) == 1
    assert "could not be matched" in err.getvalue()


def test_no_survivors_is_a_clean_pass(tool, tmp_path):
    survivors = tmp_path / "survivors.txt"
    survivors.write_text("")
    with redirect_stdout(io.StringIO()):
        assert tool.main(["x", str(survivors), "--check"]) == 0


def test_a_malformed_ledger_line_is_refused(tool, tmp_path):
    bad = tmp_path / "ledger.txt"
    bad.write_text("deadbeefdeadbeef sentinel/x.py\n")  # no reason
    with pytest.raises(ValueError, match="reason"):
        tool.load_ledger(bad)


# --- the gate's use of it -------------------------------------------------------

def test_the_gate_consults_the_ledger_and_fails_on_its_verdict():
    gate = GATE.read_text()
    assert "scripts/mutation_survivors.py" in gate
    assert 'LEDGER_ARGS+=(--check)' in gate
    assert "exit 1" in gate.rsplit("mutation_survivors.py", 1)[1][:600]


def test_a_test_edit_never_forces_a_regeneration():
    """The other half of the requirement: an unrelated test edit must not
    cost a rebuild. The staleness manifest and the cache key hash
    sentinel/ and the gate config - never tests/."""
    gate = GATE.read_text()
    manifest_line = next(line for line in gate.splitlines() if line.startswith("CURRENT=$("))
    assert "find sentinel" in manifest_line and "tests" not in manifest_line
    key_line = next(line for line in gate.splitlines() if line.startswith("CACHE_KEY="))
    assert "tests" not in key_line
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    cache_key = next(line for line in ci.splitlines() if "key: mutants-" in line)
    assert "tests/" not in cache_key


def test_every_named_mutant_is_rejudged_each_run():
    """Why a test manifest is NOT needed: the gate names every selected
    mutant explicitly, and mutmut re-runs a named mutant whatever its cached
    verdict. Pinned here so a future 'optimisation' that drops the names
    (or a mutmut that stops honouring them) is noticed as the cache hole
    it would be."""
    gate = GATE.read_text()
    assert '"$MUTMUT" run --max-children 6 "${MUTANTS[@]}"' in gate
    # mutmut's half: only where it is installed (CI's test job does not
    # have it; the mutation job runs the real gate against the real mutmut).
    mutmut = pytest.importorskip("mutmut", reason="mutmut not installed here; the gate job checks it for real")
    main_src = Path(mutmut.__file__).parent / "__main__.py"
    assert "if not mutant_names and result is not None:" in main_src.read_text()
