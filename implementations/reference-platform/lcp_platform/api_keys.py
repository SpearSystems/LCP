"""API-key hashing for the reference platform.

API keys are stored as salted PBKDF2-HMAC-SHA256 digests so that a leaked
credentials table cannot be brute-forced offline. Every credential receives
its own random salt (per-credential salting defeats rainbow tables and batch
cracking), and verification uses constant-time comparison.

The API key itself is never stored; only the salt and digest are persisted.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# OWASP Password Storage Cheat Sheet (2023) minimum for PBKDF2-HMAC-SHA256.
# The reference platform holds a small credentials table, so the lookup cost
# of iterating rows during bearer-only authentication stays negligible.
API_KEY_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16


def hash_api_key(api_key: str) -> tuple[str, str]:
    """Return ``(salt, digest)`` for a new API key.

    The salt is random per credential and must be stored alongside the
    digest so the key can be verified later.
    """
    salt = secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        api_key.encode("utf-8"),
        salt.encode("ascii"),
        API_KEY_PBKDF2_ITERATIONS,
    )
    return salt, digest.hex()


def verify_api_key(api_key: str, salt: str | None, stored_digest: str | None) -> bool:
    """Constant-time verification of an API key against stored salt/digest."""
    if not salt or not stored_digest:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        api_key.encode("utf-8"),
        salt.encode("ascii"),
        API_KEY_PBKDF2_ITERATIONS,
    )
    return hmac.compare_digest(digest.hex(), stored_digest)
