#!/usr/bin/env python3
"""A throwaway member portal and app API, seeded with demo data, for trying
the mobile app on a phone (decision 21). Never production, and never real data.

    .venv/bin/python scripts/dev-server.py
    # then, in another terminal, using the address it prints:
    cd mobile && EXPO_PUBLIC_API_URL=http://<LAN-IP>:8391 npx expo start --clear

What it does:
- a FRESH SQLite database in a temporary directory, deleted on exit. It never
  reads SENTINEL_DB_PATH, never opens an existing database, never reads .env;
- a demo member with a password and two-factor, two demo accounts (one
  watched, one whose key is being refused), alerts of every severity with
  evidence and balances, and an activity record;
- serves the member portal and /api/v1 on 0.0.0.0, so a phone on the same
  network can reach it, and prints the live two-factor code every 30 seconds
  so no authenticator app is needed (the secret is fixed, so one can be added
  once if preferred);
- keeps the watched account "watched" (the monitoring engine does not run
  here, so nothing else would refresh its last successful check).

What it can never do:
- reach an exchange, or send an email, SMS or Telegram message. It sets
  SENTINEL_ENV=test before anything is imported, and sentinel/net.py then
  refuses every outbound connection - the same guard the test suite runs
  under. The new-device notice the API sends on every app sign-in is printed
  here instead.
"""
from __future__ import annotations

import os

# Before ANY sentinel import: sentinel/net.py reads this per call, and it is
# what makes an accidental send impossible rather than merely unlikely.
os.environ["SENTINEL_ENV"] = "test"

import argparse  # noqa: E402
import json  # noqa: E402
import signal  # noqa: E402
import socket  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cryptography.fernet import Fernet  # noqa: E402

from sentinel import api as api_module  # noqa: E402
from sentinel import twofactor  # noqa: E402
from sentinel.config import RulesConfig  # noqa: E402
from sentinel.crypto import CredentialCipher  # noqa: E402
from sentinel.db import Database  # noqa: E402
from sentinel.events import append_event, record_member_security_event  # noqa: E402
from sentinel.expected import mark_expected  # noqa: E402
from sentinel.portal import serve_portal, set_member_password  # noqa: E402
from sentinel.totp import STEP_SECONDS, totp_at  # noqa: E402

