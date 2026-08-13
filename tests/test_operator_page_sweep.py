"""Group A of the logged-and-swallowed sweep (#58), plus one defect.

The sweep's own rule is that a page for every entry trains an operator to
ignore the channel, which is the same outcome as not paging at all. So the
question for each is not "is this bad" but "is this the kind of bad that a
human must act on now, and is it event-shaped rather than rate-shaped".

Three entries in group A cleared that bar. What they share is that each one is
**permanent for the life of the process** or **the last resort** — not a
transient that the next cycle retries:

- **Escalation reached nobody.** Escalation is what happens when a CRITICAL
  goes unacknowledged. There is nothing after it. A failure here is the end of
  the line, and it is the only entry in the sweep with a real claim on
  CRITICAL.
- **A channel is routed for a severity but not configured.** Every alert of
  that severity is under-delivered for the entire run, and the existing log
  line is printed once at startup into a file nobody reads.
- **A contact channel is due but has no provider.** Same shape, per account:
  every SMS that account should ever receive silently does not happen.

The per-event delivery failures in group A — one email bounced, one SMS
rejected — are deliberately *not* paged. One failure is noise; a rising rate is
a broken SMTP config, and that is a metric-and-alarm shape. Recorded in the
issue and in comments so the next sweep does not re-litigate it.

The fourth test here is not a paging decision at all. It is a defect the sweep
turned up in group D.
"""
from __future__ import annotations

import sqlite3

from sentinel.alerts.escalation import Escalator
from sentinel.cli import build_channels


class Broken:
    def send(self, alert):
        raise RuntimeError("channel is down")


class Recording:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


def _alerter(db, rules):
    from sentinel.alerts.operator import OperatorAlerter

    return OperatorAlerter(db, {"CRITICAL": [Recording()], "HIGH": [Recording()]}, rules)


def _pages(db):
    with db.cursor() as cur:
        cur.execute("SELECT rule, severity, message, evidence_json FROM operator_pages ORDER BY id")
        return [dict(r) for r in cur.fetchall()]


def _unacked_critical(db, minutes_ago=30):
    from datetime import datetime, timedelta, timezone

    from tests.conftest import make_account

    account_id = make_account(db)
    sent_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, "
            "confidence, sent_at) VALUES (?, 'withdrawal_initiated', 'CRITICAL', 'k', 'msg', "
            "'{}', 'high', ?)",
            (account_id, sent_at),
        )
    return account_id


# --- escalation is the last resort, so its failure is CRITICAL ---------------


def test_an_escalation_that_reaches_nobody_pages_the_operator(db, rules):
    """The strongest case in the sweep. An unacknowledged CRITICAL escalates;
    if the escalation itself reaches nobody there is no further mechanism. The
    customer is not answering and now nobody knows they are not answering."""
    _unacked_critical(db)

    escalated = Escalator([Broken()], unacknowledged_minutes=15,
                          operator_alerter=_alerter(db, rules)).check(db)

    assert escalated == 1  # still marked escalated: we tried
    pages = _pages(db)
    assert [p["rule"] for p in pages] == ["escalation_delivered_to_nobody"]
    assert pages[0]["severity"] == "CRITICAL"


def test_a_partly_working_escalation_pages_nobody(db, rules):
    """Degraded is not uninformed. One channel dead and another delivering
    means the operator was reached, and paging on it would train them to
    ignore the channel — which is the failure this whole sweep is trying not
    to cause."""
    _unacked_critical(db)
    good = Recording()

    Escalator([Broken(), good], unacknowledged_minutes=15,
              operator_alerter=_alerter(db, rules)).check(db)

    assert len(good.sent) == 1
    assert _pages(db) == []


def test_escalation_with_no_channels_at_all_pages(db, rules):
    """An empty channel list is not "nothing to do" — it is every escalation
    going nowhere, which is worse than a channel that fails loudly."""
    _unacked_critical(db)

    Escalator([], unacknowledged_minutes=15, operator_alerter=_alerter(db, rules)).check(db)

    assert [p["rule"] for p in _pages(db)] == ["escalation_delivered_to_nobody"]


def test_escalation_still_works_without_an_alerter(db, rules):
    """The alerter is optional, and its absence must not break escalation. A
    deployment without operator paging configured is worse off, not broken."""
    _unacked_critical(db)

    assert Escalator([Broken()], unacknowledged_minutes=15).check(db) == 1


# --- a channel routed but never configured is a whole severity, all run ------


def test_a_routed_but_unconfigured_channel_is_reported_to_the_caller(monkeypatch, rules):
    """`build_channels` logged this once at startup and carried on. Every alert
    of that severity is then under-delivered for the entire run — not a
    transient, and not something the next cycle fixes.

    Returned rather than paged from inside `build_channels`, because the
    operator alerter is constructed *from* these channels; paging in here would
    need the thing being built."""
    problems: list = []

    build_channels(rules, problems=problems)

    unconfigured = {p["channel"] for p in problems if p["kind"] == "unconfigured"}
    assert "telegram" in unconfigured, problems
    assert all(p["severity"] for p in problems)


