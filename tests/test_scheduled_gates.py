"""#86: the scheduled gates' failure reporting must tell a regression from a
broken gate - in the issue title.

The defect this pins against recurring: CI filed "Mutation score dropped
below the floor" on ANY non-zero exit, including "no mutants ran" - sending
someone hunting a lost test when the gate itself was broken. The classifier
lives in scripts/scheduled-gates-report.sh precisely so it can be
execution-tested here with synthetic logs (DRY_RUN prints would-file titles
instead of touching GitHub, so these run offline and deterministically).

The vocabulary tests hold three files together: the strings the classifier
greps must be strings the gate scripts actually emit, in both directions of
the usual one-directional-containment trap - a classifier keyed on a phrase
an emitter stopped printing classifies everything as "no result" forever,
which is quieter than wrong titles and worse.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
REPORT = ROOT / "scripts" / "scheduled-gates-report.sh"
PROBE_TOOL = ROOT / "scripts" / "mutation-probe.py"
MUTATION_TOOL = ROOT / "scripts" / "mutation-check.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

PROBE_COUNT = len(json.loads((ROOT / "scripts" / "probes" / "design-guards.json").read_text()))

REGRESSION_PROBES = "Design-guard probe no longer killed by any test"
NORESULT_PROBES = "Design-guard probes produced no result"
REGRESSION_MUTATION = "Mutation score dropped below the floor"
NORESULT_MUTATION = "Mutation gate produced no result"


def _green_probes_log(tmp_path):
    p = tmp_path / "probes.log"
    lines = [f"  ok    killed    (want killed   ) probe {i}" for i in range(PROBE_COUNT)]
    p.write_text("\n".join(lines) + f"\n\n{PROBE_COUNT}/{PROBE_COUNT} probes as expected\n")
    return p


def _green_mutation_log(tmp_path):
    p = tmp_path / "mutation.log"
    p.write_text("Killed 108/110 = 98%\nFloor: 96%\n")
    return p


def _run(tmp_path, probes_log=None, mutation_log=None,
         probes_outcome="success", mutation_outcome="success"):
    env = dict(os.environ)
    env.update({
        "DRY_RUN": "1",
        "PROBES_LOG": str(probes_log or _green_probes_log(tmp_path)),
        "MUTATION_LOG": str(mutation_log or _green_mutation_log(tmp_path)),
        "PROBES_OUTCOME": probes_outcome,
        "MUTATION_OUTCOME": mutation_outcome,
    })
    return subprocess.run(["bash", str(REPORT)], capture_output=True, text=True,
                          env=env, cwd=ROOT, timeout=60)


def _filed(result):
    return [line.removeprefix("WOULD FILE: ")
            for line in result.stdout.splitlines() if line.startswith("WOULD FILE: ")]


# --- the classification ------------------------------------------------------

def test_green_gates_file_nothing_and_exit_zero(tmp_path):
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _filed(result) == []


def test_a_surviving_probe_is_a_regression_not_a_broken_gate(tmp_path):
    log = tmp_path / "probes.log"
    log.write_text(
        "  FAIL  survived  (want killed   ) #67: some guard\n"
        f"\n{PROBE_COUNT - 1}/{PROBE_COUNT} probes as expected\n")
    result = _run(tmp_path, probes_log=log, probes_outcome="failure")
    assert result.returncode == 1
    assert REGRESSION_PROBES in _filed(result)
    assert NORESULT_PROBES not in _filed(result)


def test_a_probe_that_did_not_land_is_a_broken_gate_not_a_regression(tmp_path):
    """A patch that missed never became a question. Titling it as a lost
    test sends someone hunting through the suite for a grip that was never
    released - the exact mislabeling #86 exists to end."""
    log = tmp_path / "probes.log"
    log.write_text(
        "  DID NOT LAND  #41: some guard: pattern matched 0 times\n"
        f"\n{PROBE_COUNT - 1}/{PROBE_COUNT} probes as expected\n")
    result = _run(tmp_path, probes_log=log, probes_outcome="failure")
    assert result.returncode == 1
    assert NORESULT_PROBES in _filed(result)
    assert REGRESSION_PROBES not in _filed(result)


def test_a_mixed_probe_run_files_both_kinds_under_their_own_titles(tmp_path):
    log = tmp_path / "probes.log"
    log.write_text(
        "  FAIL  survived  (want killed   ) #67: some guard\n"
        "  DID NOT LAND  #41: another guard: pattern matched 0 times\n"
        f"\n{PROBE_COUNT - 2}/{PROBE_COUNT} probes as expected\n")
    result = _run(tmp_path, probes_log=log, probes_outcome="failure")
    assert REGRESSION_PROBES in _filed(result)
    assert NORESULT_PROBES in _filed(result)


def test_a_probe_timeout_is_a_broken_gate(tmp_path):
    log = tmp_path / "probes.log"
    log.write_text(
        "  FAIL  timeout   (want killed   ) #58: some slow guard\n"
        f"\n{PROBE_COUNT - 1}/{PROBE_COUNT} probes as expected\n")
    result = _run(tmp_path, probes_log=log, probes_outcome="failure")
    assert NORESULT_PROBES in _filed(result)
    assert REGRESSION_PROBES not in _filed(result)


