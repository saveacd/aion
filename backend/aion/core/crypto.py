"""Encryption-at-rest for exchange credentials and TOTP secrets.

Keys are derived from AION_SECRET_KEY via HKDF, so rotating the master secret
invalidates stored ciphertext (by design: an operator must re-enter keys).
"""

from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from aion.core.config import Settings
from aion.core.errors import AppError, ErrorCode

_SALT = b"aion-v2.1-secret-store"
_INFO = b"fernet-key-v1"


def _derive_key(secret: str) -> bytes:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=_SALT, info=_INFO)
    return base64.urlsafe_b64encode(hkdf.derive(secret.encode("utf-8")))


class SecretBox:
    """Symmetric encryption for values that must never be readable at rest."""

    def __init__(self, settings: Settings) -> None:
        if not settings.secret_key:
            raise RuntimeError("AION_SECRET_KEY is required for the secret store")
        self._fernet = Fernet(_derive_key(settings.secret_key))

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            raise ValueError("refusing to encrypt an empty secret")
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "Stored credential could not be decrypted. Re-enter it in Settings.",
            ) from exc

    def try_decrypt(self, ciphertext: str | None) -> str | None:
        if not ciphertext:
            return None
        try:
            return self.decrypt(ciphertext)
        except AppError:
            return None

    # ---- helpers for display ----
    @staticmethod
    def mask(value: str | None, keep: int = 4) -> str:
        if not value:
            return ""
        if len(value) <= keep:
            return "*" * len(value)
        return f"{value[:keep]}{'*' * min(12, len(value) - keep)}"

    @staticmethod
    def fingerprint(value: str) -> str:
        """Stable, non-reversible identifier used to detect key changes."""
        import hashlib

        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def generate_master_secret() -> str:
    return base64.urlsafe_b64encode(os.urandom(48)).decode("ascii")
