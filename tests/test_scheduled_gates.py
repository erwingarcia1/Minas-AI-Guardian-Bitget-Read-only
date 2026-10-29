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
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
REPORT = ROOT / "scripts" / "scheduled-gates-report.sh"
PROBE_TOOL = ROOT / "scripts" / "mutation-probe.py"
MUTATION_TOOL = ROOT / "scripts" / "mutation-check.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

PROBE_COUNT = len(json.loads((ROOT / "scripts" / "probes" / "design-guards.json").read_text()))

REGRESSION_PROBES = "Design-guard probe no longer killed by any test"
NORESULT_PROBES = "Design-guard probes produced no result"
REGRESSION_MUTATION = "Mutation score dropped below the floor"
REGRESSION_UNLEDGERED = "Mutation gate found a survivor no test pins"
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


def test_an_unledgered_survivor_is_a_regression_not_a_broken_gate(tmp_path):
    """#110, 2026-10-22: the gate ran, scored 97% against a floor of 96, and
    failed because one survivor was not in the ledger - and the issue said
    the gate "produced no result". It produced exactly one: a decision no
    test pins. The issue carries the survivor and its diff, and not the
    accepted rows."""
    log = tmp_path / "mutation.log"
    log.write_text(
        "SURVIVORS (2):\n  a.x_ok__mutmut_1\n  b.x_new__mutmut_53\n\n"
        "Killed 676/693 = 97%\n\n"
        "Survivor ledger (scripts/mutation-known-survivors.txt):\n"
        "  Survivor ledger: 16 accepted\n"
        "  accepted   1111111111111111  a.x_ok__mutmut_1\n"
        "             sentinel/a.py equivalent: argued\n"
        "  NEW        6117330947849837  b.x_new__mutmut_53\n"
        "             -keep = x if c else d\n"
        "             +keep = x if (c) and False else d\n"
        "\n1 survivor(s) not in scripts/mutation-known-survivors.txt: a decision this suite used to pin, "
        "it no longer does - or a new one nobody pinned.\n"
        "A survivor the ledger does not know is a decision nobody is pinning.\n")
    result = _run(tmp_path, mutation_log=log, mutation_outcome="failure")
    assert _filed(result) == [REGRESSION_UNLEDGERED], result.stdout
    assert "REGRESSION (unledgered survivor)" in result.stdout