def test_a_probes_run_with_no_summary_line_is_a_broken_gate(tmp_path):
    """A truncated run prints per-probe lines and dies before the summary.
    Absence of the population statement must read as no-result, never as
    'no failures printed, so fine'."""
    log = tmp_path / "probes.log"
    log.write_text("  ok    killed    (want killed   ) probe 1\n")
    result = _run(tmp_path, probes_log=log, probes_outcome="failure")
    assert NORESULT_PROBES in _filed(result)


def test_a_probes_run_over_the_wrong_population_is_a_broken_gate(tmp_path):
    """N/N green over the wrong N: every probe that ran passed, and probes
    were silently not attempted. Green over a shrunken population is the
    hollow-pass shape, one level up."""
    n = PROBE_COUNT - 3
    log = tmp_path / "probes.log"
    log.write_text(f"\n{n}/{n} probes as expected\n")
    result = _run(tmp_path, probes_log=log, probes_outcome="success")
    assert NORESULT_PROBES in _filed(result)


def test_a_score_below_the_floor_keeps_its_established_title(tmp_path):
    log = tmp_path / "mutation.log"
    log.write_text(
        "Killed 100/110 = 90%\nFloor: 96%\n"
        "Mutation score dropped below the floor. A decision this suite used to pin, it no longer does.\n")
    result = _run(tmp_path, mutation_log=log, mutation_outcome="failure")
    assert REGRESSION_MUTATION in _filed(result)
    assert NORESULT_MUTATION not in _filed(result)


def test_no_mutants_ran_is_a_broken_gate_not_a_dropped_score(tmp_path):
    """THE defect: 'no mutants ran' used to be filed as 'score dropped below
    the floor'. It measured nothing; there is no score to have dropped."""
    log = tmp_path / "mutation.log"
    log.write_text("no mutants ran - this gate measured nothing. Check the log.\n")
    result = _run(tmp_path, mutation_log=log, mutation_outcome="failure")
    assert NORESULT_MUTATION in _filed(result)
    assert REGRESSION_MUTATION not in _filed(result)


def test_a_successful_step_with_no_score_line_is_a_broken_gate(tmp_path):
    """A pass that cannot show its Killed K/T line is a claim, not a
    measurement - the 8-second-cached-pass shape. Step outcome 'success'
    does not outrank the log."""
    log = tmp_path / "mutation.log"
    log.write_text("everything seems fine\n")
    result = _run(tmp_path, mutation_log=log, mutation_outcome="success")
    assert NORESULT_MUTATION in _filed(result)


def test_a_missing_log_is_a_broken_gate_for_that_gate_only(tmp_path):
    result = _run(tmp_path, probes_log=tmp_path / "never-written.log",
                  probes_outcome="cancelled")
    assert NORESULT_PROBES in _filed(result)
    assert REGRESSION_PROBES not in _filed(result)
    assert NORESULT_MUTATION not in _filed(result)


# --- the vocabularies agree, both directions ---------------------------------

def test_the_classifier_greps_strings_the_gate_scripts_actually_emit():
    """The quiet failure: an emitter rewords its message, the classifier's
    grep goes dark, and every future failure classifies as no-result. Each
    phrase the classifier keys on must exist in its emitter's source."""
    probe_src = PROBE_TOOL.read_text()
    mutation_src = MUTATION_TOOL.read_text()
    assert "DID NOT LAND" in probe_src
    assert "probes as expected" in probe_src
    assert "FAIL " in probe_src
    assert "dropped below the floor" in mutation_src
    assert 'Killed ' in mutation_src
    assert "Floor" in mutation_src


def test_the_classifier_still_keys_on_those_strings():
    """The other direction: the classifier must keep grepping them - a
    refactor that drops a grep silently narrows classification."""
    report_src = REPORT.read_text()
    for phrase in ("DID NOT LAND", "probes as expected", "dropped below the floor",
                   "Killed", "Floor"):
        assert phrase in report_src, f"classifier no longer keys on {phrase!r}"


def test_the_workflow_wires_both_gates_and_the_classifier():
    """Registration guard over ci.yml, honest about its limit: Actions
    cannot run here, so execution evidence comes from a real dispatched run
    (recorded on #86). What this pins: the scheduled job still runs BOTH
    gates cold-asserted, lets each produce its own verdict, and reports
    through the tested classifier on always()."""
    wf = WORKFLOW.read_text()
    assert "scheduled-gates:" in wf
    assert "mutation-probe.py scripts/probes/design-guards.json" in wf
    assert "mutation-check.sh --check" in wf
    assert "scheduled-gates-report.sh" in wf
    assert "test ! -d mutants" in wf, "the cold-run proof is gone"
    assert wf.count("continue-on-error: true") >= 2, (
        "each gate must reach its own verdict; an abort reports the second gate as nothing")
    assert "if: always()" in wf, "a timeout kill must still be classified and filed"


def test_the_local_health_check_watches_the_schedules_own_pulse():
    """A cron that silently stops firing reads as permanently green. The
    local health check (and so every release) asserts the scheduled gates
    ran recently - 'did it run', not 'did it pass'."""
    hc = (ROOT / "scripts" / "health-check.sh").read_text()
    assert "scheduled-gates-freshness.sh" in hc