DEMO_EMAIL = "demo@minas.example"
DEMO_PASSWORD = "minas-demo-password"
# Fixed, so an authenticator app entry survives restarts. Demo only: it opens
# a throwaway database on a developer's machine and nothing else.
DEMO_TOTP_SECRET = "MINASGUARDIANDEMOSECRETFORTESTSX"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def lan_address() -> str:
    """The address a phone on the same network would use. A UDP connect
    sends nothing; it only asks the kernel which interface would be used."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def seed(db: Database, cipher: CredentialCipher) -> dict:
    """The demo world. Returns the ids the simulation thread needs."""
    now = _now()
    set_member_password(db, DEMO_EMAIL, DEMO_PASSWORD, notify=None)

    # Two-factor, enrolled and confirmed with the fixed demo secret. The code
    # used to confirm is the PREVIOUS step's, so the current one is still
    # unused for the first sign-in (a code is good once).
    original = twofactor.generate_secret
    twofactor.generate_secret = lambda: DEMO_TOTP_SECRET
    try:
        secret, _ = twofactor.begin_enrolment(db, cipher, twofactor.MEMBER, DEMO_EMAIL)
    finally:
        twofactor.generate_secret = original
    assert secret == DEMO_TOTP_SECRET
    if twofactor.confirm_enrolment(db, cipher, twofactor.MEMBER, DEMO_EMAIL,
                                   totp_at(secret, twofactor._clock() - STEP_SECONDS)) is None:
        raise SystemExit("could not confirm the demo two-factor enrolment")

    with db.cursor() as cur:
        def account(label, exchange, member_name, status="active"):
            cur.execute(
                "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, encrypted_secret, "
                "encrypted_passphrase, alert_email, alert_phone, status, member_name, onboarded_at, updated_at) "
                "VALUES (?, ?, 'independent', 'demo', 'demo', 'demo', ?, '+15555550100', ?, ?, ?, ?)",
                (label, exchange, DEMO_EMAIL, status, member_name, _iso(now - timedelta(days=40)), _iso(now)))
            return cur.lastrowid

        savings = account("demo-binance-1", "binance", "Binance - savings")
        trading = account("demo-bitget-1", "bitget", "Bitget - trading")
        cur.execute("INSERT INTO poll_state (account_id, last_success_at) VALUES (?, ?)", (savings, _iso(now)))
        # The trading account's key is being refused: the member sees why it
        # is not being watched, and what to do.
        cur.execute(
            "INSERT INTO poll_state (account_id, consecutive_auth_failures, last_success_at, auth_failing_since, "
            "last_auth_error, last_auth_error_at) VALUES (?, 4, ?, ?, 'apikey invalid', ?)",
            (trading, _iso(now - timedelta(hours=3)), _iso(now - timedelta(hours=3)), _iso(now)))

        def alert(account_id, rule, severity, message, evidence, minutes_ago, delivered=2,
                  acknowledged_by=None):
            sent = now - timedelta(minutes=minutes_ago)
            cur.execute(
                "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, confidence, "
                "sent_at, delivered_to, acknowledged_at, acknowledged_by) VALUES (?, ?, ?, ?, ?, ?, 'high', ?, ?, ?, ?)",
                (account_id, rule, severity, f"demo:{rule}:{minutes_ago}", message, json.dumps(evidence),
                 _iso(sent), delivered,
                 _iso(sent + timedelta(minutes=4)) if acknowledged_by else None, acknowledged_by))
            return cur.lastrowid, sent

        theft, theft_at = alert(
            savings, "withdrawal_to_unseen_address", "CRITICAL",
            "0.85 BTC withdrawal to an address never seen on this account",
            {"coin": "BTC", "amount": "0.85", "address": "bc1qdemo0address0never0seen0before0xyz",
             "order_id": "demo-wd-1"}, minutes_ago=12)
        for coin, before, after in (("BTC", "1.20000000", "0.35000000"), ("USDT", "5400.00", "5400.00")):
            cur.execute("INSERT INTO balance_snapshots (account_id, coin, quantity, snapshot_at) VALUES (?, ?, ?, ?)",
                        (savings, coin, before, _iso(theft_at - timedelta(minutes=1))))
            cur.execute("INSERT INTO balance_snapshots (account_id, coin, quantity, snapshot_at) VALUES (?, ?, ?, ?)",
                        (savings, coin, after, _iso(theft_at + timedelta(minutes=1))))
        alert(savings, "withdrawal_burst", "HIGH", "4 withdrawals in 10 minutes",
              {"count": 4, "window_minutes": 10}, minutes_ago=180, acknowledged_by=DEMO_EMAIL)
        alert(trading, "account_unreachable", "CRITICAL",
              "Bitget - trading is not being watched: the exchange is refusing our read-only key",
              {"consecutive_auth_failures": 4}, minutes_ago=170)
        alert(savings, "abnormal_fee_spend", "MEDIUM", "Trading fees 4x your usual level today",
              {"multiplier": "4.1"}, minutes_ago=60 * 26)
        alert(savings, "withdrawal_initiated", "CRITICAL", "0.10 ETH withdrawal initiated",
              {"coin": "ETH", "amount": "0.10", "order_id": "demo-wd-0"}, minutes_ago=60 * 50, delivered=0)
        quiet, _ = alert(savings, "baseline_drift_new_deposit_source", "LOW",
                         "A deposit arrived from a source not seen before", {"coin": "USDT"}, minutes_ago=60 * 72)

    mark_expected(db, quiet, savings, "baseline_drift_new_deposit_source",
                  "demo:baseline_drift_new_deposit_source:4320", DEMO_EMAIL, note="That was my other exchange.")
    append_event(db, savings, "customer_added", "LOW", {"exchange": "binance", "topology": "independent"},
                 actor="minas-operator")
    append_event(db, trading, "customer_added", "LOW", {"exchange": "bitget", "topology": "independent"},
                 actor="minas-operator")
    append_event(db, savings, "alert_contact_updated", "LOW",
                 {"email_from": None, "email_to": DEMO_EMAIL, "phone_from": None, "phone_to": "+15555550100"},
                 actor="minas-operator")
    record_member_security_event(db, DEMO_EMAIL, "member_two_factor_enabled")
    return {"savings": savings, "theft": theft}


def keep_watched(db: Database, account_id: int, stop: threading.Event) -> None:
    """The engine does not run here; without this the 'watched' account
    would read 'no successful check recently' after a few minutes."""
    while not stop.wait(20):
        with db.cursor() as cur:
            cur.execute("UPDATE poll_state SET last_success_at = ? WHERE account_id = ?", (_iso(_now()), account_id))


def new_alerts(db: Database, account_id: int, every: int, stop: threading.Event) -> None:
    n = 0
    while not stop.wait(every):
        n += 1
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO alerts (account_id, rule, severity, dedup_key, message, evidence_json, confidence, "
                "sent_at, delivered_to) VALUES (?, 'withdrawal_initiated', 'CRITICAL', ?, ?, ?, 'high', ?, 1)",
                (account_id, f"demo:sim:{n}", f"Simulated withdrawal #{n}: 0.0{n % 10} BTC",
                 json.dumps({"coin": "BTC", "amount": f"0.0{n % 10}", "order_id": f"demo-sim-{n}"}), _iso(_now())))
        print(f"[demo] new CRITICAL alert #{n} - pull to refresh on the Alerts tab", flush=True)


def print_codes(stop: threading.Event) -> None:
    while True:
        now = time.time()
        left = STEP_SECONDS - int(now) % STEP_SECONDS
        print(f"[demo] two-factor code: {totp_at(DEMO_TOTP_SECRET, now)}  (valid ~{left}s more)", flush=True)
        if stop.wait(left + 0.5):
            return


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default: every interface, for a phone)")
    parser.add_argument("--port", type=int, default=8391)
    parser.add_argument("--new-alert-every", type=int, default=0, metavar="SECONDS",
                        help="add a simulated CRITICAL alert this often (0 = never)")
    parser.add_argument("--no-codes", action="store_true", help="do not print the live two-factor code")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="minas-dev-") as tmp:
        db = Database(Path(tmp) / "demo.db")
        cipher = CredentialCipher(Fernet.generate_key().decode())
        ids = seed(db, cipher)

        def notice(_db, address, name):
            print(f"[demo] the app signed in to {address} as \"{name}\" (a real server would email this)",
                  flush=True)

        api_module.send_new_device_email = notice
        server = serve_portal(db, args.port, host=args.host, rules=RulesConfig(str(ROOT / "config" / "rules.yaml")),
                              cipher=cipher)
        stop = threading.Event()
        # Ctrl+C and a plain `kill` both end in the cleanup below, which is
        # what deletes the demo database. Installed explicitly: a process
        # started in the background by a non-interactive shell inherits
        # SIGINT as ignored, and Python's own handler is then never set.
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        threading.Thread(target=keep_watched, args=(db, ids["savings"], stop), daemon=True).start()
        if args.new_alert_every > 0:
            threading.Thread(target=new_alerts, args=(db, ids["savings"], args.new_alert_every, stop),
                             daemon=True).start()

        address = lan_address() if args.host in ("0.0.0.0", "") else args.host
        url = f"http://{address}:{args.port}"
        print(f"""
Minas AI Guardian - DEMO server (throwaway data, nothing leaves this machine)

  API for the app:   {url}
  Start the app:     cd mobile && EXPO_PUBLIC_API_URL={url} npx expo start --clear
  Sign in with:      {DEMO_EMAIL}  /  {DEMO_PASSWORD}  /  the code printed below
  Authenticator:     secret {DEMO_TOTP_SECRET} (optional - the live code is printed here)
  Web portal:        http://localhost:{args.port} on THIS machine only (its cookie is Secure,
                     which browsers accept over plain http on localhost but not on {address})

Ctrl+C stops it and deletes the demo database.
""", flush=True)
        try:
            if args.no_codes:
                stop.wait()
            else:
                print_codes(stop)
        finally:
            stop.set()
            server.shutdown()
            server.server_close()
            db.close()
    print("[demo] stopped; the demo database is gone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
