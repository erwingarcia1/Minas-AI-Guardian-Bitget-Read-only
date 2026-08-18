"""#84: every accounts.status value shipped as a declaration first.

`disabled` was in the schema, styled, sorted and filtered on from schema
day, and nothing could write it until #80. `tamper_suspected` is in that
state now. The CHECK constraint *registers* a value; nothing proves
anything *produces* it — the registration-guards-need-execution-guards
lesson applied to enum values rather than tables.

The guard: every value in the accounts.status CHECK constraint either has
a writer proven here, or sits in INTENTIONALLY_UNREACHABLE with a reason.
Both containment directions are asserted (the one-directional-containment
lesson): a constraint value with neither claim fails, and a stale claim
naming a value the constraint no longer carries fails too.

Honest limit: "has a writer" is proven in two halves — the writer
round-trips the value into the row (execution), and the product caller's
source carries the literal (registration of the caller) — because nothing
mechanical can prove a deployed code path fires without running the whole
product. The two halves together are exactly what #80's defect lacked.
"""
from __future__ import annotations

import importlib
import inspect
import re
from pathlib import Path

from sentinel.onboarding import ACCOUNT_STATUSES, set_account_status

ROOT = Path(__file__).parent.parent

# status -> the product code path that passes the literal. A value absent
# here must be in INTENTIONALLY_UNREACHABLE instead — never in neither.
WRITTEN_BY = {
    "active": "sentinel.cli.cmd_account_status",    # enable-account
    "disabled": "sentinel.cli.cmd_account_status",  # disable-account
}

# status -> why it deliberately has no writer yet. An entry here is a
# recorded decision that fails loudly when someone later assumes the value
# works — not silence that reads as coverage.
INTENTIONALLY_UNREACHABLE = {
    "tamper_suspected": (
        "Declared ahead of its writer: detect_tamper raises the alert, but "
        "no code path moves an account into this status (#84). Its "
        "prerequisite — #83's reason ordering, so the status cannot ship "
        "with dead customer-facing copy — is fixed; whether the detector "
        "should write the status automatically is a product decision, not "
        "an oversight. Building the writer removes this entry."
    ),
}


def _constraint_values() -> set[str]:
    """Parsed from schema.sql, never retyped: a list restated here would go
    on passing after the constraint changed (the same rule as the health
    check reading its floors out of CI config)."""
    schema = (ROOT / "sentinel" / "schema.sql").read_text()
    block = re.search(r"CREATE TABLE IF NOT EXISTS accounts\b.*?\n\);", schema, re.S)
    assert block, "accounts table not found in schema.sql"
    m = re.search(r"^\s*status\s+TEXT.*CHECK \(status IN \(([^)]*)\)\)",
                  block.group(0), re.M)
    assert m, "accounts.status CHECK constraint not found"
    return {v.strip().strip("'") for v in m.group(1).split(",")}


def _account(db, label, status):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, "
            "encrypted_secret, encrypted_passphrase, status, onboarded_at, updated_at) "
            "VALUES (?, 'bitget', 'independent', 'x', 'y', 'z', ?, '2026-01-01', '2026-01-01')",
            (label, status),
        )


def test_every_constraint_value_has_a_writer_or_a_recorded_reason():
    values = _constraint_values()
    claimed = set(WRITTEN_BY) | set(INTENTIONALLY_UNREACHABLE)
    assert values <= claimed, (
        f"status value(s) with no writer and no recorded reason: "
        f"{sorted(values - claimed)} — the #80 defect shape, again")


def test_every_claim_names_a_real_constraint_value():
    """The quieter direction: a claim outliving a renamed value keeps
    looking like coverage while covering nothing."""
    values = _constraint_values()
    claimed = set(WRITTEN_BY) | set(INTENTIONALLY_UNREACHABLE)
    assert claimed <= values, f"stale claim(s): {sorted(claimed - values)}"


def test_no_value_is_claimed_both_ways():
    both = set(WRITTEN_BY) & set(INTENTIONALLY_UNREACHABLE)
    assert not both, f"written AND unreachable is a contradiction: {sorted(both)}"


def test_the_code_mirror_matches_the_constraint():
    """onboarding.ACCOUNT_STATUSES exists so a typo fails with a named error
    before SQL — which only holds while it and the schema agree."""
    assert set(ACCOUNT_STATUSES) == _constraint_values()


def test_every_written_value_round_trips_through_the_writer(db):
    """Execution half: the writer actually lands each claimed value."""
    for value in WRITTEN_BY:
        label = f"writer-{value}"
        _account(db, label, "disabled" if value == "active" else "active")
        assert set_account_status(db, label, value, actor=None) is True
        with db.cursor() as cur:
            cur.execute("SELECT status FROM accounts WHERE label = ?", (label,))
            assert cur.fetchone()["status"] == value


def test_every_written_value_is_passed_by_its_named_caller():
    """Registration half: the product entry point's own source carries the
    literal, so WRITTEN_BY cannot claim a caller that never passes it."""
    for value, qualname in WRITTEN_BY.items():
        mod_name, func_name = qualname.rsplit(".", 1)
        func = getattr(importlib.import_module(mod_name), func_name)
        assert f'"{value}"' in inspect.getsource(func), (
            f"{qualname} never passes {value!r}")
