"""The mutation gate's population includes class methods (#104, 2026-09-22).

scripts/classify_mutants.py selected mutants with `^def (x_...)`: column zero,
underscore after the x. mutmut names a method `xǁClassǁmethod__mutmut_N` and
indents it inside the class, so for as long as the gate existed no method
was in its population - not ContactRouter, not the escalator, not
WatchAlarms, not OperatorAlerter - and "341 decision-point mutants" read as
the whole scope. Measured honest: 538. This is the execution guard: a
generated module with one function and one method, each with a decision
mutant and a cosmetic one, and the classifier must select exactly the two
decisions.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import classify_mutants as cm  # noqa: E402

GENERATED = '''
def x_top__mutmut_orig(a, b):
    if a < b:
        return "lt"
    return "ge"

def x_top__mutmut_1(a, b):
    if a <= b:
        return "lt"
    return "ge"

def x_top__mutmut_2(a, b):
    if a < b:
        return "XXltXX"
    return "ge"

mutants_x_top__mutmut = {"x_top__mutmut_1": x_top__mutmut_1, "x_top__mutmut_2": x_top__mutmut_2}

class Router:
    def xǁRouterǁdecide__mutmut_orig(self, a, b):
        if a and b:
            return 1
        return 0

    def xǁRouterǁdecide__mutmut_1(self, a, b):
        if a or b:
            return 1
        return 0

    def xǁRouterǁdecide__mutmut_2(self, a, b):
        if a and b:
            return 2
        return 0

    mutants_xǁRouterǁdecide__mutmut = {"xǁRouterǁdecide__mutmut_1": xǁRouterǁdecide__mutmut_1}

    def helper(self):
        return None
'''


def _tree(tmp_path: Path) -> Path:
    mod = tmp_path / "mutants" / "sentinel" / "alerts" / "router.py"
    mod.parent.mkdir(parents=True)
    mod.write_text(GENERATED)
    return tmp_path / "mutants"


def test_a_method_decision_mutant_is_selected_beside_the_module_level_one(tmp_path):
    selected = cm.decision_point_mutants(_tree(tmp_path), ("sentinel.alerts",))
    assert selected == [
        "sentinel.alerts.router.x_top__mutmut_1",
        "sentinel.alerts.router.xǁRouterǁdecide__mutmut_1",
    ], "one decision each; the string and constant mutations are not decisions"


def test_a_method_body_ends_at_its_own_indentation_not_at_the_next_column_zero_def(tmp_path):
    """The old parser bounded a body at the next COLUMN-ZERO statement, so an
    indented method's body would have swallowed every method after it; a
    decision token in a later method then counted as a change in this one."""
    functions = cm._functions(GENERATED)
    decide = functions["xǁRouterǁdecide__mutmut_2"]
    assert "return 2" in decide
    assert "def helper" not in decide and "mutants_x" not in decide


def test_the_real_classifier_names_methods_with_the_class_separator():
    """Registration half: the regex the gate runs accepts an indented,
    `xǁClassǁmethod` def. If someone 'simplifies' it back to `^def (x_`, this
    fails before the gate silently shrinks to module-level functions again."""
    assert cm.MUTANT_DEF.match("    def xǁContactRouterǁsend__mutmut_3(self):")
    assert cm.MUTANT_DEF.match("def x_dispatch__mutmut_48(db):")
    assert not cm.MUTANT_DEF.match("    def send(self):")
