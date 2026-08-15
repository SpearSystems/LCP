"""Application-level encryption for persisted LCP envelopes."""

from __future__ import annotations

import base64
import json
import secrets
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class EnvelopeCryptoError(ValueError):
    """Raised when an encrypted envelope cannot be decoded."""


class EnvelopeCipher:
    """Encrypt JSON envelopes while preserving an explicit version marker."""

    _prefix = "enc:v1:"
    _aad = b"lcp-persisted-envelope:v1"

    def __init__(self, key: str | bytes | None):
        self._key = self._decode_key(key) if key else None

    @staticmethod
    def _decode_key(key: str | bytes) -> bytes:
        raw = key.encode("ascii") if isinstance(key, str) else key
        try:
            decoded = base64.b64decode(
                raw + b"=" * (-len(raw) % 4), altchars=b"-_", validate=True
            )
        except (ValueError, UnicodeEncodeError) as exc:
            raise EnvelopeCryptoError(
                "LCP_PII_ENCRYPTION_KEY must be URL-safe base64"
            ) from exc
        if len(decoded) != 32:
            raise EnvelopeCryptoError(
                "LCP_PII_ENCRYPTION_KEY must decode to exactly 32 bytes"
            )
        return decoded

    @property
    def enabled(self) -> bool:
        return self._key is not None

    def encode(self, value: dict[str, Any]) -> str:
        plaintext = json.dumps(
            value, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        if not self._key:
            return plaintext.decode("utf-8")
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._key).encrypt(nonce, plaintext, self._aad)
        return self._prefix + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decode(self, value: str) -> dict[str, Any]:
        if not value.startswith(self._prefix):
            if self._key:
                raise EnvelopeCryptoError(
                    "Unencrypted persisted envelope encountered while encryption is required"
                )
            return json.loads(value)
        if not self._key:
            raise EnvelopeCryptoError("Encrypted envelope requires LCP_PII_ENCRYPTION_KEY")
        try:
            packed = base64.urlsafe_b64decode(value[len(self._prefix) :])
            plaintext = AESGCM(self._key).decrypt(packed[:12], packed[12:], self._aad)
            decoded = json.loads(plaintext)
        except Exception as exc:
            raise EnvelopeCryptoError("Persisted envelope authentication failed") from exc
        if not isinstance(decoded, dict):
            raise EnvelopeCryptoError("Persisted envelope must be a JSON object")
        return decoded
