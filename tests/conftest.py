from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from sentinel.config import RulesConfig
from sentinel.crypto import CredentialCipher
from sentinel.db import Database

RULES_YAML = """
polling:
  fast_loop_seconds: "45"
  slow_loop_seconds: "600"
  jitter_seconds: "10"
detectors:
  quantity_drop:
    tolerance_pct: "0.5"
    tolerance_absolute: "0.00000001"
  tamper:
    consecutive_auth_failures_threshold: 2
  heartbeat:
    consecutive_failures_threshold: 5
alerting:
  cooldown_minutes:
    CRITICAL: "15"
    HIGH: "30"
    MEDIUM: "720"
    LOW: "1440"
  escalation_unacknowledged_minutes: "15"
"""


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def cipher():
    return CredentialCipher(Fernet.generate_key().decode())


@pytest.fixture
def rules(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(RULES_YAML)
    return RulesConfig(rules_path)


def make_account(db, label="acct-1"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts "
            "(label, topology, encrypted_api_key, encrypted_secret, encrypted_passphrase, onboarded_at, updated_at) "
            "VALUES (?, 'independent', 'x', 'y', 'z', '2026-01-01', '2026-01-01')",
            (label,),
        )
        return cur.lastrowid
