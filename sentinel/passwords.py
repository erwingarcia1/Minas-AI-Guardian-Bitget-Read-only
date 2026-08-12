"""Login primitives shared by the two identity systems.

Only the *primitives* are shared. Customers (`portal_members`) and operators
(`operators`) remain separate tables reached through separate logins - see
G-50, which is explicit that conflating them is how an operator ends up
reachable through a customer login. Sharing a scrypt call is not conflating
identity; duplicating the crypto so the two could drift would be worse.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

# Interactive-login parameters: ~100ms per verification on the deployment host.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1

# Verified against when the account does not exist, so a missing username costs
# exactly one scrypt op - the same as a real one - and response time never
# reveals whether an account exists.
DUMMY_HASH = None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False  # unparseable hash fails closed


DUMMY_HASH = hash_password(secrets.token_urlsafe(32))

# Five failures in fifteen minutes, per client IP and per account name. Chosen
# to stop credential stuffing without locking a fat-fingered operator out for
# the rest of the day.
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 15 * 60


class LoginThrottle:
    """Sliding-window failure counter per key (email or client IP)."""

    def __init__(self, max_failures: int = MAX_FAILURES, window: float = FAILURE_WINDOW_SECONDS):
        self._max = max_failures
        self._window = window
        self._failures: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def blocked(self, key: str, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        with self._lock:
            q = self._failures[key]
            while q and q[0] <= now - self._window:
                q.popleft()
            return len(q) >= self._max

    def record_failure(self, key: str, now: float | None = None) -> None:
        with self._lock:
            self._failures[key].append(now if now is not None else time.time())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
