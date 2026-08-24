from __future__ import annotations

import argparse
import copy
import getpass
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from sentinel.alerts.email import EmailChannel
from sentinel.alerts.escalation import Escalator
from sentinel.alerts.telegram import TelegramChannel
from sentinel.buildinfo import build_info
from sentinel.config import RulesConfig
from sentinel.crypto import CredentialCipher
from sentinel.db import Database
from sentinel.onboarding import OnboardingRejected, onboard_account
from sentinel.rules_schema import default_for

logger = logging.getLogger("sentinel.cli")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def _build_named_channels() -> dict[str, object]:
    """Instantiates every channel that has credentials configured; the
    routing config in rules.yaml decides which severities use which."""
    named: dict[str, object] = {}

    token, chat_id = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    if token and chat_id:
        named["telegram"] = TelegramChannel(token, chat_id)

    # Optional team/VA Telegram group: the bot must be a member of the group.
    # Routed via the 'telegram_team' channel name in alerting.routing.
    team_chat = _env("TELEGRAM_TEAM_CHAT_ID")
    if token and team_chat:
        named["telegram_team"] = TelegramChannel(token, team_chat)

    # Global SMS ops channel - ClickSend preferred, Twilio fallback (see
    # sentinel/alerts/sms.build_sms_channel). Recipients come from whichever
    # provider's *_TO_NUMBERS is set.
    sms_to = _env("CLICKSEND_TO_NUMBERS") or _env("TWILIO_TO_NUMBERS")
    if sms_to:
        from sentinel.alerts.sms import build_sms_channel

        # audience="operator": these numbers are Minas's own pagers. Rule
        # names mean something to the person paged; the raw detector message
        # (amounts, addresses) still stays off their lock screens too.
        sms_channel = build_sms_channel([n.strip() for n in sms_to.split(",")],
                                        audience="operator")
        if sms_channel is not None:
            named["sms"] = sms_channel

    host, sender, recipients = _env("SMTP_HOST"), _env("SMTP_SENDER"), _env("SMTP_RECIPIENTS")
    if host and sender and recipients:
        named["email"] = EmailChannel(
            host,
            int(_env("SMTP_PORT", "587")),
            _env("SMTP_USERNAME", ""),
            _env("SMTP_PASSWORD", ""),
            sender,
            [r.strip() for r in recipients.split(",")],
        )
    return named


def build_channels(rules: RulesConfig, problems: list | None = None) -> dict[str, list]:
    """severity -> [channel objects], from alerting.routing in rules.yaml.
    'digest' entries are intentionally skipped here: MEDIUM/LOW alerts are
    stored (dedup still applies) and delivered by the `sentinel digest`
    command instead of immediately."""
    named = _build_named_channels()
    channels: dict[str, list] = {}
    for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        routed = rules.get("alerting", "routing", severity, default=None)
        if not routed:
            routed = default_for("alerting", "routing", severity) or []
            logger.error(
                "no usable alert routing for %s - falling back to %s. An alert with no "
                "channel is an alert that is never delivered.",
                severity, ", ".join(routed),
            )
            if problems is not None:
                problems.append({"kind": "fallback", "channel": None, "severity": severity})
        resolved = []
        for name in routed:
            if name == "digest":
                continue
            channel = named.get(str(name))
            if channel is None:
                logger.warning("channel '%s' routed for %s but not configured (missing env vars)", name, severity)
                # Collected rather than paged from here: the operator alerter
                # is constructed *from* the channels this function returns, so
                # paging inside it would need the thing being built. The caller
                # pages once the alerter exists (#58, group A).
                if problems is not None:
                    problems.append({"kind": "unconfigured", "channel": str(name),
                                     "severity": severity})
                continue
            resolved.append(channel)
        channels[severity] = resolved
    return channels


def build_operator_channels(rules: RulesConfig) -> dict[str, list]:
    """severity -> [channel objects], from `operator_alerting.routing` (G-49).

    Same named channels as the customer routing - they are all Minas-side
    destinations already - but resolved through a separate table, so paging
    ourselves about an outage can't be changed by retuning a customer's alert
    preferences. 'digest' is meaningless for an operator page and is skipped:
    an outage that waits for the daily digest is not a page."""
    named = _build_named_channels()
    channels: dict[str, list] = {}
    for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        routed = rules.get("operator_alerting", "routing", severity, default=None)
        if not routed:
            # Same trap as the customer routing above, and worse here: the
            # page this drops would be the one telling us the config is broken.
            routed = default_for("operator_alerting", "routing", severity) or []
            logger.error(
                "no usable operator routing for %s - falling back to %s", severity, ", ".join(routed)
            )
        resolved = []
        for name in routed:
            if name == "digest":
                continue
            channel = named.get(str(name))
            if channel is None:
                logger.warning(
                    "operator channel '%s' routed for %s but not configured (missing env vars)",
                    name, severity,
                )
                continue
            resolved.append(channel)
        channels[severity] = resolved
    return channels