def test_the_unledgered_issue_carries_the_survivor_and_not_the_accepted_rows(tmp_path):
    """The body is what someone acts on; prove it by filing for real against
    a stub gh that records what it was given."""
    log = tmp_path / "mutation.log"
    log.write_text(
        "Killed 676/693 = 97%\n"
        "  accepted   1111111111111111  a.x_ok__mutmut_1\n"
        "             sentinel/a.py equivalent: argued\n"
        "  NEW        6117330947849837  b.x_new__mutmut_53\n"
        "             -keep = x if c else d\n"
        "             +keep = x if (c) and False else d\n"
        "1 survivor(s) not in scripts/mutation-known-survivors.txt: a decision nobody pins.\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "gh-calls.txt"
    gh = bin_dir / "gh"
    gh.write_text(f'#!/usr/bin/env bash\nprintf "%s\\0" "$@" >> {record}\nprintf "\\n" >> {record}\n')
    gh.chmod(0o755)
    env = dict(os.environ)
    env.update({"PATH": f"{bin_dir}:{env['PATH']}", "PROBES_LOG": str(_green_probes_log(tmp_path)),
                "MUTATION_LOG": str(log), "PROBES_OUTCOME": "success", "MUTATION_OUTCOME": "failure"})
    env.pop("DRY_RUN", None)
    result = subprocess.run(["bash", str(REPORT)], capture_output=True, text=True, env=env, cwd=ROOT, timeout=60)
    assert result.returncode == 1, result.stderr
    calls = record.read_text()
    assert REGRESSION_UNLEDGERED in calls
    assert "6117330947849837" in calls and "(c) and False" in calls and "Killed 676/693" in calls
    assert "1111111111111111" not in calls and "equivalent: argued" not in calls


def test_a_malformed_ledger_is_a_broken_gate_not_a_regression(tmp_path):
    log = tmp_path / "mutation.log"
    log.write_text("Killed 676/693 = 97%\nsurvivor ledger is malformed: row 3 has no reason\n")
    result = _run(tmp_path, mutation_log=log, mutation_outcome="failure")
    assert _filed(result) == [NORESULT_MUTATION]


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
    survivors_src = (ROOT / "scripts" / "mutation_survivors.py").read_text()
    assert 'survivor(s) not in {LEDGER.relative_to(REPO)}' in survivors_src
    assert 'print(f"  NEW        {digest}  {name}")' in survivors_src
    assert '"survivor ledger is malformed' in survivors_src
    assert 'Killed ' in mutation_src
    assert "Floor" in mutation_src


def test_the_classifier_still_keys_on_those_strings():
    """The other direction: the classifier must keep grepping them - a
    refactor that drops a grep silently narrows classification."""
    report_src = REPORT.read_text()
    for phrase in ("DID NOT LAND", "probes as expected", "dropped below the floor",
                   "Killed", "Floor", "survivor\\(s\\) not in scripts/mutation-known-survivors", "^  NEW "):
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
    # #97 replaced the cold-run assert with a VALIDATED cache: trust now
    # comes from the manifest check, not from paying the rebuild. What must
    # never disappear is the validation - a cache restored without it is
    # the hollow-green shape the old assert existed to kill.
    assert "actions/cache" in wf, "the mutant cache restore is gone (#97)"
    assert "cmp -s mutants/.source-manifest" in wf, (
        "a restored cache is no longer validated against current sources - "
        "that check is what replaced the cold-run proof (#97)")
    assert "rm -rf mutants" in wf, "an invalid cache must be discarded, not run"
    assert wf.count("continue-on-error: true") >= 2, (
        "each gate must reach its own verdict; an abort reports the second gate as nothing")
    assert "if: always()" in wf, "a timeout kill must still be classified and filed"


def test_the_mutation_instrument_is_one_pinned_version_everywhere():
    """mutmut 3.8.0 arrived unpinned on 2026-09-12 and moved the gate's
    population (322 decision points became 400 on an unchanged tree): the
    weekly run went red on 2026-09-14 and stayed red for five weeks over
    code that had not changed. The version CI installs, the version the gate
    refuses to run without, and the version its install hint names must be
    one number, so an upgrade is a commit, never an accident."""
    script = MUTATION_TOOL.read_text()
    pinned = re.search(r"^MUTMUT_VERSION=(\S+)$", script, re.M)
    assert pinned, "mutation-check.sh no longer declares the version it is calibrated for"
    installs = re.findall(r"pip install mutmut(\S*)", WORKFLOW.read_text())
    assert installs, "ci.yml no longer installs mutmut"
    assert installs == [f"=={pinned.group(1)}"] * len(installs), (
        f"ci.yml installs mutmut{installs}, the gate is calibrated for {pinned.group(1)}")
    # The refusal itself, and the hint that names the same number.
    assert '[ "$installed" != "$MUTMUT_VERSION" ]' in script
    assert "pip install mutmut==$MUTMUT_VERSION" in script


def test_the_test_runner_the_instrument_drives_is_capped_where_it_is_installed():
    """#156: pytest 9.2.0 removed the `item._nodeid` mutmut 3.8.0's harness
    reads, CI installed it through the unbounded dev extra, and the mutation
    gate ran no mutants twice. The cap pyproject.toml's dev extra sets and
    the one the gate refuses to run without must be one number."""
    import tomllib

    script = MUTATION_TOOL.read_text()
    cap = re.search(r"^PYTEST_BELOW=(\S+)$", script, re.M)
    assert cap, "mutation-check.sh no longer declares the pytest it can drive"
    dev = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]["dev"]
    [pytest] = [d for d in dev if d.startswith("pytest") and not d.startswith("pytest-")]
    assert f"<{cap.group(1)}" in pytest, f"pyproject.toml pins {pytest!r}, the gate needs pytest<{cap.group(1)}"
    assert "exit 2" in script.split("PYTEST_BELOW=", 1)[1].split("\n\n", 1)[0], "the gate must refuse, not warn"


