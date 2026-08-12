"""`sentinel validate-config [path]` - a pre-deploy gate for a rules file.

Exists to be run against the *server's* live `config/rules.yaml` before an
upgrade, from a shell, with no database and nothing listening. The question it
answers is "will the new schema accept the file that is already on that box",
and the point of answering it beforehand is that the alternative is finding out
during a deploy window.

Exit codes are the contract, because this gets used in a deploy script:

    0  the file is valid
    1  the file was read but the schema rejects it
    2  the file could not be read at all (wrong path, permissions)

1 and 2 are kept apart deliberately: a typo in the *config* and a typo in the
*command* need different reactions, and a deploy script that treats them the
same will happily conclude a missing file is a bad config.
"""

from __future__ import annotations

import sentinel.db
from sentinel.cli import cmd_validate_config, main

GOOD = """
polling:
  fast_loop_seconds: "45"
detectors:
  off_market:
    max_deviation_pct: "10"
"""


class _Args:
    def __init__(self, path=None):
        self.path = path


def _run(capsys, path=None):
    code = cmd_validate_config(_Args(path))
    return code, capsys.readouterr()


# --- the happy path ----------------------------------------------------------

def test_a_valid_file_passes_with_exit_zero(tmp_path, capsys):
    path = tmp_path / "rules.yaml"
    path.write_text(GOOD)

    code, out = _run(capsys, str(path))

    assert code == 0
    assert "OK" in out.out
    assert str(path) in out.out


def test_the_shipped_config_passes(capsys):
    """If the repo's own file didn't pass, the command would be useless for
    telling an operator anything about theirs."""
    code, out = _run(capsys, "config/rules.yaml")
    assert code == 0, out.out


def test_with_no_path_it_checks_the_default_location(capsys):
    code, out = _run(capsys, None)
    assert code == 0
    assert "config/rules.yaml" in out.out


# --- rejection ---------------------------------------------------------------

def test_a_bad_value_exits_one_and_names_the_setting_and_the_value(tmp_path, capsys):
    path = tmp_path / "rules.yaml"
    path.write_text(GOOD.replace('max_deviation_pct: "10"', 'max_deviation_pct: "ten"'))

    code, out = _run(capsys, str(path))

    assert code == 1
    combined = out.out + out.err
    assert "detectors.off_market.max_deviation_pct" in combined
    assert "ten" in combined


def test_a_misspelled_key_is_reported_with_the_near_match(tmp_path, capsys):
    path = tmp_path / "rules.yaml"
    path.write_text(GOOD.replace("max_deviation_pct:", "max_deviaton_pct:"))

    code, out = _run(capsys, str(path))

    assert code == 1
    combined = out.out + out.err
    assert "max_deviaton_pct" in combined
    assert "max_deviation_pct" in combined, "should suggest the key that was meant"


def test_every_problem_is_reported_not_just_the_first(tmp_path, capsys):
    """An operator fixing a production file before a deploy window wants the
    whole list in one pass, not one problem per round trip to the server."""
    path = tmp_path / "rules.yaml"
    path.write_text(
        "polling:\n"
        '  fast_loop_seconds: "nope"\n'
        "detectors:\n"
        "  off_market:\n"
        '    max_deviation_pct: "-1"\n'
        "  fee_anomaly:\n"
        '    ema_alpha: "9"\n'
    )

    code, out = _run(capsys, str(path))
    combined = out.out + out.err

    assert code == 1
    assert "fast_loop_seconds" in combined
    assert "max_deviation_pct" in combined
    assert "ema_alpha" in combined
    assert "3 problem" in combined


def test_malformed_yaml_exits_one_and_says_so(tmp_path, capsys):
    path = tmp_path / "rules.yaml"
    path.write_text("detectors:\n  off_market:\n   max_deviation_pct: [unclosed\n")

    code, out = _run(capsys, str(path))

    assert code == 1
    assert "YAML" in (out.out + out.err)


def test_a_missing_file_exits_two_not_one(tmp_path, capsys):
    """Distinct from a bad config: this is a wrong path, and a deploy script
    that conflates them will report a missing file as a rejected config."""
    code, out = _run(capsys, str(tmp_path / "nope.yaml"))

    assert code == 2
    assert "could not be read" in (out.out + out.err).lower()


def test_the_output_says_what_deploying_anyway_would_do(tmp_path, capsys):
    """The operator's real question is "what happens if I ship this". Since the
    service no longer refuses to start, the answer is specific and worth
    printing rather than leaving them to infer it."""
    path = tmp_path / "rules.yaml"
    path.write_text(GOOD.replace('max_deviation_pct: "10"', 'max_deviation_pct: "ten"'))

    _, out = _run(capsys, str(path))
    combined = (out.out + out.err).lower()

    assert "default" in combined
    assert "critical" in combined


# --- it must not start or touch anything -------------------------------------

def test_it_never_opens_the_database(tmp_path, capsys, monkeypatch):
    """It runs against a production box before a deploy. Opening the database
    would create or migrate the live one as a side effect of a read-only
    question."""
    def explode(*a, **k):
        raise AssertionError("validate-config opened the database")

    monkeypatch.setattr(sentinel.db.Database, "__init__", explode)

    path = tmp_path / "rules.yaml"
    path.write_text(GOOD)
    code, _ = _run(capsys, str(path))

    assert code == 0


def test_it_leaves_no_files_behind(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "rules.yaml"
    path.write_text(GOOD)
    before = set(tmp_path.iterdir())

    _run(capsys, str(path))

    assert set(tmp_path.iterdir()) == before, "validate-config wrote something"


def test_it_is_reachable_as_a_subcommand(tmp_path, capsys, monkeypatch):
    """Wired into the parser, not just importable."""
    path = tmp_path / "rules.yaml"
    path.write_text(GOOD)
    monkeypatch.setattr("sys.argv", ["sentinel", "validate-config", str(path)])

    raised = None
    try:
        main()
    except SystemExit as exc:
        raised = exc.code

    assert raised == 0
    assert "OK" in capsys.readouterr().out