def cmd_onboard(args: argparse.Namespace) -> int:
    from sentinel.operators import is_active_operator, list_operators, operator_count

    db = Database(_env("SENTINEL_DB_PATH", "./sentinel_data/sentinel.db"))
    cipher = CredentialCipher()

    # The `--as` gate (#7): creating a customer is the founding entry of the
    # account's whole record, and it must carry a name the moment names
    # exist. The dashboard's add form already attributes via its operator
    # session; this closes the CLI half. Checked BEFORE prompting for
    # secrets - a refusal after someone pasted a key is a key pasted for
    # nothing.
    actor = getattr(args, "as_operator", None)
    if operator_count(db) > 0:
        if not actor:
            names = ", ".join(o["username"] for o in list_operators(db) if o["status"] == "active")
            print(
                f"--as <operator> is required: onboarding is recorded against a name. "
                f"Active operators: {names or '(none active)'}",
                file=sys.stderr,
            )
            return 2
        if not is_active_operator(db, actor):
            print(f"'{actor}' is not an active operator", file=sys.stderr)
            return 2
    elif actor:
        print("no operator accounts exist - create one with `sentinel operator add`", file=sys.stderr)
        return 2

    exchange = args.exchange
    api_key = args.api_key or input(f"{exchange} API key: ").strip()
    secret = args.secret or getpass.getpass(f"{exchange} API secret: ").strip()
    # Only Bitget and OKX keys carry a passphrase; don't prompt (or hang a
    # non-interactive run) for exchanges that have none.
    if exchange in ("bybit", "binance"):
        passphrase = ""
    else:
        passphrase = args.passphrase or getpass.getpass(f"{exchange} API passphrase: ").strip()

    try:
        result = onboard_account(
            db, cipher, label=args.label, topology=args.topology,
            api_key=api_key, secret=secret, passphrase=passphrase,
            exchange=exchange, alert_email=args.alert_email, alert_phone=args.alert_phone,
            actor=actor,
        )
    except OnboardingRejected as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 1

    print(f"Onboarded '{result.label}' ({exchange}) as account id {result.account_id}")

    # No operator alerter on this path, and deliberately none: the person who
    # ran this command is standing in front of the output, and paging somebody
    # about something they are currently reading is how a channel gets muted.
    # But the gap must not be silent, so it is printed loudly here and recorded
    # as a `baseline_seed_incomplete` event on the account either way (#58,
    # group C).
    with db.cursor() as cur:
        cur.execute(
            "SELECT payload_json FROM events WHERE account_id = ? "
            "AND event_type = 'baseline_seed_incomplete'",
            (result.account_id,),
        )
        gaps = [r["payload_json"] for r in cur.fetchall()]
    if gaps:
        print(
            f"\nWARNING: {len(gaps)} part(s) of this account's history could not be read from "
            f"the exchange. It is monitored, but detectors that compare against history will "
            f"raise false positives on activity this customer has always had:",
            file=sys.stderr,
        )
        for gap in gaps:
            print(f"  {gap}", file=sys.stderr)
        print("Re-onboard or re-seed once the exchange endpoint is working.", file=sys.stderr)
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    """What is running, and what it was built from.

    Answers the question a deploy otherwise leaves to trust. `sudo docker
    compose exec -T sentinel sentinel version` after an upgrade is the
    difference between knowing the new image is serving and assuming it,
    which is the failure this codebase keeps closing elsewhere.

    Exits 1 when the commit is unknown - a build that cannot say what it is
    should not report success to a script checking whether the deploy landed.

    `--machine` prints `key=value` lines instead. That form is a CONTRACT:
    scripts/deploy.sh greps it to assert the running container is the tag it
    was asked to deploy, so its shape is pinned by tests on this side - the
    side a reformat would happen on - and the human block above it is free
    to change. (Images older than this flag are parsed by their human
    output instead, which is safe precisely because those images are already
    built and their format can never change.)
    """
    info = build_info()
    if getattr(args, "machine", False):
        for key in ("version", "commit", "tag", "source"):
            print(f"{key}={info[key]}")
        return 1 if info["commit"] == "unknown" else 0
    print(f"Minas AI Guardian {info['version']}")
    print(f"  commit   {info['commit']}")
    print(f"  tag      {info['tag']}")
    print(f"  source   {info['source']}")
    if info["commit"] == "unknown":
        print(
            "\nThis build does not know which commit it came from: it was built "
            "without\nGIT_SHA and has no checkout to read. Deploy via "
            "docs/deployment.md, which\npasses it through, if you need this to "
            "identify what is running.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_validate_config(args: argparse.Namespace) -> int:
    """Checks a rules file against the schema and reports, without starting
    anything or opening the database.

    Meant to be run against a *server's* live rules file before an upgrade,
    from a shell, as a deploy gate. The service itself no longer refuses to
    start on a rejected config - refusing turns an upgrade into an outage - so
    this is where failing fast lives: before the deploy rather than during it.

    Exit codes are the contract, since this gets called from deploy scripts:
      0  valid
      1  read, but the schema rejects it
      2  could not be read at all

    1 and 2 are kept apart on purpose. A typo in the config and a typo in the
    path need different reactions, and a script that treats them the same will
    report a missing file as a bad config."""
    import yaml as _yaml

    from sentinel.config import _to_decimal_leaves
    from sentinel.rules_schema import validate

    path = Path(getattr(args, "path", None) or "./config/rules.yaml")

    try:
        with open(path, "r") as f:
            raw_text = f.read()
    except OSError as exc:
        print(f"FAIL  {path}\n      could not be read: {exc}", file=sys.stderr)
        return 2

    try:
        raw = _yaml.safe_load(raw_text) or {}
    except _yaml.YAMLError as exc:
        print(f"FAIL  {path}\n      not valid YAML: {exc}", file=sys.stderr)
        _print_degraded_warning()
        return 1

    problems = validate(_to_decimal_leaves(raw))
    if not problems:
        print(f"OK    {path}\n      valid — every setting is recognised and in range.")
        return 0

    plural = "problem" if len(problems) == 1 else "problems"
    print(f"FAIL  {path}\n      {len(problems)} {plural}:", file=sys.stderr)
    for problem in problems:
        print(f"        - {problem}", file=sys.stderr)
    _print_degraded_warning()
    return 1


def _print_degraded_warning() -> None:
    """What happens if they deploy it anyway.

    Worth printing rather than leaving them to infer: the answer changed when
    the service stopped refusing to start, and "it won't come up" is the thing
    an operator would otherwise reasonably assume."""
    print(
        "\n      Deploying this will NOT stop the service from starting. It will run on\n"
        "      the settings that validate plus built-in defaults for the rest, and raise\n"
        "      a CRITICAL operator page. The thresholds in force would not be the ones\n"
        "      in this file.",
        file=sys.stderr,
    )


def cmd_run(args: argparse.Namespace) -> int:
    _setup_logging()
    from sentinel.alerts.contact import ContactRouter
    from sentinel.alerts.operator import OperatorAlerter
    from sentinel.dashboard import serve_dashboard
    from sentinel.engine import Engine
    from sentinel.events import record_config_changes
    from sentinel.metrics import Metrics

    db = Database(_env("SENTINEL_DB_PATH", "./sentinel_data/sentinel.db"))
    cipher = CredentialCipher()
    # Threshold changes go into the audit trail (G-12, #16). Wired here rather
    # than inside RulesConfig so the config layer stays free of the database -
    # it is constructed and validated before there is one.
    rules = RulesConfig(
        _env("SENTINEL_RULES_PATH", "./config/rules.yaml"),
        on_change=lambda changes: record_config_changes(db, changes),
    )
    routing_problems: list = []
    channels = build_channels(rules, problems=routing_problems)

    metrics = Metrics()
    metrics_port = int(_env("SENTINEL_METRICS_PORT", "9109"))
    metrics_host = _env("SENTINEL_METRICS_HOST", "127.0.0.1")
    Metrics.serve(metrics_port, host=metrics_host)
    # Loopback by default: the dashboard has no login of its own, so the
    # quickstart must not put it on every interface (G-36). Widening either
    # one is deliberate, and logged below so it's visible at startup rather
    # than discovered by a port scan.
    dashboard_port = int(_env("SENTINEL_DASHBOARD_PORT", "8390"))
    dashboard_host = _env("SENTINEL_DASHBOARD_HOST", "127.0.0.1")
    # `rules` so the page can say when config/rules.yaml was rejected and the
    # thresholds in force are older than the file (G-12).
    operator_alerter = OperatorAlerter(db, build_operator_channels(rules), rules)

    # Routing problems are collected by build_channels above and paged here,
    # once the alerter exists - it is built *from* those channels, so this
    # cannot happen any earlier (#58, group A). A whole severity routed to a
    # channel that was never configured is under-delivered for the entire run,
    # and the log line it used to be is printed once into a file nobody reads.
    for problem in routing_problems:
        if problem["kind"] == "fallback":
            operator_alerter.page(
                rule="alert_routing_fell_back_to_defaults",
                severity="HIGH",
                message=(
                    f"No usable alert routing is configured for {problem['severity']}, so the "
                    f"built-in defaults are in use. Alerts of that severity are going wherever "
                    f"the defaults point, which is not a choice anyone made for this deployment."
                ),
                evidence=problem,
                dedup_scope=f"routing-fallback:{problem['severity']}",
            )
        else:
            operator_alerter.page(
                rule="alert_channel_routed_but_not_configured",
                severity="HIGH",
                message=(
                    f"Channel '{problem['channel']}' is routed for {problem['severity']} alerts "
                    f"but has no configuration, so it delivers nothing. Every {problem['severity']} "
                    f"alert is under-delivered for as long as this process runs. Check the "
                    f"environment variables for that channel."
                ),
                evidence=problem,
                dedup_scope=f"{problem['channel']}:{problem['severity']}",
            )

    # The self-serve Telegram handshake (#18) needs someone watching the
    # bot's updates. Absent token = the feature is off and the portal simply
    # never shows a working deep link; said in the log rather than silently.
    telegram_token = _env("TELEGRAM_BOT_TOKEN")
    if telegram_token:
        from sentinel.alerts.telegram_link import TelegramLinkWorker

        TelegramLinkWorker(db, telegram_token).start()
        logger.info("telegram link worker started")
    else:
        logger.info("telegram link worker not started - TELEGRAM_BOT_TOKEN unset")

    serve_dashboard(db, cipher, dashboard_port, host=dashboard_host, rules=rules,
                    operator_alerter=operator_alerter)

    from sentinel.portal import serve_portal

    portal_port = int(_env("SENTINEL_PORTAL_PORT", "8391"))
    portal_host = _env("SENTINEL_PORTAL_HOST", "127.0.0.1")
    serve_portal(db, portal_port, host=portal_host, rules=rules, cipher=cipher,
                 metrics=metrics, operator_alerter=operator_alerter)
    logger.info(
        "dashboard on %s:%d, member portal on %s:%d, metrics on %s:%d",
        dashboard_host, dashboard_port, portal_host, portal_port, metrics_host, metrics_port,
    )
    level, message = bind_disclosure(
        dashboard_host, dashboard_port, _env("SENTINEL_BIND_GATE"))
    if level:
        getattr(logger, level)(message)

    # One router, shared. The escalator needs it to reach the customer's own
    # contacts (#27) and it needs the alerter to report a channel with no
    # provider (#58); a privately-constructed one inside Engine could do
    # neither.
    contact_router = ContactRouter(db=db, rules=rules, operator_alerter=operator_alerter)

    escalation_minutes = rules.get_int("alerting", "escalation_unacknowledged_minutes", default=15)
    escalation_minutes_high = rules.get_int(
        "alerting", "escalation_unacknowledged_minutes_high", default=60)
    escalator = Escalator(channels.get("CRITICAL", []), escalation_minutes,
                          operator_alerter=operator_alerter,
                          contact_router=contact_router,
                          high_unacknowledged_minutes=escalation_minutes_high)

    engine = Engine(
        db, cipher, rules, channels, metrics=metrics, escalator=escalator,
        operator_alerter=operator_alerter, contact_router=contact_router,
    )
    engine.run()
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    from sentinel.alerts.digest import send_digest, send_member_digests

    db = Database(_env("SENTINEL_DB_PATH", "./sentinel_data/sentinel.db"))
    # Constructing it validates config/rules.yaml and refuses to run the
    # digest on a config the poller would also be rejecting (G-12).
    rules = RulesConfig(_env("SENTINEL_RULES_PATH", "./config/rules.yaml"))
    named = _build_named_channels()
    # The ops digest goes to telegram + email when configured.
    targets = [c for name, c in named.items() if name in ("telegram", "email")]
    if not targets:
        print("no digest channels configured (need Telegram or SMTP env vars)", file=sys.stderr)
        return 1
    sent = send_digest(db, targets, hours=args.hours)
    print("ops digest sent" if sent else "nothing to report - no ops digest sent")
    # The member proof-of-life digests (#21) ride the same daily cron: each
    # contact's own choice (daily/weekly/off) decides, and the per-day mark
    # makes a re-run a no-op, so running this twice cannot double-send.
    members = send_member_digests(db, rules)
    print(f"member digests sent: {members}")
    return 0


def cmd_ack(args: argparse.Namespace) -> int:
    """Acknowledge an alert, by name.

    `--as` is required once operator accounts exist, and not before. That is
    the upgrade path: a deployment with no operators keeps working exactly as
    it did, and attribution becomes mandatory the moment there is somebody to
    attribute to (G-50). An unattributed ack on a system that has names would
    be a hole in the record straight through the one command that is run from a
    shell by whoever has SSH."""
    from sentinel.operators import acknowledge_alert, is_active_operator, list_operators, operator_count

    db = Database(_env("SENTINEL_DB_PATH", "./sentinel_data/sentinel.db"))
    actor = getattr(args, "as_operator", None)

    if operator_count(db) > 0:
        if not actor:
            names = ", ".join(o["username"] for o in list_operators(db) if o["status"] == "active")
            print(
                f"--as <operator> is required: this alert's acknowledgement is recorded "
                f"against a name. Active operators: {names or '(none active)'}",
                file=sys.stderr,
            )
            return 2
        if not is_active_operator(db, actor):
            print(f"'{actor}' is not an active operator", file=sys.stderr)
            return 2
    elif actor:
        print("no operator accounts exist - create one with `sentinel operator add`", file=sys.stderr)
        return 2

    if acknowledge_alert(db, args.alert_id, actor):
        by = f" by {actor}" if actor else ""
        print(f"alert {args.alert_id} acknowledged{by}")
        return 0
    print(f"alert {args.alert_id} not found or already acknowledged", file=sys.stderr)
    return 1


def bind_disclosure(host: str, port: int, gate: str | None) -> tuple[str | None, str | None]:
    """What startup says about the dashboard's bind (#76).

    A security warning that always fires is how a real one gets scrolled
    past. docker-compose deliberately binds the container to 0.0.0.0 and
    gates it with a 127.0.0.1: publish on the host - a boundary this process
    cannot see - so warning on the internal bind alone cried wolf on every
    correctly-secured deployment. The deployment therefore DECLARES its
    boundary: SENTINEL_BIND_GATE, set in docker-compose.yml next to the
    publish it describes, so the two travel in the same diff. Three states,
    kept distinct:

    - loopback: nothing to disclose (the info line above logs every bind);
    - wide with a declared gate: INFO naming the gate and the one host-side
      command that verifies it - stated as unverifiable from in here,
      because it is; claiming safety would be assuming, not saying;
    - wide with no declared gate: the warning, at full volume. That case is
      the real G-36 exposure, and the fix for a warning that always fires
      is precision, never deletion.
    """
    if host in ("127.0.0.1", "localhost", "::1"):
        return (None, None)
    if gate:
        return ("info", (
            f"ops dashboard binds {host}:{port} inside its container; the declared "
            f"boundary is: {gate}. This process cannot verify the host side - "
            f"confirm with `ss -tlnp | grep {port}` on the host "
            f"(expect 127.0.0.1:{port})."))
    return ("warning", (
        f"ops dashboard is bound to {host}:{port}, not loopback, with no declared "
        f"boundary (SENTINEL_BIND_GATE is unset). Sign-in is required once operator "
        f"accounts exist, but until the first operator is created the page is open - "
        f"and a sign-in page need not face every network either. Put a VPN, SSH "
        f"tunnel or authenticating proxy in front, or declare the external gate in "
        f"SENTINEL_BIND_GATE next to the publish that provides it."))


def cmd_account_status(args: argparse.Namespace) -> int:
    """Disable an account (offboarding, G-50) or re-enable it.

    Disable, never delete: the baselines survive, so re-enabling resumes
    monitoring without the false CRITICALs an empty baseline produces. The
    `--as` gate is `cmd_ack`'s, verbatim in spirit: attribution becomes
    mandatory the moment operator accounts exist, and not before."""
    from sentinel.onboarding import set_account_status
    from sentinel.operators import is_active_operator, list_operators, operator_count

    db = Database(_env("SENTINEL_DB_PATH", "./sentinel_data/sentinel.db"))
    actor = getattr(args, "as_operator", None)

    if operator_count(db) > 0:
        if not actor:
            names = ", ".join(o["username"] for o in list_operators(db) if o["status"] == "active")
            print(
                f"--as <operator> is required: this change is recorded against a name. "
                f"Active operators: {names or '(none active)'}",
                file=sys.stderr,
            )
            return 2
        if not is_active_operator(db, actor):
            print(f"'{actor}' is not an active operator", file=sys.stderr)
            return 2
    elif actor:
        print("no operator accounts exist - create one with `sentinel operator add`", file=sys.stderr)
        return 2

    status = "disabled" if args.action == "disable" else "active"
    try:
        changed = set_account_status(db, args.label, status, actor)
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    verb = "disabled" if status == "disabled" else "enabled"
    if changed:
        by = f" by {actor}" if actor else ""
        extra = " - it will not be polled or alerted on until re-enabled" if status == "disabled" else ""
        print(f"account '{args.label}' {verb}{by}{extra}")
    else:
        print(f"account '{args.label}' was already {verb} - nothing recorded")
    return 0


def cmd_set_threshold(args: argparse.Namespace) -> int:
    """Change one threshold, attributably (G-12, #16).

    The point of this command is not convenience - editing the YAML is easier -
    it is that an edit made through here carries a name and an edit made with
    `vi` cannot. Both are recorded; only this one can say who.

    It writes the value and lets the running service pick the change up through
    the normal mtime reload, rather than reaching into a process it does not
    own. The audit row is written here, before the file, so a crash between the
    two leaves a recorded change that did not take effect - visible and
    harmless - rather than an unrecorded change that did."""
    import yaml

    from sentinel.config import _to_decimal_leaves, settings_diff
    from sentinel.events import record_config_changes
    from sentinel.operators import is_active_operator, list_operators, operator_count
    from sentinel.rules_schema import validate

    db = Database(_env("SENTINEL_DB_PATH", "./sentinel_data/sentinel.db"))
    actor = getattr(args, "as_operator", None)
    if operator_count(db) > 0:
        if not actor:
            names = ", ".join(o["username"] for o in list_operators(db) if o["status"] == "active")
            print(
                f"--as <operator> is required: a threshold change is recorded against a "
                f"name. Active operators: {names or '(none active)'}",
                file=sys.stderr,
            )
            return 2
        if not is_active_operator(db, actor):
            print(f"'{actor}' is not an active operator", file=sys.stderr)
            return 2
    elif actor:
        print("no operator accounts exist - create one with `sentinel operator add`", file=sys.stderr)
        return 2

    # Per-account (#17): one customer's sensitivity, the global file
    # untouched. Attributed by the gate above, diff-evented on /activity by
    # the overrides module, validated against the schema's own leaves.
    account_label = getattr(args, "account", None)
    if account_label:
        from sentinel.overrides import clear_override, set_override

        try:
            if getattr(args, "clear", False):
                existed = clear_override(db, account_label, args.setting, actor)
                print(f"{account_label}: {args.setting} "
                      + ("override cleared - back to the defaults" if existed
                         else "had no override - nothing recorded"))
                return 0
            if args.value is None:
                print("a value is required (or pass --clear)", file=sys.stderr)
                return 2
            previous = set_override(db, account_label, args.setting, args.value, actor)
            if previous == args.value:
                print(f"{account_label}: {args.setting} is already {args.value}")
            else:
                was = f"was {previous}" if previous is not None else "was: the default"
                by = f" (by {actor})" if actor else ""
                print(f"{account_label}: {args.setting} = {args.value} ({was}){by}")
            return 0
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except LookupError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    if getattr(args, "clear", False):
        print("--clear needs --account: the global config is edited by setting a value", file=sys.stderr)
        return 2
    if args.value is None:
        print("a value is required", file=sys.stderr)
        return 2

    path = Path(_env("SENTINEL_RULES_PATH", "./config/rules.yaml"))
    try:
        before = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 1

    segments = args.setting.split(".")
    if len(segments) < 2:
        print("setting must be dotted, e.g. polling.fast_loop_seconds", file=sys.stderr)
        return 2

    after = copy.deepcopy(before)
    node = after
    for segment in segments[:-1]:
        node = node.setdefault(segment, {})
        if not isinstance(node, dict):
            print(f"{args.setting}: '{segment}' is a value, not a section", file=sys.stderr)
            return 2
    node[segments[-1]] = yaml.safe_load(args.value)

    problems = validate(_to_decimal_leaves(after))
    if problems:
        # Refused here rather than written and rejected on reload. A bad value
        # written to disk means the service runs on defaults for that section
        # until someone notices, which is the G-12 failure with an extra step.
        for problem in problems:
            print(f"rejected: {problem}", file=sys.stderr)
        return 2

    changes = settings_diff(before, after)
    if not changes:
        print(f"{args.setting} is already {args.value}")
        return 0

    record_config_changes(db, changes, actor=actor)
    path.write_text(yaml.safe_dump(after, sort_keys=False, default_flow_style=False))
    for setting, old, new in changes:
        print(f"{setting}: {old} -> {new}" + (f" (by {actor})" if actor else ""))
    return 0


def cmd_operator(args: argparse.Namespace) -> int:
    """Manage operator accounts. Offboarding is `disable`, never a DELETE:
    removing the row would orphan every action the person ever took."""
    from sentinel.operators import (
        create_operator,
        disable_operator,
        enable_operator,
        list_operators,
        set_operator_password,
    )

    db = Database(_env("SENTINEL_DB_PATH", "./sentinel_data/sentinel.db"))

    if args.operator_command == "list":
        rows = list_operators(db)
        if not rows:
            print("no operator accounts yet - the ops dashboard is unprotected until one exists")
            return 0
        print(f"{'USERNAME':<20}{'STATUS':<12}{'LAST LOGIN':<28}NAME")
        for row in rows:
            print(f"{row['username']:<20}{row['status']:<12}"
                  f"{(row['last_login_at'] or '—'):<28}{row['display_name'] or ''}")
        return 0

    username = args.username
    if args.operator_command in ("add", "passwd"):
        password = args.password or getpass.getpass("password: ")
        confirm = args.password or getpass.getpass("confirm: ")
        if password != confirm:
            print("passwords do not match", file=sys.stderr)
            return 1
        if len(password) < 12:
            # Not a policy, a floor. These accounts gate every customer's
            # contact details and every stored API key.
            print("password must be at least 12 characters", file=sys.stderr)
            return 1

    try:
        if args.operator_command == "add":
            create_operator(db, username, password, display_name=args.name)
            print(f"operator '{username}' created - the ops dashboard now requires a sign-in")
        elif args.operator_command == "passwd":
            if not set_operator_password(db, username, password):
                print(f"no such operator '{username}'", file=sys.stderr)
                return 1
            print(f"password updated for '{username}'")
        elif args.operator_command == "disable":
            if not disable_operator(db, username):
                print(f"no such operator '{username}'", file=sys.stderr)
                return 1
            print(f"operator '{username}' disabled - existing sessions stop working immediately")
        elif args.operator_command == "enable":
            if not enable_operator(db, username):
                print(f"no such operator '{username}'", file=sys.stderr)
                return 1
            print(f"operator '{username}' enabled")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def cmd_set_contact(args: argparse.Namespace) -> int:
    """Set/clear an existing account's alert destinations. Pass
    --alert-email/--alert-phone to set the primary; --clear to remove both.
    --alert-email-secondary/--alert-phone-secondary set the emergency
    contact; --clear-secondary removes it (#91 - until then the secondary
    was write-once at onboarding, and honouring a removal request meant SQL
    against production). Pass --sms-alerts on|off to toggle the member's SMS
    notification preference (CRITICAL alerts may still page per
    alerting.sms_critical_overrides_optout).

    Each destination moves only when its own flags are present: a
    secondary-only change cannot disturb where the alerts themselves go, and
    an invocation with no flags at all changes nothing (it used to clear the
    primary - a footgun, not a feature, removed with #91)."""
    from sentinel.events import (
        append_event,
        notify_alert_destination_change,
        notify_secondary_contact_added,
    )
    from sentinel.operators import is_active_operator, list_operators, operator_count

    db = Database(_env("SENTINEL_DB_PATH", "./sentinel_data/sentinel.db"))

    # The `--as` gate, in cmd_disable_account's mold (#80): attribution is
    # mandatory the moment operator accounts exist, and not before. Where the
    # alerts go is the setting an attacker changes first, so the row that
    # records the change carries a name once names exist.
    actor = getattr(args, "as_operator", None)
    if operator_count(db) > 0:
        if not actor:
            names = ", ".join(o["username"] for o in list_operators(db) if o["status"] == "active")
            print(
                f"--as <operator> is required: this change is recorded against a name. "
                f"Active operators: {names or '(none active)'}",
                file=sys.stderr,
            )
            return 2
        if not is_active_operator(db, actor):
            print(f"'{actor}' is not an active operator", file=sys.stderr)
            return 2
    elif actor:
        print("no operator accounts exist - create one with `sentinel operator add`", file=sys.stderr)
        return 2

    # (event_type, payload) rows to write once the UPDATE has landed. Where
    # the alerts about an account go is a setting whose silent change is an
    # attack (redirect the alerts, then drain the account), so every actual
    # change leaves an event row the customer can read (G-32, #36). Only
    # diffs are recorded: re-running the command with the same values is not
    # a change and must not manufacture one in a forensic log.
    changes: list[tuple[str, dict]] = []
    # getattr throughout: tests (and older callers) build the Namespace by
    # hand without the newer optional flags.
    sms_alerts = getattr(args, "sms_alerts", None)
    sec_email_arg = getattr(args, "alert_email_secondary", None)
    sec_phone_arg = getattr(args, "alert_phone_secondary", None)
    clear_secondary = getattr(args, "clear_secondary", False)
    primary_requested = bool(args.clear) or args.alert_email is not None \
        or args.alert_phone is not None
    secondary_requested = clear_secondary or sec_email_arg is not None \
        or sec_phone_arg is not None
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, alert_email, alert_phone, alert_email_secondary, "
            "alert_phone_secondary, sms_alerts_enabled FROM accounts WHERE label = ?",
            (args.label,),
        )
        before = cur.fetchone()
        if before is None:
            print(f"no account labeled '{args.label}'", file=sys.stderr)
            return 1
        account_id = before["id"]
        now = datetime.now(timezone.utc).isoformat()
        if sms_alerts is not None:
            enabled = 1 if sms_alerts == "on" else 0
            cur.execute(
                "UPDATE accounts SET sms_alerts_enabled = ?, updated_at = ? WHERE label = ?",
                (enabled, now, args.label),
            )
            if enabled != before["sms_alerts_enabled"]:
                changes.append(("sms_alerts_changed",
                                {"from": bool(before["sms_alerts_enabled"]),
                                 "to": bool(enabled)}))
        if args.clear:
            email, phone = None, None
        else:
            email, phone = args.alert_email, args.alert_phone
        # Canonical on write (#90): emails normalized, blank-as-NULL - and
        # BEFORE the diff below, so re-typing a stored address in different
        # case is recognised as no change rather than manufactured as one.
        from sentinel.portal import canonical_contact_email, canonical_contact_phone

        email, phone = canonical_contact_email(email), canonical_contact_phone(phone)
        if primary_requested:
            cur.execute(
                "UPDATE accounts SET alert_email = ?, alert_phone = ?, updated_at = ? WHERE label = ?",
                (email, phone, now, args.label),
            )
            if (email, phone) != (before["alert_email"], before["alert_phone"]):
                changes.append(("alert_contact_updated",
                                {"email_from": before["alert_email"], "email_to": email,
                                 "phone_from": before["alert_phone"], "phone_to": phone}))
        if secondary_requested:
            sec_email, sec_phone = (None, None) if clear_secondary \
                else (canonical_contact_email(sec_email_arg),
                      canonical_contact_phone(sec_phone_arg))
            cur.execute(
                "UPDATE accounts SET alert_email_secondary = ?, alert_phone_secondary = ?, "
                "updated_at = ? WHERE label = ?",
                (sec_email, sec_phone, now, args.label),
            )
            if (sec_email, sec_phone) != (before["alert_email_secondary"],
                                          before["alert_phone_secondary"]):
                changes.append(("secondary_contact_updated",
                                {"email_from": before["alert_email_secondary"],
                                 "email_to": sec_email,
                                 "phone_from": before["alert_phone_secondary"],
                                 "phone_to": sec_phone}))
    for event_type, payload in changes:
        # `actor` is the --as name once the gate above captured one, and None
        # before operator accounts exist - the unattributed row IS the record
        # then (see the actor note on `config_changes` in schema.sql).
        append_event(db, account_id, event_type, "LOW", payload, actor=actor)
        if event_type == "alert_contact_updated":
            # The address being removed loses sight of this account the moment
            # the change lands, so the record of it never reaches them. See
            # notify_alert_destination_change.
            notify_alert_destination_change(
                db, args.label, payload["email_from"], payload["email_to"])
        elif event_type == "secondary_contact_updated" \
                and payload["email_to"] and payload["email_to"] != payload["email_from"]:
            # A newly-listed emergency contact gets the one-time note saying
            # what the role means (#89). The diff-guard above makes it fire
            # exactly once per addition - a re-run with the same address is
            # not a change and sends nothing.
            notify_secondary_contact_added(db, args.label, payload["email_to"])
    parts = []
    if primary_requested:
        parts.append(f"email={email or '-'} phone={phone or '-'}")
    if secondary_requested:
        parts.append(f"secondary_email={sec_email or '-'} secondary_phone={sec_phone or '-'}")
    if sms_alerts is not None:
        parts.append(f"sms_alerts={sms_alerts}")
    if not parts:
        parts.append(
            f"unchanged: email={before['alert_email'] or '-'} "
            f"phone={before['alert_phone'] or '-'} "
            f"secondary_email={before['alert_email_secondary'] or '-'} "
            f"secondary_phone={before['alert_phone_secondary'] or '-'}")
    print(f"contact for '{args.label}': " + " ".join(parts))
    return 0


def cmd_cancel_pause(args: argparse.Namespace) -> int:
    """Cancel a member's pending pause countdown (#98) - the Minas half of
    "cancellable by the member OR by Minas", for the owner phoning in from
    outside the portal. Same --as gate as every attributed action."""
    from sentinel.operators import is_active_operator, list_operators, operator_count
    from sentinel.pause import cancel_pause

    db = Database(_env("SENTINEL_DB_PATH", "./sentinel_data/sentinel.db"))
    actor = getattr(args, "as_operator", None)
    if operator_count(db) > 0:
        if not actor:
            names = ", ".join(o["username"] for o in list_operators(db) if o["status"] == "active")
            print(
                f"--as <operator> is required: cancelling a pause is recorded against a "
                f"name. Active operators: {names or '(none active)'}",
                file=sys.stderr,
            )
            return 2
        if not is_active_operator(db, actor):
            print(f"'{actor}' is not an active operator", file=sys.stderr)
            return 2
    elif actor:
        print("no operator accounts exist - create one with `sentinel operator add`", file=sys.stderr)
        return 2

    with db.cursor() as cur:
        cur.execute("SELECT id FROM accounts WHERE label = ?", (args.label,))
        row = cur.fetchone()
    if row is None:
        print(f"no account labeled '{args.label}'", file=sys.stderr)
        return 1
    if cancel_pause(db, int(row["id"]), f"operator {actor}" if actor else "operator (unattributed)",
                    who="Minas"):
        by = f" by {actor}" if actor else ""
        print(f"pending pause for '{args.label}' cancelled{by}")
    else:
        print(f"'{args.label}' has no pending pause - nothing recorded")
    return 0


