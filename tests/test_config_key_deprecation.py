"""#103 batch item 2: a renamed config key must not repeat 2026-08-20.

On 2026-08-20 the still-running v1.5.2 hot-reloaded a rules.yaml carrying a
key it did not know (`escalation_unacknowledged_minutes_high`) and rejected
the whole file - `rules_config_rejected`, CRITICAL. `retention.
sms_deliveries_days` became `retention.deliveries_days` on 2026-08-28, so
the same deploy shape would reject a config that was valid a minute
earlier. The old spelling is accepted with a warning naming the new one for
at least one release; both spellings at once is an error, never a silent
winner.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pytest

from sentinel.config import RulesConfig
from sentinel.rules_schema import DEPRECATED_KEYS, apply_deprecations

BASE = Path("config/rules.yaml").read_text()


def _config(tmp_path, text):
    path = tmp_path / "rules.yaml"
    path.write_text(text)
    os.utime(path, (time.time() - 10, time.time() - 10))
    return RulesConfig(str(path))


def test_the_old_key_is_accepted_and_warns_naming_the_new_one(tmp_path, caplog):
    text = BASE.replace('deliveries_days: "730"', 'sms_deliveries_days: "365"')
    assert "sms_deliveries_days" in text
    with caplog.at_level(logging.WARNING):
        cfg = _config(tmp_path, text)
    assert cfg.is_valid, cfg.validation_error
    assert cfg.get_int("retention", "deliveries_days") == 365, "the old key's value reaches the new key"
    assert any("sms_deliveries_days is deprecated" in r.message and "deliveries_days" in r.message
               for r in caplog.records)


def test_the_new_key_is_accepted_without_a_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        cfg = _config(tmp_path, BASE)
    assert cfg.is_valid
    assert cfg.get_int("retention", "deliveries_days") == 730
    assert not any("deprecated" in r.message for r in caplog.records)


def test_both_spellings_at_once_is_rejected_not_a_silent_winner(tmp_path):
    text = BASE.replace('deliveries_days: "730"', 'deliveries_days: "730"\n  sms_deliveries_days: "365"')
    cfg = _config(tmp_path, text)
    assert not cfg.is_valid
    assert "both set" in cfg.validation_error and "sms_deliveries_days" in cfg.validation_error
    assert "deliveries_days" in cfg.validation_error


def test_the_rewrite_is_pure_on_data_with_neither_key():
    data = {"retention": {"events_days": 1}}
    assert apply_deprecations(data) == ([], [])
    assert data == {"retention": {"events_days": 1}}


def test_every_deprecation_points_at_a_key_the_schema_knows():
    from sentinel.rules_schema import SPEC

    for old, new in DEPRECATED_KEYS.items():
        node = SPEC
        for part in new:
            assert part in node, f"{new} is not in the schema"
            node = node[part]
        node = SPEC
        for part in old[:-1]:
            node = node[part]
        assert old[-1] not in node, f"{old} is both deprecated and still in the schema"


@pytest.mark.parametrize("text", [BASE.replace('deliveries_days: "730"', 'sms_deliveries_days: "365"')])
def test_a_hot_reload_from_the_old_spelling_to_the_new_is_not_a_rejection(tmp_path, text):
    """The 2026-08-20 shape: the file on disk changes under a running process."""
    cfg = _config(tmp_path, text)
    assert cfg.is_valid
    path = tmp_path / "rules.yaml"
    path.write_text(BASE)
    os.utime(path, (time.time() + 5, time.time() + 5))
    cfg.reload()
    assert cfg.is_valid and cfg.get_int("retention", "deliveries_days") == 730
