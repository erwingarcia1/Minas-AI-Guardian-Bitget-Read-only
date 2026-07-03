from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from sentinel.alerts.email import EmailChannel
from sentinel.alerts.escalation import Escalator
from sentinel.alerts.sms import TwilioSmsChannel
from sentinel.alerts.telegram import TelegramChannel
from sentinel.config import RulesConfig
from sentinel.crypto import CredentialCipher
from sentinel.db import Database
from sentinel.onboarding import OnboardingRejected, onboard_account

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

    sid, tw_token = _env("TWILIO_ACCOUNT_SID"), _env("TWILIO_AUTH_TOKEN")
    tw_from, tw_to = _env("TWILIO_FROM_NUMBER"), _env("TWILIO_TO_NUMBERS")
    if sid and tw_token and tw_from and tw_to:
        named["sms"] = TwilioSmsChannel(sid, tw_token, tw_from, [n.strip() for n in tw_to.split(",")])

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


def build_channels(rules: RulesConfig) -> dict[str, list]:
    """severity -> [channel objects], from alerting.routing in rules.yaml.
    'digest' entries are intentionally skipped here: MEDIUM/LOW alerts are
    stored (dedup still applies) and delivered by the `sentinel digest`
    command instead of immediately."""
    named = _build_named_channels()
    channels: dict[str, list] = {}
    for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        routed = rules.get("alerting", "routing", severity, default=[]) or []
        resolved = []
        for name in routed:
            if name == "digest":
                continue
            channel = named.get(str(name))
            if channel is None:
                logger.warning("channel '%s' routed for %s but not configured (missing env vars)", name, severity)
                continue
            resolved.append(channel)
        channels[severity] = resolved
    return channels


def cmd_onboard(args: argparse.Namespace) -> int:
    db = Database(_env("SENTINEL_DB_PATH", "./sentinel_data/sentinel.db"))
    cipher = CredentialCipher()

    api_key = args.api_key or input("Bitget API key: ").strip()
    secret = args.secret or getpass.getpass("Bitget API secret: ").strip()
    passphrase = args.passphrase or getpass.getpass("Bitget API passphrase: ").strip()

    try:
        result = onboard_account(
            db, cipher, label=args.label, topology=args.topology,
            api_key=api_key, secret=secret, passphrase=passphrase,
        )
    except OnboardingRejected as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 1

    print(f"Onboarded '{result.label}' as account id {result.account_id}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    _setup_logging()
    from sentinel.dashboard import serve_dashboard
    from sentinel.engine import Engine
    from sentinel.metrics import Metrics

    db = Database(_env("SENTINEL_DB_PATH", "./sentinel_data/sentinel.db"))
    cipher = CredentialCipher()
    rules = RulesConfig(_env("SENTINEL_RULES_PATH", "./config/rules.yaml"))
    channels = build_channels(rules)

    metrics = Metrics()
    metrics_port = int(_env("SENTINEL_METRICS_PORT", "9109"))
    Metrics.serve(metrics_port)
    dashboard_port = int(_env("SENTINEL_DASHBOARD_PORT", "8390"))
    serve_dashboard(db, dashboard_port)
    logger.info("dashboard on :%d, metrics on :%d", dashboard_port, metrics_port)

    escalation_minutes = rules.get_int("alerting", "escalation_unacknowledged_minutes", default=15)
    escalator = Escalator(channels.get("CRITICAL", []), escalation_minutes)

    engine = Engine(db, cipher, rules, channels, metrics=metrics, escalator=escalator)
    engine.run()
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    from sentinel.alerts.digest import send_digest

    db = Database(_env("SENTINEL_DB_PATH", "./sentinel_data/sentinel.db"))
    rules = RulesConfig(_env("SENTINEL_RULES_PATH", "./config/rules.yaml"))
    named = _build_named_channels()
    # Digest goes to telegram + email when configured.
    targets = [c for name, c in named.items() if name in ("telegram", "email")]
    if not targets:
        print("no digest channels configured (need Telegram or SMTP env vars)", file=sys.stderr)
        return 1
    sent = send_digest(db, targets, hours=args.hours)
    print("digest sent" if sent else "nothing to report - no digest sent")
    return 0


def cmd_ack(args: argparse.Namespace) -> int:
    db = Database(_env("SENTINEL_DB_PATH", "./sentinel_data/sentinel.db"))
    with db.cursor() as cur:
        cur.execute(
            "UPDATE alerts SET acknowledged_at = ? WHERE id = ? AND acknowledged_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), args.alert_id),
        )
        updated = cur.rowcount
    if updated:
        print(f"alert {args.alert_id} acknowledged")
        return 0
    print(f"alert {args.alert_id} not found or already acknowledged", file=sys.stderr)
    return 1


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
    load_dotenv()
    parser = argparse.ArgumentParser(prog="sentinel")
    sub = parser.add_subparsers(dest="command", required=True)

    onboard_parser = sub.add_parser("onboard", help="Verify and register a read-only Bitget key")
    onboard_parser.add_argument("--label", required=True)
    onboard_parser.add_argument("--topology", choices=["independent", "sub_account"], default="independent")
    onboard_parser.add_argument("--api-key")
    onboard_parser.add_argument("--secret")
    onboard_parser.add_argument("--passphrase")
    onboard_parser.set_defaults(func=cmd_onboard)

    run_parser = sub.add_parser("run", help="Run the monitoring engine (workers, rate limits, breakers)")
    run_parser.set_defaults(func=cmd_run)

    digest_parser = sub.add_parser("digest", help="Send the MEDIUM/LOW daily digest now")
    digest_parser.add_argument("--hours", type=int, default=24)
    digest_parser.set_defaults(func=cmd_digest)

    ack_parser = sub.add_parser("ack", help="Acknowledge an alert by id (stops CRITICAL escalation)")
    ack_parser.add_argument("alert_id", type=int)
    ack_parser.set_defaults(func=cmd_ack)

    triage_parser = sub.add_parser("triage", help="Print an AI incident narrative for an account's recent events")
    triage_parser.add_argument("--label", required=True)
    triage_parser.set_defaults(func=cmd_triage)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
