from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class CredentialCipher:
    """Encrypts/decrypts customer API credentials at rest with a Fernet key
    supplied out-of-band via SENTINEL_MASTER_KEY (env/secret manager only -
    never in code or config files). Decrypted values must only live in
    worker memory for the duration of a single exchange call."""

    def __init__(self, master_key: str | None = None):
        key = master_key or os.environ.get("SENTINEL_MASTER_KEY")
        if not key:
            raise RuntimeError(
                "SENTINEL_MASTER_KEY is not set. Generate one with:\n"
                '  python3 -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("Failed to decrypt credential - wrong master key?") from exc


def redact_secrets(text: str, *secrets: str | None) -> str:
    """Strip known secret values out of a string before it is logged or
    included in an error message/alert."""
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted
