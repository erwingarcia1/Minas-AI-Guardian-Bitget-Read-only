"""The liveness gate (#103 item 4): mechanisms that need a human switch-on
step, and the signal that proves each one is actually running.

The recurring defect behind audit #103 was not code that was wrong but code
that shipped complete and was never switched on: six mechanisms BUILT BUT
INERT, seven BUILT NOT LIVE, SMS with no provider. Each waited on one human
act - an operator account, a cron line, a credential, a backup, a drill
account - and nothing anywhere said so.

Two halves, deliberately separate:

- The DECLARATION (`ENTRIES` below) lists every such mechanism with the act
  that switches it on and the concrete signal that proves it is live. CI
  proves the declaration is COMPLETE (tests/test_liveness_declaration.py:
  a switch found in the codebase with no entry fails the build). CI cannot
  see production.
- `sentinel liveness` runs ON PRODUCTION, evaluates every entry against the
  live database, the container's environment and facts the host script
  hands in, and exits non-zero on any mechanism with no evidence of
  running. Production proves LIVENESS.

"Held" is a valid state. A deliberate hold - SMS until a provider is
chosen, thresholds left at their defaults - is declared in
config/liveness-holds.yaml with a reason, and is not a failure. An
UNDECLARED hold is exactly the failure this gate exists to catch.

How the verdict travels - the design constraint: it must not travel by
any channel this gate is checking, because a checker that reports
through the digest can never say the digest is dead.

Resolved factually (2026-08-28): the declared `telegram` entry is the
BOT TOKEN, and the member Telegram linking mechanism (#18) and the ops
team chat (#53) are the SAME bot with different chat ids - one token,
one Telegram API, one way to die. So the operator page's Telegram leg is
NOT an independent path, and neither is its email leg (`smtp` is
declared too). Both are kept as BEST-EFFORT legs - useful while they
work - and the guaranteed paths are the ones that are not, and cannot
be, mechanisms in this declaration:

  1. the `operator_pages` ROW. OperatorAlerter writes it whether or not
     any channel delivered (#57's rule), and the ops dashboard's pages
     card shows it - the same surface an operator already reads.
  2. journald at err and /etc/motd, written by the host script
     (scripts/host/liveness.sh) once per red transition in #81's
     loud-once style - no network, no credential, no bot.
  3. the timer unit's non-zero exit: `systemctl --failed` on the host
     names minas-liveness.service.

`REPORT_PATHS` names those three; `BEST_EFFORT_LEGS` names the two
declared mechanisms the page also travels by. tests/test_liveness_gate.py
FAILS if a report path ever appears in the declaration, or if a
best-effort leg stops being declared (it would then be a hidden
dependency).

The page fires on a CHANGE in the failing set, never on the state
(2026-09-02): seven mechanisms that had been off for days, and could not
change without a human act, paged HIGH on every hourly run - and each
page escalated at 60 unacknowledged minutes, so the owner received a
message every ~30 minutes on two channels, until the timer was stopped by
hand. `page_verdict` keeps the set of failing mechanism KEYS it last
reported in portal_meta['liveness:paged_failing']; a mechanism that newly
fails pages HIGH once; an unchanged set is silent however long it stays;
one that comes back is reported once at MEDIUM (below HIGH: nothing is
wrong); a held mechanism is never in the set and never pages. The host
script applies the same rule to journald and /etc/motd with its own state
file; the two are deliberately independent because each survives the
other's death.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

logger = logging.getLogger(__name__)

HOLDS_PATH_ENV = "SENTINEL_LIVENESS_HOLDS"
DEFAULT_HOLDS_PATH = "./config/liveness-holds.yaml"
FRESH = timedelta(hours=48)
PAGE_RULE = "liveness_failed"
RECOVERY_RULE = "liveness_recovered"
# portal_meta key holding the sorted failing keys the last page reported.
# Written only by page_verdict - a hand run (no --page) never touches it.
PAGED_STATE_KEY = "liveness:paged_failing"

LIVE, HELD, DEAD, UNKNOWN = "live", "held", "dead", "unknown"

# Where the verdict is GUARANTEED to land. None of these may ever be a
# declared mechanism - see the module docstring and the test that enforces it.
REPORT_PATHS = ("operator_pages", "journald", "motd", "systemd")
# Declared mechanisms the operator page ALSO travels by, best effort: they
# share their failure with what they report on, so they are not relied on.
BEST_EFFORT_LEGS = ("telegram", "smtp")


@dataclass
class Facts:
    """Everything an entry may look at. `conn` is any sqlite3 connection
    with a dict-like row factory - a READ-ONLY one is fine, which is how
    the first run on production was made without a deploy. Host facts are
    None when the caller could not gather them (a run from inside the
    container with no host script) and are then reported as UNKNOWN, never
    as live."""
    conn: object
    env: dict = field(default_factory=lambda: dict(os.environ))
    holds: dict = field(default_factory=dict)
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    crontab_has_digest: bool | None = None
    newest_backup_age_seconds: int | None = None
    restart_timer_active: bool | None = None
    liveness_timer_active: bool | None = None

    def query(self, sql, *params):
        try:
            cur = self.conn.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) if not isinstance(r, dict) else r for r in rows]
        except Exception as exc:  # noqa: BLE001 - a missing table is an answer, not a crash
            return [{"__error__": str(exc)}]

    def scalar(self, sql, *params):
        rows = self.query(sql, *params)
        if not rows or "__error__" in rows[0]:
            return None
        return list(rows[0].values())[0]

    def env_set(self, *names) -> bool:
        return all((self.env.get(n) or "").strip() for n in names)


@dataclass
class Verdict:
    key: str
    state: str
    evidence: str


@dataclass(frozen=True)
class Entry:
    key: str
    title: str
    issue: str
    switch_on: str  # the exact human act or command
    signal: str  # what proves it is live, in words
    switches: tuple  # the switch-shaped things in the codebase this entry covers (for the completeness gate)
    check: Callable[[Facts], tuple[str, str]]
    holdable: bool = False  # may be declared on hold with a reason


def _within(facts: Facts, stamp: str | None) -> bool:
    if not stamp:
        return False
    try:
        when = datetime.fromisoformat(str(stamp))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return facts.now - when <= FRESH


# --- the checks --------------------------------------------------------------------

def _operators(f: Facts):
    n = f.scalar("SELECT COUNT(*) FROM operators WHERE status = 'active'")
    if n is None:
        n = f.scalar("SELECT COUNT(*) FROM operators")
    if n is None:
        return UNKNOWN, "operators table unreadable"
    return (LIVE, f"{n} active operator(s)") if n > 0 else (DEAD, "operators table is empty: no dashboard sign-in, every operator action anonymous")


def _digest(f: Facts):
    last = f.scalar("SELECT max(created_at) FROM deliveries WHERE kind = 'digest' AND status = 'accepted'")
    marks = f.query("SELECT value FROM portal_meta WHERE key LIKE 'digest_mark:%'")
    mark_dates = []
    for row in marks:
        if "__error__" in row:
            break
        value = str(row.get("value") or "")
        for token in value.replace('"', " ").replace("{", " ").replace("}", " ").replace(",", " ").split():
            if token[:4].isdigit() and "-" in token:
                mark_dates.append(token[:10])
    recent_mark = any(_within(f, d) for d in mark_dates)
    sent = _within(f, last) or recent_mark
    cron = f.crontab_has_digest
    if cron is None and not sent:
        return UNKNOWN, "no digest delivery in 48h and the host did not say whether the cron line exists"
    if cron is False and not sent:
        return DEAD, "no crontab line runs `sentinel digest` and no digest was sent in 48h"
    if not sent:
        return DEAD, "the cron line exists but no digest was sent in the last 48h"
    if cron is False:
        return DEAD, f"a digest went out ({last or max(mark_dates)}) but no cron line schedules the next one"
    return LIVE, f"cron line present; last digest {last or max(mark_dates)}"


def _backup(f: Facts):
    age = f.newest_backup_age_seconds
    if age is None:
        return UNKNOWN, "the host did not report a backup age (no host script, or /var/backups/minas-guardian absent)"
    if age < 0:
        return DEAD, "no backup file exists in /var/backups/minas-guardian"
    hours = age / 3600
    return (LIVE, f"newest backup is {hours:.1f}h old") if age <= FRESH.total_seconds() else (DEAD, f"newest backup is {hours:.1f}h old (> 48h)")


def _drill_account(f: Facts):
    n = f.scalar("SELECT COUNT(*) FROM accounts WHERE is_drill = 1 AND status = 'active'")
    if n is None:
        return DEAD, "the schema has no drill flag yet (this build is not deployed) or the table is unreadable"
    if n == 0:
        return DEAD, "no dedicated drill account: every scheduled drill fails and pages"
    last = f.scalar("SELECT max(started_at) FROM drills")
    return LIVE, f"{n} drill account(s); last drill {last or 'never yet'}"


def _sms(f: Facts):
    click = f.env_set("CLICKSEND_USERNAME", "CLICKSEND_API_KEY")
    twilio = f.env_set("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")
    if click or twilio:
        return LIVE, "an SMS provider is configured (" + ("ClickSend" if click else "Twilio") + ")"
    return DEAD, "no SMS provider configured: every SMS leg is skipped, CRITICALs reach no phone"


def _thresholds(f: Facts):
    n = f.scalar("SELECT COUNT(*) FROM account_threshold_overrides")
    if n is None:
        return UNKNOWN, "overrides table unreadable"
    return (LIVE, f"{n} per-account override(s)") if n > 0 else (DEAD, "no per-account threshold has ever been set")


def _heartbeat(f: Facts):
    if not f.env_set("SENTINEL_HEARTBEAT_URL"):
        return DEAD, "SENTINEL_HEARTBEAT_URL is unset: no external monitor hears the heartbeat"
    last = f.scalar("SELECT value FROM portal_meta WHERE key = 'heartbeat:last'")
    return (LIVE, f"last beat {last}") if last else (DEAD, "URL set but no beat has ever been recorded")


def _restart_timer(f: Facts):
    if f.restart_timer_active is None:
        return UNKNOWN, "the host did not report the timer's state"
    return (LIVE, "minas-restart-on-unhealthy.timer is active") if f.restart_timer_active else (DEAD, "minas-restart-on-unhealthy.timer is not active")


def _liveness_timer(f: Facts):
    """The gate judging itself: a liveness gate that ships inert would be the
    joke writing itself. The host script reports whether its own timer is
    active; a run with no host script cannot know, and says so."""
    if f.liveness_timer_active is None:
        return UNKNOWN, "the host did not report whether minas-liveness.timer is active (was this run from the host script?)"
    return (LIVE, "minas-liveness.timer is active") if f.liveness_timer_active else (DEAD, "minas-liveness.timer is not active: this gate runs only by hand")


def _telegram(f: Facts):
    if not f.env_set("TELEGRAM_BOT_TOKEN"):
        return DEAD, "TELEGRAM_BOT_TOKEN unset: no ops Telegram, no member Telegram"
    last = f.scalar("SELECT max(created_at) FROM deliveries WHERE channel IN ('telegram', 'telegram_team') AND status = 'accepted'")
    return LIVE, f"token set; last accepted Telegram delivery {last or 'none recorded yet'}"


# How many consecutive refused sends make the email channel DEAD. Five: a
# single bounce is one bad address; five in a row with no acceptance between
# them is the provider refusing us (2026-09-21: "550 daily sending quota
# reached" for twelve hours, every customer alert email lost, SMTP "live").
SMTP_DEAD_AFTER_REFUSALS = 5


def _smtp(f: Facts):
    if not f.env_set("SMTP_HOST", "SMTP_SENDER"):
        return DEAD, "SMTP_HOST/SMTP_SENDER unset: no email leaves this host"
    recent = f.query("SELECT status, detail, created_at FROM deliveries WHERE channel = 'email' "
                     "ORDER BY created_at DESC, id DESC LIMIT ?", SMTP_DEAD_AFTER_REFUSALS)
    if recent and "__error__" not in recent[0] and len(recent) >= SMTP_DEAD_AFTER_REFUSALS \
            and all(r["status"] == "failed" for r in recent):
        detail = (recent[0].get("detail") or "")[:120]
        return DEAD, (f"the last {SMTP_DEAD_AFTER_REFUSALS} email sends were refused (newest {recent[0]['created_at']}): "
                      f"{detail} - the provider is refusing us; nothing here can say how much capacity remains, "
                      "SMTP does not expose it")
    last = f.scalar("SELECT max(created_at) FROM deliveries WHERE channel = 'email' AND status = 'accepted'")
    return LIVE, f"SMTP set; last accepted email {last or 'none recorded yet'}"


ENTRIES: tuple[Entry, ...] = (
    Entry("operators", "Operator accounts", "#54 #40 #76 #87",
          "`sentinel operator add <username>` on the host, once per person (the dashboard admits nobody until then)",
          "operators table has an active row", ("operator add",), _operators),
    Entry("digest", "Member digest", "#21 #14",
          "root's crontab: `0 8 * * * cd /opt/minas-guardian && docker compose exec -T sentinel sentinel digest`",
          "a cron line runs `sentinel digest` AND a digest delivery was accepted in the last 48h",
          ("sentinel digest", "cron"), _digest),
    Entry("backup", "Database backup", "#85",
          "a nightly job writing to /var/backups/minas-guardian (docs/deployment.md 'Where backups go')",
          "a file in /var/backups/minas-guardian newer than 48h", ("/var/backups/minas-guardian",), _backup),
    Entry("drill_account", "Dedicated drill account", "#37",
          "`sentinel onboard ... --drill` for a Minas-owned account, on production",
          "an active account with is_drill = 1", ("onboard --drill", "drills.enabled"), _drill_account,
          # Holdable since 2026-08-31: `onboard --drill` verifies a REAL
          # exchange key, so the drill account is an owner decision (which
          # exchange, who creates it), not a command. The hold and
          # drills.enabled: false travel together - see #37.
          holdable=True),
    Entry("sms", "SMS provider", "#19 #75",
          "set CLICKSEND_USERNAME + CLICKSEND_API_KEY (or the TWILIO_* triple) in the host's .env and restart",
          "a provider's credentials are present in the environment",
          ("CLICKSEND_USERNAME", "CLICKSEND_API_KEY", "CLICKSEND_FROM", "CLICKSEND_TO_NUMBERS",
           "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "TWILIO_TO_NUMBERS"), _sms, holdable=True),
    Entry("thresholds", "Per-account thresholds", "#17",
          "`sentinel set-threshold --account <label> <setting> <value>` for the first customer who needs one",
          "account_threshold_overrides has a row", ("set-threshold",), _thresholds, holdable=True),
    Entry("heartbeat", "Dead-man's switch", "#63",
          "set SENTINEL_HEARTBEAT_URL to the external monitor's check-in URL and restart",
          "the URL is set and a beat has been recorded", ("SENTINEL_HEARTBEAT_URL",), _heartbeat, holdable=True),
    Entry("restart_timer", "Restart-on-unhealthy timer", "#81",
          "install scripts/host/minas-restart-on-unhealthy.{service,timer} and `systemctl enable --now` the timer",
          "systemd reports the timer active", ("minas-restart-on-unhealthy.timer",), _restart_timer),
    Entry("liveness_timer", "The liveness gate itself", "#103",
          "install scripts/host/minas-liveness.{service,timer} and `systemctl enable --now minas-liveness.timer`",
          "systemd reports minas-liveness.timer active", ("minas-liveness.timer",), _liveness_timer),
    Entry("telegram", "Telegram", "#18 #53",
          "set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (+ TELEGRAM_TEAM_CHAT_ID) in the host's .env",
          "the token is set", ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_TEAM_CHAT_ID"), _telegram),
    Entry("smtp", "Email", "#20",
          "set SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_SENDER, SMTP_RECIPIENTS in the host's .env",
          "host and sender are set AND the last sends were not all refused", ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_SENDER",
                                      "SMTP_RECIPIENTS"), _smtp),
)

# Switch-shaped things in the codebase that are NOT a mechanism waiting on a
# human, with the reason. The completeness gate reads this so that every
# .env variable, every `enabled:` flag, every host timer is either declared
# above or excused here - never silently neither.
NOT_A_SWITCH: dict[str, str] = {
    "SENTINEL_MASTER_KEY": "the service does not start without it; a missing key is a crash, not a silent hold",
    "SENTINEL_DB_PATH": "a path, not a mechanism",
    "SENTINEL_RULES_PATH": "a path, not a mechanism",
    "SENTINEL_DASHBOARD_PORT": "a port, not a mechanism",
    "SENTINEL_METRICS_PORT": "a port, not a mechanism",
    "SENTINEL_PORTAL_URL": ("cosmetic: emails carry a link, and the sign-in page names the address, only when "
                            "set; the alert is delivered either way"),
    "SENTINEL_TRUSTED_PROXIES": ("pinned in docker-compose.yml beside the proxy it names, a test holds the two "
                                 "equal; unset trusts no proxy, as before the proxy existed"),
    "SENTINEL_DASHBOARD_TRUSTED_PROXIES": ("pinned in docker-compose.yml to the same proxy address as "
                                           "SENTINEL_TRUSTED_PROXIES (Caddy fronts the dashboard at /admin), a test "
                                           "holds the two equal; unset trusts no proxy"),
    "SENTINEL_DASHBOARD_BASE_PATH": ("a URL path, not a mechanism: compose pins /admin, where Caddy forwards; "
                                     "blank serves the root"),
    "ANTHROPIC_API_KEY": "triage narrative (triage.enabled false by design): additive, never load-bearing",
    "POSTGRES_PASSWORD": "unused by the SQLite deployment; kept for the compose file",
    "triage.enabled": "additive AI narrative, deliberately off; not a mechanism a customer relies on",
}


def load_holds(path: str | None = None) -> dict:
    """{entry key: reason}. A hold with no reason, or for an entry that is
    not holdable, is refused: the file is the owner's declaration and must
    say what was decided."""
    path = path or os.environ.get(HOLDS_PATH_ENV) or DEFAULT_HOLDS_PATH
    if not os.path.exists(path):
        return {}
    import yaml

    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    holds = (data.get("holds") if isinstance(data, dict) else None) or {}
    if not isinstance(holds, dict):
        raise ValueError(f"{path}: 'holds' must be a mapping of entry -> reason")
    known = {e.key: e for e in ENTRIES}
    for key, reason in holds.items():
        if key not in known:
            raise ValueError(f"{path}: hold for unknown mechanism {key!r}")
        if not known[key].holdable:
            raise ValueError(f"{path}: {key!r} cannot be held - it is load-bearing")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{path}: hold for {key!r} needs a reason")
    return {k: v.strip() for k, v in holds.items()}


def evaluate(facts: Facts) -> list[Verdict]:
    out = []
    for entry in ENTRIES:
        if entry.key in facts.holds:
            out.append(Verdict(entry.key, HELD, f"on hold: {facts.holds[entry.key]}"))
            continue
        try:
            state, evidence = entry.check(facts)
        except Exception as exc:  # noqa: BLE001 - one broken check must not hide the others
            state, evidence = UNKNOWN, f"check raised: {exc}"
        out.append(Verdict(entry.key, state, evidence))
    return out


def failing(verdicts: list[Verdict]) -> list[Verdict]:
    """DEAD and UNKNOWN both fail: no evidence of running is no evidence."""
    return [v for v in verdicts if v.state in (DEAD, UNKNOWN)]


def render(verdicts: list[Verdict]) -> str:
    by = {e.key: e for e in ENTRIES}
    lines = []
    for v in verdicts:
        e = by[v.key]
        mark = {LIVE: "ok  ", HELD: "held", DEAD: "DEAD", UNKNOWN: "????"}[v.state]
        lines.append(f"{mark}  {e.title:<32} {v.evidence}")
        if v.state in (DEAD, UNKNOWN):
            lines.append(f"      switch on: {e.switch_on}")
    bad = failing(verdicts)
    lines.append("")
    lines.append(f"{len(bad)} mechanism(s) with no evidence of running" if bad else "every declared mechanism is live or deliberately held")
    return "\n".join(lines)


def paged_failing(db) -> list[str] | None:
    """The failing keys the last page reported, or None when no page has
    ever been recorded (a fresh install, or a database from before this
    rule) - in which case everything failing now is newly failing."""
    with db.cursor() as cur:
        cur.execute("SELECT value FROM portal_meta WHERE key = ?", (PAGED_STATE_KEY,))
        row = cur.fetchone()
    if row is None:
        return None
    try:
        return sorted(str(k) for k in json.loads(row["value"]))
    except (ValueError, TypeError):
        return None


def _remember_paged(db, keys: list[str]) -> None:
    with db.cursor() as cur:
        cur.execute("INSERT INTO portal_meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (PAGED_STATE_KEY, json.dumps(sorted(keys))))


def page_verdict(db, rules, verdicts: list[Verdict]) -> int | None:
    """The verdict as an operator page (see the module docstring for why
    that path and not another), raised on a CHANGE in the failing set and
    never on the state:

      - a mechanism that newly fails: one HIGH page naming it, with the
        rest of the set as context;
      - a set that has not changed since the last page: nothing, however
        many runs go by and whatever the alerter's cooldown says;
      - a set that only shrank (something came back, or was declared held):
        one MEDIUM report - nothing is wrong, and HIGH would escalate;
      - a held mechanism is not failing and never pages.

    The change is keyed on the set of failing mechanism KEYS: a mechanism
    moving between `dead` and `unknown` is still not running and is not
    news. The failing set itself is the page's dedup scope, so the
    alerter's cooldown can only ever suppress a page about the same set -
    never a new failure inside the hour.

    Returns the page id, or None when nothing changed, nothing fails and
    nothing had failed, or no alerter could be built."""
    current = sorted(v.key for v in failing(verdicts))
    previous = paged_failing(db)
    newly = [k for k in current if k not in (previous or [])]
    recovered = [k for k in (previous or []) if k not in current]
    if not newly and not recovered:
        return None

    from sentinel.alerts.operator import OperatorAlerter
    from sentinel.cli import build_operator_channels

    alerter = OperatorAlerter(db, build_operator_channels(rules), rules)
    by = {e.key: e for e in ENTRIES}
    state = {v.key: v for v in verdicts}

    def names(keys):
        return ", ".join(f"{by[k].title} ({state[k].state})" if k in state else by[k].title for k in keys)

    # One line per mechanism, readable through the operator format as it
    # is (evidence renders as `key: value`): the state, the fact, and the
    # switch-on act for anything failing.
    evidence = {"newly_failing": names(newly) or "none",
                "recovered": names(recovered) or "none",
                "still_failing": names([k for k in current if k not in newly]) or "none"}
    for k in current:
        evidence[k] = f"{state[k].state}: {state[k].evidence} - switch on: {by[k].switch_on}"

    scope = ",".join(current) or "-"
    if newly:
        page_id = alerter.page(
            rule=PAGE_RULE, severity="HIGH",
            message=(f"Liveness: {len(newly)} mechanism(s) newly not running: {names(newly)}. "
                     f"{len(current)} built but not running in all"
                     + (f"; {len(recovered)} recovered: {names(recovered)}" if recovered else "")
                     + ". Each one waits on a human act; `sentinel liveness` on the host prints which. "
                     "This pages again only when the failing set changes."),
            evidence=evidence, dedup_scope=scope)
    else:
        page_id = alerter.page(
            rule=RECOVERY_RULE, severity="MEDIUM",
            message=(f"Liveness: {len(recovered)} mechanism(s) no longer failing: {names(recovered)}. "
                     + (f"{len(current)} still built but not running: {names(current)}."
                        if current else "Every declared mechanism is live or deliberately held.")),
            evidence=evidence, dedup_scope=scope)
    _remember_paged(db, current)
    return page_id
