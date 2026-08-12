"""What happens when a setting is missing.

The rule this file enforces: **a setting that is absent must behave exactly as
the shipped `config/rules.yaml` says it should.** Anything else is a silent
behaviour change that nobody asked for and nobody sees — the setting is gone,
so there is no line to read and no diff to review.

`alerting.routing` was the loud instance: its fallback was `[]`, which is not a
value but an absence of one, so a pruned routing table meant alerts stored,
deduped and delivered nowhere. The audit that followed found three more, all
quieter and all the same shape — a code default that had simply drifted from
the value the product ships.
"""

from __future__ import annotations

from decimal import Decimal

import yaml

from sentinel.config import RulesConfig
from sentinel.rules_schema import DEFAULTS, SPEC, default_for


def _shipped():
    return yaml.safe_load(open("config/rules.yaml"))


def _same(a, b) -> bool:
    """Value equality, not text equality.

    `Decimal("0.00000001")` renders as `1E-8`; comparing the strings would
    report a mismatch between two identical numbers, which is a false alarm in
    a test whose whole job is to be believed."""
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, list) or isinstance(b, list):
        return list(a) == list(b)
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (ArithmeticError, ValueError, TypeError):
        return str(a) == str(b)


def _walk(node, path=()):
    for key, value in node.items():
        if isinstance(value, dict):
            yield from _walk(value, path + (key,))
        else:
            yield path + (key,), value


# --- the registry is the single source, and it cannot drift ------------------

def test_every_declared_default_matches_the_shipped_config(tmp_path):
    """The anti-drift guard, and the one that would have caught all four.

    A code default that disagrees with the shipped file means an install with
    the setting absent behaves differently from an install with the setting
    present at its documented value - for no reason anyone chose."""
    shipped = _shipped()
    mismatches = []
    for path, declared in _walk(DEFAULTS):
        node = shipped
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        if node is None:
            continue  # not present in the shipped file; nothing to disagree with
        if not _same(node, declared):
            mismatches.append(f"{'.'.join(path)}: code {declared!r} vs shipped {node!r}")
    assert mismatches == [], "code defaults have drifted from config/rules.yaml:\n  " + "\n  ".join(mismatches)


def test_every_setting_in_the_schema_declares_a_default():
    """Forces the question to be answered when a setting is added, rather than
    left to whatever the reader happens to pass - which is how
    `tolerance_pct` ended up defaulting to zero by accident."""
    missing = []

    def walk_spec(spec, defaults, path=()):
        for key, expected in spec.items():
            here = path + (key,)
            if isinstance(expected, dict):
                walk_spec(expected, (defaults or {}).get(key, {}), here)
            elif not isinstance(defaults, dict) or key not in defaults:
                missing.append(".".join(here))

    walk_spec(SPEC, DEFAULTS)
    assert missing == [], f"settings with no declared fallback: {missing}"


# --- the divergences the audit found -----------------------------------------

def test_alert_cooldowns_fall_back_per_severity_not_to_a_flat_number(tmp_path):
    """Was a flat 60 for every severity. CRITICAL ships at 15, so falling back
    quadrupled how long a repeat CRITICAL stayed suppressed - quieter than
    configured, on the severity where quieter is worst."""
    assert default_for("alerting", "cooldown_minutes", "CRITICAL") == 15
    assert default_for("alerting", "cooldown_minutes", "HIGH") == 30
    assert default_for("alerting", "cooldown_minutes", "MEDIUM") == 720
    assert default_for("alerting", "cooldown_minutes", "LOW") == 1440


def test_a_config_with_no_cooldowns_uses_the_shipped_cooldowns(tmp_path):
    """End to end through the reader, not just the registry."""
    path = tmp_path / "rules.yaml"
    path.write_text('polling:\n  fast_loop_seconds: "45"\n')
    rules = RulesConfig(path)

    assert rules.get_int("alerting", "cooldown_minutes", "CRITICAL") == 15
    assert rules.get_int("alerting", "cooldown_minutes", "LOW") == 1440


def test_quantity_drop_tolerances_fall_back_to_the_shipped_values(tmp_path):
    """Both were read with no default at all, so they fell through to the
    getter's own `Decimal("0")`. Zero tolerance alerts on every dust movement
    and rounding artefact - fail-loud in direction, but an alert storm buries
    the real alert just as effectively as silence does."""
    path = tmp_path / "rules.yaml"
    path.write_text('polling:\n  fast_loop_seconds: "45"\n')
    rules = RulesConfig(path)

    assert rules.get_decimal("detectors", "quantity_drop", "tolerance_pct") == Decimal("0.5")
    assert rules.get_decimal("detectors", "quantity_drop", "tolerance_absolute") == Decimal("0.00000001")


def test_a_rejected_tolerance_does_not_become_zero(tmp_path):
    """The realistic path: the value is pruned as invalid, and the reader must
    land on the shipped default rather than on zero."""
    path = tmp_path / "rules.yaml"
    path.write_text('detectors:\n  quantity_drop:\n    tolerance_pct: "0,5"\n')
    rules = RulesConfig(path)

    assert rules.is_valid is False
    assert rules.get_decimal("detectors", "quantity_drop", "tolerance_pct") == Decimal("0.5")


def test_an_explicit_default_still_wins_over_the_registry(tmp_path):
    """Callers that pass one mean it; the registry only fills the gap."""
    path = tmp_path / "rules.yaml"
    path.write_text('polling:\n  fast_loop_seconds: "45"\n')
    rules = RulesConfig(path)

    assert rules.get_int("alerting", "cooldown_minutes", "CRITICAL", default=999) == 999


def test_an_unknown_setting_with_no_default_still_returns_none(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text('polling:\n  fast_loop_seconds: "45"\n')
    rules = RulesConfig(path)

    assert rules.get("detectors", "nonexistent", "thing") is None
