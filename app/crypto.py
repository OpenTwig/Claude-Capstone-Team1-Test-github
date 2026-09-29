"""Field encryption for SSNs and bank account numbers (design: Core modules, DD8; N1.3).

Values are encrypted with Fernet. FIELD_ENCRYPTION_KEYS holds one or more keys,
newest first: new values are always encrypted with the newest key, and any
listed key can decrypt, so keys can be rotated without re-encrypting at once.

Decrypted values must never be logged. SSNs are never displayed at all (D8);
account numbers are displayed only through mask_last4().
"""
from __future__ import annotations

from collections.abc import Sequence

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class DecryptionError(Exception):
    """A stored value could not be decrypted with any configured key."""


def validate_key(key: str) -> None:
    """Raise ValueError if `key` is not a valid Fernet key."""
    try:
        Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise ValueError("not a valid Fernet key (32 url-safe base64-encoded bytes)") from exc


class FieldCipher:
    """Encrypts and decrypts individual text fields."""

    def __init__(self, keys: Sequence[str]):
        if not keys:
            raise ValueError("at least one encryption key is required")
        self._fernet = MultiFernet([Fernet(k.encode()) for k in keys])

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeEncodeError) as exc:
            raise DecryptionError("value could not be decrypted with the configured keys") from exc

    def rotate(self, token: str) -> str:
        """Re-encrypt a stored value with the newest key."""
        try:
            return self._fernet.rotate(token.encode("ascii")).decode("ascii")
        except (InvalidToken, UnicodeEncodeError) as exc:
            raise DecryptionError("value could not be decrypted with the configured keys") from exc


def _cipher() -> FieldCipher:
    from flask import current_app

    return current_app.extensions["field_cipher"]


def encrypt_field(plaintext: str) -> str:
    """Encrypt with the app's configured keys (requires an app context)."""
    return _cipher().encrypt(plaintext)


def decrypt_field(token: str) -> str:
    """Decrypt with the app's configured keys (requires an app context)."""
    return _cipher().decrypt(token)


def mask_last4(value: str) -> str:
    """Show only the last 4 characters of an account number, e.g. '•••• 6789'."""
    value = (value or "").strip()
    if len(value) <= 4:
        return "••••"
    return f"•••• {value[-4:]}"