def test_the_scheduled_job_may_file_the_issues_it_exists_to_file():
    """From 2026-09-14 the report step failed every week with "Resource not
    accessible by integration (repository.issues)": the default token could
    not write issues, so the red reached nobody. The job names what it needs,
    and only that. Execution evidence is the job log's GITHUB_TOKEN
    permissions block on a dispatched run."""
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["scheduled-gates"]
    assert job["permissions"] == {"contents": "read", "issues": "write"}


def test_the_local_health_check_watches_the_schedules_own_pulse():
    """A cron that silently stops firing reads as permanently green. The
    local health check (and so every release) asserts the scheduled gates
    ran recently - 'did it run', not 'did it pass'."""
    hc = (ROOT / "scripts" / "health-check.sh").read_text()
    assert "scheduled-gates-freshness.sh" in hc


# --- the survivor ledger's size, weekly ---------------------------------------

def test_the_weekly_report_carries_the_ledger_size(tmp_path):
    """A count that only ever goes up is noticed in a report; a file that has
    to be opened is not. The row is there whatever the mutation verdict."""
    log = tmp_path / "mutation.log"
    log.write_text("  Survivor ledger: 2 accepted\nKilled 186/188 = 98%\nFloor: 96%\n")
    result = _run(tmp_path, mutation_log=log)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "| survivor ledger | 2 accepted |" in result.stdout


def test_a_gate_that_did_not_state_the_ledger_size_is_reported_as_such(tmp_path):
    result = _run(tmp_path)  # the plain green log carries no ledger line
    assert "| survivor ledger | ? (the gate did not state it) |" in result.stdout


def test_the_report_greps_the_line_the_gate_actually_prints():
    tool = (ROOT / "scripts" / "mutation_survivors.py").read_text()
    assert 'f"  Survivor ledger: {len(ledger)} accepted"' in tool
    assert "Survivor ledger: [0-9]+ accepted" in REPORT.read_text()


def test_freshness_is_not_a_function_of_how_busy_the_week_was(tmp_path):
    """2026-08-29: fifty push runs had landed since the last scheduled run,
    the freshness gate paged through the last fifty runs of ANY event, the
    scheduled run fell off the page, and a release was refused for a gate
    that had run five days earlier. The query must ask the server for the
    scheduled/dispatched runs themselves. A stub gh that answers the
    unfiltered page with pushes only, and the --event query with the real
    scheduled run, tells the two shapes apart."""
    import json
    import os
    import subprocess
    from datetime import datetime, timedelta, timezone

    stubs = tmp_path / "bin"
    stubs.mkdir()
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # The stub answers the way GitHub does when the page is full of pushes:
    # an unfiltered listing yields no scheduled run (the old query's shape),
    # a listing filtered to --event schedule yields the real one.
    (stubs / "gh").write_text(
        "#!/usr/bin/env bash\n"
        "case \"$*\" in\n"
        f"  *'--event schedule'*) printf '%s\\n' '{recent}' ;;\n"
        "  *) printf '' ;;\n"
        "esac\n")
    (stubs / "gh").chmod(0o755)
    assert json.dumps(recent)  # the stamp is what the script parses
    env = dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}")
    result = subprocess.run(["bash", str(ROOT / "scripts" / "scheduled-gates-freshness.sh")],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "5d ago" in result.stdout
