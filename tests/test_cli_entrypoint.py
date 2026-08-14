"""Where `.env` is loaded, and where it must not be (#68).

`load_dotenv()` mutates `os.environ` for the life of the process and has no
teardown. It used to be the first line of `sentinel.cli.main`, which two tests
call - so running the suite on a machine with a real `.env` handed production
credentials to every test that ran afterwards, and they used them: a live SMTP
connection during a password-reset test, taking 3.07s where the timing
guarantee it exists to measure allows a ratio of 1.5.

Splitting it into `cli_entry` moves the risk from "every test must remember not
to call main()" to "one function that only the installed command reaches". Both
halves need pinning: that `main` no longer loads it, and that something still
does - a fix that quietly stopped reading `.env` at all would pass the first
test and take the service's whole configuration with it.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

from sentinel import cli

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_main_does_not_load_the_environment(monkeypatch, capsys):
    """The guarantee, stated against the real parser rather than by reading the
    source: run a real subcommand through `main` with `load_dotenv` rigged to
    explode, and reach the end."""
    def _explode():
        raise AssertionError("main() loaded .env - #68 is back")

    monkeypatch.setattr(cli, "load_dotenv", _explode)
    monkeypatch.setattr(sys, "argv", ["sentinel", "version"])

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip()


def test_cli_entry_loads_the_environment_before_dispatching(monkeypatch):
    """The other half. Order matters: a command reading SMTP_HOST at import or
    argument-parsing time would see nothing if the load came second."""
    events = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: events.append("load_dotenv"))
    monkeypatch.setattr(cli, "main", lambda: events.append("main"))

    cli.cli_entry()

    assert events == ["load_dotenv", "main"]


def test_the_installed_command_points_at_the_entry_that_loads_it():
    """The failure this catches is silent and total: point `[project.scripts]`
    at `main` and the deployed `sentinel` command reads no `.env`, so it starts
    with no SMTP, no Telegram and no exchange keys - and alerts nobody, looking
    exactly like a quiet night."""
    scripts = tomllib.loads(PYPROJECT.read_text())["project"]["scripts"]

    assert scripts["sentinel"] == "sentinel.cli:cli_entry"
    module, _, attribute = scripts["sentinel"].partition(":")
    assert callable(getattr(__import__(module, fromlist=[attribute]), attribute))


def _load_dotenv_calls(path: Path) -> list[str]:
    """The functions in `path` whose body calls `load_dotenv`.

    Parsed rather than grepped, because this module and `cli.py` both discuss
    `load_dotenv()` at length in prose and a text scan counts the discussion.
    The first version of this test did exactly that and failed on its own
    docstrings, which is the cheap version of the mistake this file is about:
    a check that answers a question next to the one being asked."""
    tree = ast.parse(path.read_text(), filename=str(path))
    callers = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                    and inner.func.id == "load_dotenv"):
                callers.append(node.name)
    return callers


def test_nothing_else_in_the_package_loads_dotenv():
    """One caller, or the split means nothing. A second `load_dotenv()` further
    down a code path a test can reach puts the leak straight back."""
    package = Path(cli.__file__).resolve().parent
    callers = {
        str(path.relative_to(package)): _load_dotenv_calls(path)
        for path in package.rglob("*.py")
        if "__pycache__" not in path.parts and _load_dotenv_calls(path)
    }

    assert callers == {"cli.py": ["cli_entry"]}