def test_a_fully_configured_routing_reports_no_problems(monkeypatch, rules):
    """So the list above means something."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("SMTP_HOST", "h")
    monkeypatch.setenv("SMTP_PORT", "25")
    monkeypatch.setenv("SMTP_USERNAME", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    monkeypatch.setenv("SMTP_SENDER", "s@example.com")
    monkeypatch.setenv("ALERT_EMAIL_TO", "to@example.com")
    problems: list = []

    build_channels(rules, problems=problems)

    unconfigured = {p["channel"] for p in problems if p["kind"] == "unconfigured"}
    assert "telegram" not in unconfigured, problems


def test_routing_falling_back_to_defaults_is_reported_too(rules, monkeypatch):
    """A severity with no usable routing falls back to the built-in defaults
    and logs an error. That is arguably worse than an unconfigured channel —
    the alerts go somewhere nobody chose — and it was equally invisible."""
    problems: list = []

    build_channels(rules, problems=problems)

    assert any(p["kind"] == "fallback" for p in problems), problems


# --- a genuine migration failure is not a no-op ------------------------------


def test_a_migration_failing_for_a_real_reason_is_not_suppressed():
    """Group D, and a defect rather than a paging decision.

    Every migration ran under `contextlib.suppress(OperationalError)`, because
    re-adding an existing column raises and that is the ordinary no-op case.
    But it is the *same exception class* as a migration that genuinely failed —
    a renamed table, a locked database, a typo in the DDL. Those were
    indistinguishable, so a schema that did not actually migrate started the
    service anyway, and every query written against the new shape then failed
    somewhere far away from the cause.

    Only "duplicate column" is a no-op. Everything else is a broken schema and
    must stop the process."""
    from sentinel.db import _migration_already_applied

    assert _migration_already_applied(
        sqlite3.OperationalError("duplicate column name: escalated_at")) is True
    for real_failure in ("no such table: alerts",
                         "database is locked",
                         "near \"ADD\": syntax error",
                         "attempt to write a readonly database"):
        assert _migration_already_applied(sqlite3.OperationalError(real_failure)) is False, real_failure


def test_re_adding_an_existing_column_is_still_a_no_op(tmp_path):
    """The case the suppression existed for. Opening the same database twice
    runs every migration twice, and the second run must be silent."""
    from sentinel.db import Database

    path = tmp_path / "twice.db"
    Database(path).close()
    Database(path).close()  # must not raise


# --- a contact channel with no provider is permanent, per account ------------


class _SpyAlerter:
    """Records every `page` call, including ones a real alerter would dedup
    away.

    Asserting on the `operator_pages` table here would test
    `OperatorAlerter`'s cooldown rather than this router's once-per-process
    guard — and it did: removing the guard entirely left the test passing,
    because the alerter suppressed the second page on its own. Two mechanisms
    that both produce "one page" are indistinguishable through the table."""

    def __init__(self):
        self.calls = []

    def page(self, **kwargs):
        self.calls.append(kwargs)


def test_a_contact_channel_with_no_provider_pages_once(db, rules):
    """Not the per-event failures on this path — one bounced email is noise.
    This is the case where the channel is *set on the account* and there is no
    provider at all, so every alert that account should ever receive through it
    silently does not happen for the life of the process."""
    from sentinel.alerts.contact import ContactRouter

    spy = _SpyAlerter()
    router = ContactRouter(db=db, rules=rules, operator_alerter=spy)
    for _ in range(5):
        router._warn_once("sms", "per-account SMS set but no SMS provider is configured")

    assert len(spy.calls) == 1
    assert spy.calls[0]["rule"] == "contact_channel_has_no_provider"
    assert spy.calls[0]["severity"] == "HIGH"


def test_two_different_contact_channels_page_separately(db, rules):
    """The guard is per channel, not per router. SMTP being unset says nothing
    about the SMS provider, and one page covering both would hide whichever
    was fixed second."""
    from sentinel.alerts.contact import ContactRouter

    spy = _SpyAlerter()
    router = ContactRouter(db=db, rules=rules, operator_alerter=spy)
    router._warn_once("sms", "no SMS provider")
    router._warn_once("smtp", "no SMTP")

    assert {c["evidence"]["channel"] for c in spy.calls} == {"sms", "smtp"}


def test_the_contact_router_works_without_an_alerter(db, rules):
    from sentinel.alerts.contact import ContactRouter

    ContactRouter(db=db, rules=rules)._warn_once("sms", "no provider")  # must not raise


def test_the_startup_routing_pages_are_wired_into_cmd_run():
    """The call site, pinned. `build_channels` can collect problems perfectly
    while `cmd_run` never reads the list — and every test above still passes
    while nothing is ever paged in production."""
    import inspect

    from sentinel import cli

    source = inspect.getsource(cli.cmd_run)
    # The exact argument, not just the name: a `routing_problems` list that is
    # declared and iterated but never *passed* to build_channels stays empty
    # forever, and every assertion about the paging still passes.
    assert "build_channels(rules, problems=routing_problems)" in source
    assert "alert_channel_routed_but_not_configured" in source
    assert "operator_alerter=operator_alerter" in source  # escalator gets it too
