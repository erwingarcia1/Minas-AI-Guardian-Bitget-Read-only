from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sentinel.crypto import CredentialCipher
from sentinel.db import Database
from sentinel.exchange import BitgetClient, TamperSignal

# Bitget's Get-Account-Info response includes an `authorities` field, confirmed
# (Phase 0 audit, docs/phase0-capability-audit.md) to carry per-module
# read/write permission codes such as 'coor' (read) / 'coow' (write) - an 'r'
# suffix for read, a 'w' suffix for write. The exact full enum of module
# prefixes was NOT verified against a live key. Because of that, this check is
# deliberately conservative and fails CLOSED: any authority token that isn't
# unambiguously read-only, or a response we can't parse at all, is rejected
# rather than assumed safe. Re-validate this logic against a real key in
# Phase 1 (see docs/phase0-capability-audit.md section 7, item 8-adjacent).
_WRITE_KEYWORDS = ("trade", "withdraw", "transfer", "write")


class OnboardingRejected(Exception):
    """Raised when a candidate API key cannot be verified as read-only."""


@dataclass
class OnboardingResult:
    account_id: int
    label: str


def _looks_read_only(authorities: object) -> tuple[bool, str]:
    if authorities is None:
        return False, "account-info response had no 'authorities' field - cannot verify scope"

    if isinstance(authorities, str):
        tokens = [t.strip() for t in authorities.replace(";", ",").split(",") if t.strip()]
    elif isinstance(authorities, (list, tuple)):
        tokens = [str(t).strip() for t in authorities if str(t).strip()]
    else:
        return False, f"unrecognized 'authorities' field type: {type(authorities).__name__}"

    if not tokens:
        return False, "'authorities' field was empty - cannot verify scope"

    for token in tokens:
        lowered = token.lower()
        if any(keyword in lowered for keyword in _WRITE_KEYWORDS):
            return False, f"authority token '{token}' indicates non-read-only scope"
        if lowered.endswith("r"):
            continue
        if lowered.endswith("w"):
            return False, f"authority token '{token}' has Bitget's documented write-scope suffix"
        return False, f"authority token '{token}' doesn't match a recognized read-only pattern - failing closed"

    return True, "all authority tokens matched a read-only ('...r' suffix) pattern"


def verify_read_only(client: BitgetClient) -> tuple[bool, str]:
    """Programmatic, fail-closed check that a candidate key is read-only.
    Two independent checks must both pass:
      1. fetch_balance() succeeds (proves the key has working read access).
      2. account-info 'authorities' contains no write/trade/withdraw indicator.
    Any unexpected error during either check is treated as a rejection, not
    a pass - this is a security gate, not a best-effort probe.
    """
    try:
        client.fetch_balance()
    except TamperSignal as exc:
        return False, f"balance read failed - key may be invalid or lacks account-read scope: {exc}"
    except Exception as exc:  # fail closed: any surprise here is a rejection
        return False, f"balance read raised an unexpected error: {exc}"

    try:
        info = client.fetch_account_info()
    except TamperSignal as exc:
        return False, f"account-info call failed: {exc}"
    except Exception as exc:  # fail closed
        return False, f"account-info call raised an unexpected error: {exc}"

    payload = info.get("data", info) if isinstance(info, dict) else info
    authorities = payload.get("authorities") if isinstance(payload, dict) else None
    return _looks_read_only(authorities)


def onboard_account(
    db: Database,
    cipher: CredentialCipher,
    *,
    label: str,
    topology: str,
    api_key: str,
    secret: str,
    passphrase: str,
) -> OnboardingResult:
    if topology not in ("independent", "sub_account"):
        raise ValueError(f"unknown topology '{topology}'")

    client = BitgetClient(api_key, secret, passphrase)
    ok, reason = verify_read_only(client)
    if not ok:
        raise OnboardingRejected(
            f"rejected key for '{label}': {reason}. Sentinel only accepts read-only keys."
        )

    now = datetime.now(timezone.utc).isoformat()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO accounts
                (label, topology, encrypted_api_key, encrypted_secret, encrypted_passphrase,
                 status, onboarded_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                label,
                topology,
                cipher.encrypt(api_key),
                cipher.encrypt(secret),
                cipher.encrypt(passphrase),
                now,
                now,
            ),
        )
        account_id = cur.lastrowid
        cur.execute("INSERT INTO poll_state (account_id) VALUES (?)", (account_id,))

    from sentinel.baseline import seed_baseline  # local import avoids a module cycle

    seed_baseline(db, account_id, client)

    return OnboardingResult(account_id=account_id, label=label)
