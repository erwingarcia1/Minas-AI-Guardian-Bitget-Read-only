"""100-account synthetic soak test.

Creates 100 synthetic accounts in a throwaway SQLite DB, drives the real
Engine (worker pool + rate limiters + circuit breakers) against mocked
exchange clients built from recorded-fixture-style responses, and reports:

  - full-fleet cycle time vs the fast-loop target
  - rate-limit compliance (observed call rate vs configured buckets)
  - fleet isolation (5 accounts fail hard every cycle; the rest must not stall)
  - detection correctness under load (3 accounts get a scripted "attack";
    exactly those must fire CRITICALs)

Never touches a live API. Run: .venv/bin/python scripts/soak_test.py
"""
from __future__ import annotations

import random
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from cryptography.fernet import Fernet

from sentinel.config import RulesConfig
from sentinel.crypto import CredentialCipher
from sentinel.db import Database
from sentinel.engine import Engine

N_ACCOUNTS = 100
N_BROKEN = 5      # accounts whose client always fails (bad key / banned IP)
N_ATTACKED = 3    # accounts with a scripted withdrawal-to-unseen-address


class CountingChannel:
    def __init__(self):
        self.alerts = []

    def send(self, alert):
        self.alerts.append(alert)


def make_client(label: str, attacked: bool) -> MagicMock:
    client = MagicMock()
    if label.startswith("broken-"):
        client.fetch_deposits.side_effect = RuntimeError("simulated dead key")
        return client

    withdrawals = []
    if attacked:
        withdrawals = [{
            "id": f"wd-attack-{label}",
            "amount": "0.9",
            "currency": "BTC",
            "status": "pending",
            "address": f"bc1q-attacker-{label}",
            "timestamp": 1751500000000,
            "fee": {"currency": "BTC", "cost": "0.0001"},
            "info": {},
        }]

    def slow(result):
        def _call(*a, **k):
            time.sleep(random.uniform(0.002, 0.01))  # simulated network latency
            return result
        return _call

    client.fetch_deposits.side_effect = slow([])
    client.fetch_withdrawals.side_effect = slow(withdrawals)
    client.fetch_closed_orders.side_effect = slow([])
    client.fetch_transfers.side_effect = slow([])
    client.fetch_tickers.side_effect = slow({})
    client.fetch_balance.side_effect = slow({"total": {"BTC": 1.0, "USDT": 5000}})
    client.fetch_account_info.side_effect = slow(
        {"data": {"authorities": "spot_account_r", "ips": "", "userId": label}}
    )
    return client


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="sentinel-soak-")
    db = Database(Path(tmp) / "soak.db")
    cipher = CredentialCipher(Fernet.generate_key().decode())
    rules = RulesConfig("config/rules.yaml")

    attacked_labels = {f"acct-{i:03d}" for i in range(N_ATTACKED)}
    labels = [f"acct-{i:03d}" for i in range(N_ACCOUNTS - N_BROKEN)] + [
        f"broken-{i}" for i in range(N_BROKEN)
    ]

    now = "2026-07-02T00:00:00+00:00"
    with db.cursor() as cur:
        for label in labels:
            cur.execute(
                "INSERT INTO accounts (label, topology, encrypted_api_key, encrypted_secret, "
                "encrypted_passphrase, status, onboarded_at, updated_at) "
                "VALUES (?, 'independent', ?, ?, ?, 'active', ?, ?)",
                (label, cipher.encrypt("k"), cipher.encrypt("s"), cipher.encrypt("p"), now, now),
            )
            cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (cur.lastrowid,))

    clients: dict[str, MagicMock] = {}

    def fake_loader(cipher_arg, row):
        label = row["label"]
        if label not in clients:
            clients[label] = make_client(label, attacked=label in attacked_labels)
        return clients[label]

    import sentinel.poller as poller_mod

    poller_mod._load_client = fake_loader  # never hits a live API

    channel = CountingChannel()
    channels = {"CRITICAL": [channel], "HIGH": [channel], "MEDIUM": [channel], "LOW": [channel]}
    engine = Engine(db, cipher, rules, channels)

    call_count_start = time.monotonic()
    cycles = 3
    cycle_times = []
    for i in range(cycles):
        t0 = time.monotonic()
        engine.run_one_cycle()
        cycle_times.append(time.monotonic() - t0)
        print(f"cycle {i + 1}/{cycles}: {cycle_times[-1]:.1f}s")

    elapsed = time.monotonic() - call_count_start
    total_calls = sum(
        sum(getattr(c, m).call_count for m in (
            "fetch_deposits", "fetch_withdrawals", "fetch_closed_orders",
            "fetch_balance", "fetch_transfers", "fetch_tickers", "fetch_account_info",
        ))
        for c in clients.values()
    )
    observed_rps = total_calls / elapsed

    with db.cursor() as cur:
        cur.execute("SELECT count(*) c FROM poll_state WHERE last_success_at IS NOT NULL")
        healthy = cur.fetchone()["c"]

    critical_labels = {
        a.account_label for a in channel.alerts if a.severity == "CRITICAL"
    }

    fast_target = rules.get_seconds("polling", "fast_loop_seconds")
    ip_budget = float(rules.get_decimal("rate_limits", "egress_ip_rps"))

    print("\n=== soak results ===")
    print(f"accounts: {N_ACCOUNTS} ({N_BROKEN} broken, {N_ATTACKED} attacked)")
    print(f"mean cycle time: {sum(cycle_times) / len(cycle_times):.1f}s (fast-loop target {fast_target:.0f}s)")
    print(f"healthy accounts polled OK: {healthy}/{N_ACCOUNTS - N_BROKEN}")
    print(f"observed egress rate: {observed_rps:.1f} req/s (configured IP bucket {ip_budget:.0f} req/s)")
    print(f"CRITICAL alerts fired for: {sorted(critical_labels)}")

    failures = []
    if healthy < N_ACCOUNTS - N_BROKEN:
        failures.append("broken accounts stalled healthy ones")
    if max(cycle_times) > fast_target:
        failures.append(f"cycle time {max(cycle_times):.1f}s exceeds fast-loop target {fast_target:.0f}s")
    if observed_rps > ip_budget * 1.05:
        failures.append(f"rate limiter breached IP budget ({observed_rps:.1f} > {ip_budget})")
    if critical_labels != attacked_labels:
        failures.append(
            f"detection mismatch under load: expected {sorted(attacked_labels)}, got {sorted(critical_labels)}"
        )

    if failures:
        print("\nSOAK FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nSOAK PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