def cmd_triage(args: argparse.Namespace) -> int:
    from sentinel.triage import TriageEngine

    db = Database(_env("SENTINEL_DB_PATH", "./sentinel_data/sentinel.db"))
    rules = RulesConfig(_env("SENTINEL_RULES_PATH", "./config/rules.yaml"))
    engine = TriageEngine(rules)
    if not engine.enabled:
        print("triage is disabled (set triage.enabled: true in rules.yaml and ANTHROPIC_API_KEY)", file=sys.stderr)
        return 1
    with db.cursor() as cur:
        cur.execute("SELECT id FROM accounts WHERE label = ?", (args.label,))
        row = cur.fetchone()
    if row is None:
        print(f"no account labeled '{args.label}'", file=sys.stderr)
        return 1
    narrative = engine.narrate_incident(db, row["id"])
    print(narrative or "no events to triage")
    return 0


def main() -> None:
    """Parse arguments and run a command. Reads the environment; never fills it.

    `load_dotenv()` used to be the first line of this function, and two tests
    call `main()` - so running the suite on a machine with a real `.env` wrote
    every production credential into `os.environ` for the rest of the pytest
    process, where later tests picked them up and used them (#68). It lives in
    `cli_entry` below now, which nothing but the installed command reaches.

    Keeping it out is a property of *this* function, not of the tests: a test
    calling `main()` cannot opt into the leak by forgetting something."""
    parser = argparse.ArgumentParser(prog="sentinel")
    sub = parser.add_subparsers(dest="command", required=True)

    onboard_parser = sub.add_parser("onboard", help="Verify and register a read-only exchange key")
    onboard_parser.add_argument("--label", required=True)
    onboard_parser.add_argument("--as", dest="as_operator", metavar="OPERATOR",
                                help="record the onboarding against an operator (required once operators exist)")
    onboard_parser.add_argument("--exchange", choices=["bitget", "bybit", "okx", "binance"], default="bitget")
    onboard_parser.add_argument("--topology", choices=["independent", "sub_account"], default="independent")
    onboard_parser.add_argument("--api-key")
    onboard_parser.add_argument("--secret")
    onboard_parser.add_argument("--passphrase")
    onboard_parser.add_argument("--alert-email", help="per-account alert destination (email)")
    onboard_parser.add_argument("--alert-phone", help="per-account alert destination (E.164 phone)")
    onboard_parser.set_defaults(func=cmd_onboard)

    version_parser = sub.add_parser(
        "version",
        help="Print the running version and the commit it was built from",
    )
    version_parser.add_argument(
        "--machine", action="store_true",
        help="key=value lines for scripts (deploy.sh asserts against this)")
    version_parser.set_defaults(func=cmd_version)

    validate_parser = sub.add_parser(
        "validate-config",
        help="Check a rules file against the schema (no database, nothing started)",
    )
    validate_parser.add_argument(
        "path", nargs="?", default=None,
        help="Path to the rules file (default: ./config/rules.yaml)",
    )
    validate_parser.set_defaults(func=cmd_validate_config)

    run_parser = sub.add_parser("run", help="Run the monitoring engine (workers, rate limits, breakers)")
    run_parser.set_defaults(func=cmd_run)

    digest_parser = sub.add_parser("digest", help="Send the MEDIUM/LOW daily digest now")
    digest_parser.add_argument("--hours", type=int, default=24)
    digest_parser.set_defaults(func=cmd_digest)

    operator_parser = sub.add_parser("operator", help="Manage operator accounts (ops dashboard sign-in)")
    operator_sub = operator_parser.add_subparsers(dest="operator_command", required=True)
    for name, help_text in (
        ("add", "Create an operator account"),
        ("passwd", "Change an operator's password"),
        ("disable", "Disable an operator (offboarding; keeps their name for attribution)"),
        ("enable", "Re-enable a disabled operator"),
    ):
        sp = operator_sub.add_parser(name, help=help_text)
        sp.add_argument("username")
        if name in ("add", "passwd"):
            sp.add_argument("--password", help="Prompted for if omitted")
        if name == "add":
            sp.add_argument("--name", help="Display name")
    operator_sub.add_parser("list", help="List operator accounts")
    operator_parser.set_defaults(func=cmd_operator)

    ack_parser = sub.add_parser("ack", help="Acknowledge an alert by id (stops CRITICAL escalation)")
    ack_parser.add_argument("--as", dest="as_operator", help="Operator acknowledging this alert")
    ack_parser.add_argument("alert_id", type=int)
    ack_parser.set_defaults(func=cmd_ack)

    for action, help_text in (
        ("disable-account", "Stop polling and alerting on an account (offboarding - disable, never delete)"),
        ("enable-account", "Resume polling and alerting on a disabled account"),
    ):
        account_status_parser = sub.add_parser(action, help=help_text)
        account_status_parser.add_argument("--as", dest="as_operator", help="Operator making this change")
        account_status_parser.add_argument("label", help="The account's label")
        account_status_parser.set_defaults(func=cmd_account_status,
                                           action=action.split("-", 1)[0])

    set_threshold_parser = sub.add_parser(
        "set-threshold", help="Change one detection threshold, recorded against an operator")
    set_threshold_parser.add_argument("--as", dest="as_operator", help="Operator making this change")
    set_threshold_parser.add_argument("--account", help="apply to ONE account only (#17); the global config is untouched")
    set_threshold_parser.add_argument("--clear", action="store_true",
                                      help="with --account: remove the override, back to the defaults")
    set_threshold_parser.add_argument("setting", help="Dotted path, e.g. detectors.withdrawal_burst.min_count")
    set_threshold_parser.add_argument("value", nargs="?", default=None)
    set_threshold_parser.set_defaults(func=cmd_set_threshold)

    cancel_pause_parser = sub.add_parser(
        "cancel-pause", help="Cancel a member's pending 24-hour pause countdown (#98)")
    cancel_pause_parser.add_argument("--label", required=True)
    cancel_pause_parser.add_argument("--as", dest="as_operator", metavar="OPERATOR",
                                     help="record the cancellation against an operator")
    cancel_pause_parser.set_defaults(func=cmd_cancel_pause)

    triage_parser = sub.add_parser("triage", help="Print an AI incident narrative for an account's recent events")
    triage_parser.add_argument("--label", required=True)
    triage_parser.set_defaults(func=cmd_triage)

    contact_parser = sub.add_parser("set-contact", help="Set/clear an account's alert destinations (primary and emergency contact)")
    contact_parser.add_argument("--label", required=True)
    contact_parser.add_argument("--alert-email")
    contact_parser.add_argument("--alert-phone")
    contact_parser.add_argument("--clear", action="store_true", help="remove the primary email and phone")
    contact_parser.add_argument("--alert-email-secondary", help="the emergency contact's email (#91)")
    contact_parser.add_argument("--alert-phone-secondary", help="the emergency contact's phone")
    contact_parser.add_argument("--clear-secondary", action="store_true",
                                help="remove the emergency contact (email and phone)")
    contact_parser.add_argument("--as", dest="as_operator", metavar="OPERATOR",
                                help="record this change against an operator (required once operators exist)")
    contact_parser.add_argument(
        "--sms-alerts", choices=["on", "off"], default=None,
        help="member SMS notification preference (CRITICAL may still page per rules.yaml)",
    )
    contact_parser.set_defaults(func=cmd_set_contact)

    args = parser.parse_args()
    sys.exit(args.func(args))


def cli_entry() -> None:
    """The installed `sentinel` command: load the operator's `.env`, then run.

    The only caller of `load_dotenv()` in the package, and deliberately a
    separate function from `main()` rather than a flag on it. A flag would make
    the safe behaviour the one a caller has to remember to ask for, and #68 is
    a record of what forgetting costs.

    `pyproject.toml`'s `[project.scripts]` must point here and not at `main`,
    or the deployed command silently stops reading `.env` - a service that
    starts with no SMTP configured and alerts nobody. `test_cli_entrypoint.py`
    pins that."""
    load_dotenv()
    main()


if __name__ == "__main__":
    cli_entry()
