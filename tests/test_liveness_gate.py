"""#103 item 4: the liveness gate.

Two halves, tested separately because they are proven in different places:
the DECLARATION must be complete (this file's completeness gate runs in CI
and fails when a switch exists in the codebase with no entry), and the
EVALUATION must say live / held / dead / unknown honestly against facts
(this file's evaluation tests), exit non-zero on any mechanism with no
evidence of running, and page through a path it is not itself checking.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from sentinel import liveness
from sentinel.liveness import (
    DEAD,
    ENTRIES,
    HELD,
    LIVE,
    NOT_A_SWITCH,
    UNKNOWN,
    Facts,
    evaluate,
    failing,
    load_holds,
    render,
)

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _facts(db, **kw):
    conn = sqlite3.connect(db._path)
    conn.row_factory = sqlite3.Row
    kw.setdefault("env", {})
    kw.setdefault("now", NOW)
    return Facts(conn=conn, **kw)


def _by(verdicts):
    return {v.key: v for v in verdicts}


# --- completeness: every switch in the codebase is declared or excused --------------------

def _declared_switches() -> set[str]:
    out = set()
    for e in ENTRIES:
        out.update(e.switches)
    return out


def test_every_env_variable_in_the_example_file_is_declared_or_excused():
    names = re.findall(r"^([A-Z][A-Z0-9_]+)=", (ROOT / ".env.example").read_text(), re.M)
    declared = _declared_switches()
    missing = [n for n in names if n not in declared and n not in NOT_A_SWITCH]
    assert not missing, f"switches with no liveness entry and no excuse: {missing}"


def test_every_enabled_flag_in_rules_is_declared_or_excused():
    rules = yaml.safe_load((ROOT / "config/rules.yaml").read_text())
    flags = [f"{section}.enabled" for section, body in rules.items() if isinstance(body, dict) and "enabled" in body]
    declared = _declared_switches()
    missing = [f for f in flags if f not in declared and f not in NOT_A_SWITCH]
    assert flags, "the scan found no enabled flags - it is broken, not clean"
    assert not missing, f"config switches with no liveness entry and no excuse: {missing}"


def test_every_host_timer_is_declared():
    timers = [p.name for p in (ROOT / "scripts/host").glob("*.timer")]
    declared = _declared_switches()
    assert timers
    missing = [t for t in timers if t not in declared and t not in NOT_A_SWITCH]
    assert not missing, f"host timers with no liveness entry: {missing}"


def test_the_switch_creating_cli_verbs_are_declared():
    src = (ROOT / "sentinel/cli.py").read_text()
    verbs = {"operator add": '"operator"' in src and '"add"' in src,
             "set-threshold": "set-threshold" in src,
             "onboard --drill": '"--drill"' in src,
             "sentinel digest": 'add_parser("digest"' in src}
    present = {k for k, v in verbs.items() if v}
    assert present == set(verbs), f"the scan lost a verb: {set(verbs) - present}"
    assert present <= _declared_switches()


def test_every_excuse_names_something_that_exists():
    env_names = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", (ROOT / ".env.example").read_text(), re.M))
    rules = yaml.safe_load((ROOT / "config/rules.yaml").read_text())
    flags = {f"{s}.enabled" for s, b in rules.items() if isinstance(b, dict) and "enabled" in b}
    for name, reason in NOT_A_SWITCH.items():
        assert name in env_names or name in flags, f"stale excuse: {name}"
        assert len(reason) > 10


def test_every_entry_says_how_to_switch_it_on_and_what_proves_it():
    for e in ENTRIES:
        assert e.switch_on and e.signal and e.switches and callable(e.check), e.key
    assert len({e.key for e in ENTRIES}) == len(ENTRIES)


# --- evaluation: honest verdicts against facts -------------------------------------------------

def test_a_fresh_database_with_nothing_switched_on_is_dead_across_the_board(db):
    verdicts = _by(evaluate(_facts(db, crontab_has_digest=False, newest_backup_age_seconds=-1,
                                   restart_timer_active=False, liveness_timer_active=False)))
    assert verdicts["operators"].state == DEAD
    assert verdicts["digest"].state == DEAD
    assert verdicts["backup"].state == DEAD
    assert verdicts["drill_account"].state == DEAD
    assert verdicts["sms"].state == DEAD
    assert verdicts["thresholds"].state == DEAD
    assert verdicts["heartbeat"].state == DEAD
    assert verdicts["restart_timer"].state == DEAD
    assert verdicts["liveness_timer"].state == DEAD
    assert verdicts["telegram"].state == DEAD and verdicts["smtp"].state == DEAD
    assert len(failing(evaluate(_facts(db)))) == len(ENTRIES)


def test_host_facts_the_host_did_not_give_are_unknown_and_still_fail(db):
    verdicts = _by(evaluate(_facts(db)))
    assert verdicts["backup"].state == UNKNOWN
    assert verdicts["restart_timer"].state == UNKNOWN
    assert verdicts["digest"].state == UNKNOWN
    assert {v.key for v in failing(evaluate(_facts(db)))} >= {"backup", "restart_timer", "digest"}


def test_switching_each_mechanism_on_makes_it_live(db, monkeypatch):
    from sentinel.alerts.delivery import record_delivery
    from sentinel.operators import create_operator
    from tests.conftest import make_account

    create_operator(db, "erwin", "correct-horse-battery")
    record_delivery(db, channel="email", recipient="a@x.test", status="accepted", kind="digest", when=NOW - timedelta(hours=3))
    record_delivery(db, channel="telegram", recipient="1", status="accepted", when=NOW - timedelta(hours=1))
    aid = make_account(db, "drill")
    with db.cursor() as cur:
        cur.execute("UPDATE accounts SET is_drill = 1, status = 'active' WHERE id = ?", (aid,))
        cur.execute("INSERT INTO account_threshold_overrides (account_id, setting, value, set_at) "
                    "VALUES (?, 'detectors.quantity_drop.tolerance_pct', '1', ?)", (aid, NOW.isoformat()))
        cur.execute("INSERT INTO portal_meta (key, value) VALUES ('heartbeat:last', ?)", (NOW.isoformat(),))
    env = {"CLICKSEND_USERNAME": "u", "CLICKSEND_API_KEY": "k", "SENTINEL_HEARTBEAT_URL": "https://m/x",
           "TELEGRAM_BOT_TOKEN": "t", "SMTP_HOST": "h", "SMTP_SENDER": "s@x"}
    verdicts = evaluate(_facts(db, env=env, crontab_has_digest=True, newest_backup_age_seconds=3600,
                               restart_timer_active=True, liveness_timer_active=True))
    assert failing(verdicts) == [], render(verdicts)
    assert all(v.state == LIVE for v in verdicts)


def test_a_digest_older_than_48h_is_dead_even_with_the_cron_line(db):
    from sentinel.alerts.delivery import record_delivery

    record_delivery(db, channel="email", recipient="a@x.test", status="accepted", kind="digest", when=NOW - timedelta(hours=49))
    v = _by(evaluate(_facts(db, crontab_has_digest=True)))["digest"]
    assert v.state == DEAD and "48h" in v.evidence


def test_a_digest_mark_from_before_the_delivery_record_counts_as_evidence(db):
    """The deployed build has no deliveries table; its digest leaves a
    portal_meta mark. The first production run must read that too."""
    with db.cursor() as cur:
        cur.execute("INSERT INTO portal_meta (key, value) VALUES ('digest_mark:a@x.test', ?)",
                    ('{"date": "%s"}' % (NOW - timedelta(hours=5)).isoformat(),))
    assert _by(evaluate(_facts(db, crontab_has_digest=True)))["digest"].state == LIVE


def test_a_backup_older_than_48h_is_dead(db):
    assert _by(evaluate(_facts(db, newest_backup_age_seconds=3 * 24 * 3600)))["backup"].state == DEAD


def test_a_declared_hold_with_a_reason_is_held_not_dead(db, tmp_path):
    holds_file = tmp_path / "holds.yaml"
    holds_file.write_text("holds:\n  sms: waiting on the ClickSend account (decision pending)\n")
    holds = load_holds(str(holds_file))
    verdicts = _by(evaluate(_facts(db, holds=holds)))
    assert verdicts["sms"].state == HELD and "ClickSend" in verdicts["sms"].evidence
    assert "sms" not in {v.key for v in failing(evaluate(_facts(db, holds=holds)))}


@pytest.mark.parametrize("text, why", [
    ("holds:\n  operators: we like it open\n", "cannot be held"),
    ("holds:\n  sms: ''\n", "needs a reason"),
    ("holds:\n  teleporter: because\n", "unknown mechanism"),
])
def test_an_undeclared_or_load_bearing_hold_is_refused(tmp_path, text, why):
    holds_file = tmp_path / "holds.yaml"
    holds_file.write_text(text)
    with pytest.raises(ValueError, match=why):
        load_holds(str(holds_file))


def test_the_shipped_holds_file_parses_and_names_only_holdable_entries():
    holds = load_holds(str(ROOT / "config/liveness-holds.yaml"))
    holdable = {e.key for e in ENTRIES if e.holdable}
    assert set(holds) <= holdable


# --- the report path: not through anything it checks -----------------------------------------

def test_no_guaranteed_report_path_is_a_declared_mechanism():
    """The constraint, enforced: the digest cannot report the digest dead,
    and the Telegram bot cannot report the Telegram bot dead. Every path
    the verdict is guaranteed to reach must be absent from the declaration
    - as a key, a switch, or a title - and the two best-effort legs must
    stay declared, so the shared failure is on record rather than hidden."""
    from sentinel.liveness import BEST_EFFORT_LEGS, REPORT_PATHS

    declared_words = set()
    for e in ENTRIES:
        declared_words.add(e.key.lower())
        declared_words.update(s.lower() for s in e.switches)
        declared_words.update(e.title.lower().split())
    for path in REPORT_PATHS:
        assert path.lower() not in declared_words, f"the verdict travels by {path}, which is a declared mechanism"
        assert not any(path.lower() in w for w in declared_words), f"{path} is entangled with a declared mechanism"
    assert set(BEST_EFFORT_LEGS) <= {e.key for e in ENTRIES}
    assert "telegram" in BEST_EFFORT_LEGS and "smtp" in BEST_EFFORT_LEGS, "the shared-bot fact must stay on record"
    # and the guaranteed paths are real: the row is written even when no channel delivered
    src = (ROOT / "sentinel/alerts/operator.py").read_text()
    assert "INSERT INTO operator_pages" in src
    host = (ROOT / "scripts/host/liveness.sh").read_text()
    assert "logger -t minas-liveness" in host and "MOTD_FILE" in host


def test_the_member_and_ops_telegram_share_one_bot_token():
    """The fact the docstring rests on, pinned: if these ever diverge the
    docstring is wrong and the best-effort leg might have become independent."""
    contact = (ROOT / "sentinel/alerts/contact.py").read_text()
    cli = (ROOT / "sentinel/cli.py").read_text()
    assert 'env_setting("TELEGRAM_BOT_TOKEN")' in contact, "member Telegram (#18) reads TELEGRAM_BOT_TOKEN"
    assert 'env_setting("TELEGRAM_BOT_TOKEN")' in cli, "the ops channels (#53) read TELEGRAM_BOT_TOKEN"

def test_the_verdict_pages_the_operator_once_per_interval_and_never_through_digest_or_sms(db):
    from sentinel.alerts.operator import OperatorAlerter
    from sentinel.config import RulesConfig

    class _Chan:
        route_name = "telegram_team"
        recipients = ["-1"]
        sent = []

        def send(self, alert):
            _Chan.sent.append(alert)

    rules = RulesConfig("config/rules.yaml")
    verdicts = evaluate(_facts(db))
    def fake_channels(_rules):
        return {"HIGH": [_Chan()], "CRITICAL": [_Chan()]}

    import sentinel.cli as cli_module

    original = cli_module.build_operator_channels
    cli_module.build_operator_channels = fake_channels
    try:
        first = liveness.page_verdict(db, rules, verdicts)
        second = liveness.page_verdict(db, rules, verdicts)
    finally:
        cli_module.build_operator_channels = original
    assert first is not None and second is None, "the HIGH cooldown makes it loud once per interval"
    assert len(_Chan.sent) == 1 and "built but not running" in _Chan.sent[0].message
    with db.cursor() as cur:
        cur.execute("SELECT rule, severity FROM operator_pages")
        assert [tuple(r) for r in cur.fetchall()] == [("liveness_failed", "HIGH")]
    src = (ROOT / "sentinel/liveness.py").read_text()
    assert "digest" not in src[src.index("def page_verdict"):], "the verdict must not travel by the digest"
    assert "build_sms_channel" not in src
    assert isinstance(OperatorAlerter, type)


def test_nothing_failing_pages_nobody(db, monkeypatch):
    from sentinel.config import RulesConfig

    verdicts = [liveness.Verdict(e.key, LIVE, "x") for e in ENTRIES]
    assert liveness.page_verdict(db, RulesConfig("config/rules.yaml"), verdicts) is None


# --- the CLI ----------------------------------------------------------------------------------

def test_the_cli_exits_non_zero_and_prints_the_switch_on_acts(db, monkeypatch, capsys):
    import argparse

    from sentinel.cli import cmd_liveness

    monkeypatch.setenv("SENTINEL_DB_PATH", str(db._path))
    monkeypatch.setenv("SENTINEL_LIVENESS_HOLDS", str(ROOT / "config/liveness-holds.yaml"))
    ns = argparse.Namespace(crontab_has_digest="no", newest_backup_age_seconds=-1, restart_timer_active="yes",
                            liveness_timer_active="yes", no_page=True, as_json=False)
    rc = cmd_liveness(ns)
    out = capsys.readouterr().out
    assert rc == 1
    assert "DEAD" in out and "switch on:" in out and "sentinel operator add" in out


def test_the_host_script_gathers_the_three_host_facts_and_goes_loud_once(tmp_path):
    """scripts/host/liveness.sh is what the timer runs: it gathers the facts
    the container cannot see, runs the gate inside the container, and on
    failure writes journald + motd exactly once per state change (#81's
    loud-once), never every tick."""
    import os
    import subprocess

    script = ROOT / "scripts/host/liveness.sh"
    assert os.access(script, os.X_OK)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "docker").write_text("#!/usr/bin/env bash\nprintf 'DEAD  Member digest  no cron\\n'\nexit 1\n")
    (fake_bin / "systemctl").write_text("#!/usr/bin/env bash\nexit 0\n")
    (fake_bin / "crontab").write_text("#!/usr/bin/env bash\necho '0 8 * * * sentinel digest'\n")
    (fake_bin / "logger").write_text("#!/usr/bin/env bash\necho \"$@\" >> " + str(tmp_path / "journal") + "\n")
    (fake_bin / "sudo").write_text("#!/usr/bin/env bash\nexec \"$@\"\n")
    for p in fake_bin.iterdir():
        p.chmod(0o755)
    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}",
               MINAS_BACKUP_DIR=str(tmp_path / "nobackups"), MINAS_MOTD_FILE=str(tmp_path / "motd"),
               MINAS_STATE_FILE=str(tmp_path / "state"), MINAS_COMPOSE_FILE=str(tmp_path / "compose.yml"),
               MINAS_HOST_LOG=str(tmp_path / "host.log"))
    first = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    second = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    assert first.returncode == 1 and second.returncode == 1
    motd = (tmp_path / "motd").read_text()
    assert motd.count("LIVENESS") == 1, "loud once per state, not once per tick"
    journal = (tmp_path / "journal").read_text()
    assert "liveness" in journal.lower()
    assert "--crontab-has-digest yes" in (tmp_path / "host.log").read_text()
    assert "--liveness-timer-active yes" in (tmp_path / "host.log").read_text()

    # 4(b): red-to-red with a CHANGED dead set must be reported, not silent.
    # At 16:28:21 on 2026-08-31 the set went 7 -> 6 and journald recorded
    # only the facts line. The docker stub now reports a different, smaller
    # dead set: the delta is logged (what came live, what went dead) and the
    # marker is refreshed, still exactly one.
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env bash\nprintf 'DEAD  Database backup  none\\n'\nexit 1\n")
    before = (tmp_path / "journal").read_text()
    third = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    assert third.returncode == 1
    delta = (tmp_path / "journal").read_text()[len(before):]
    assert "now live" in delta and "Member digest" in delta, "a mechanism switching ON is acknowledged"
    assert "newly dead" in delta and "Database backup" in delta
    assert (tmp_path / "motd").read_text().count("LIVENESS") == 1, "the marker is refreshed, not duplicated"
    # and red-to-red with the SAME set stays quiet
    before = (tmp_path / "journal").read_text()
    subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    quiet = (tmp_path / "journal").read_text()[len(before):]
    assert "newly dead" not in quiet and "now live" not in quiet
