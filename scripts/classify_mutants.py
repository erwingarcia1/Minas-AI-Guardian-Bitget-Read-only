#!/usr/bin/env python3
"""Selects the mutants worth running: decision points only.

mutmut mutates everything — string literals, SQL casing, log text, numeric
constants. On this codebase that produced 588 survivors across 1,322 mutants,
roughly 60% of them cosmetic. Chasing all of them is a project that stalls; the
signal is in the mutations that change *what the code decides*.

So this reads mutmut's generated `mutants/` tree, diffs each `x_fn__mutmut_N`
against its `x_fn__mutmut_orig`, and keeps only:

  - comparison operators   `<  <=  >  >=  ==  !=`
  - boolean operators      `and  or  not`
  - control flow           `continue  break  return`

and rejects anything whose only change is inside a string or a numeric literal.
Every survivor it reports is a behaviour a test could reasonably be expected to
pin, which is what makes the score worth ratcheting rather than aspirational.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# A method mutant is INDENTED and named `xǁClassǁmethod__mutmut_N`; a
# module-level one is `x_fn__mutmut_N` at column 0. Until 2026-09-22 this
# matched only the second form, so every decision inside a class - the whole
# ContactRouter, the escalator, WatchAlarms, OperatorAlerter - was outside the
# population and the gate's 341 read as the whole thing. The indentation is
# captured so a body ends at the next statement at or above its own level.
MUTANT_DEF = re.compile(r"^(\s*)def (x.+?__mutmut_(\d+|orig))\(")

# What counts as a decision. Word-boundaried so `or` doesn't match "for".
DECISION = re.compile(r"(?<![<>=!])(<=|>=|==|!=|<|>)(?!=)|\b(and|or|not|continue|break|return)\b")
STRING_LITERAL = re.compile(r"(\"\"\".*?\"\"\"|'''.*?'''|\"[^\"]*\"|'[^']*')", re.DOTALL)


def _functions(source: str) -> dict[str, str]:
    """Every mutant function body in a generated module, by name.

    Bounded at the next statement at or above the def's own indentation -
    the next `def`, `class`, decorator or `mutants_...` registration block -
    not at the next mutant. mutmut emits a `mutants_x_fn__mutmut[...] = ...`
    registration block after the last mutant of each function, and the next
    function's `def` doesn't start with `x` - so slicing "until the next
    mutant" swallowed the registration block and the whole following function
    into the final mutant's body, and every decision token in it counted as a
    change. Indented (method) mutants are bounded the same way at their own
    level, so a nested helper deeper than the def stays inside its body."""
    out: dict[str, str] = {}
    name: str | None = None
    indent = 0
    body: list[str] = []
    boundary = re.compile(r"^(\s*)(def |mutants_|@|class )")
    for line in source.splitlines(keepends=True):
        match = MUTANT_DEF.match(line)
        if match:
            if name:
                out[name] = "".join(body)
            name, indent, body = match.group(2), len(match.group(1)), [line]
            continue
        if name:
            edge = boundary.match(line)
            if edge and len(edge.group(1)) <= indent:
                out[name] = "".join(body)
                name, body = None, []
                continue
            body.append(line)
    if name:
        out[name] = "".join(body)
    return out


def _decision_signature(body: str) -> list[str]:
    """The decision tokens in a body, with strings blanked so a mutated
    message can't masquerade as a changed comparison."""
    return DECISION.findall(STRING_LITERAL.sub('""', body))


def decision_point_mutants(mutants_dir: Path, module_prefixes: tuple[str, ...]) -> list[str]:
    selected: list[str] = []
    for path in sorted(mutants_dir.rglob("*.py")):
        rel = path.relative_to(mutants_dir).with_suffix("")
        dotted = ".".join(rel.parts)
        if not dotted.startswith(module_prefixes):
            continue
        source = path.read_text()
        functions = _functions(source)
        for name, body in functions.items():
            if name.endswith("__mutmut_orig"):
                continue
            origin = functions.get(name.rsplit("__mutmut_", 1)[0] + "__mutmut_orig")
            if origin is None:
                continue
            # A decision-point mutation changes the decision tokens; a string or
            # constant mutation leaves them identical.
            if _decision_signature(body) != _decision_signature(origin):
                selected.append(f"{dotted}.{name}")
    return selected


if __name__ == "__main__":
    prefixes = tuple(sys.argv[1:]) or ("sentinel.detectors", "sentinel.alerts")
    for mutant in decision_point_mutants(Path("mutants"), prefixes):
        print(mutant)
