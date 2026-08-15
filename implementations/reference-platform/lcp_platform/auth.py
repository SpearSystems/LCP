"""Authentication for the reference platform."""

from __future__ import annotations

import hashlib
import hmac

from .config import PlatformConfig
from .storage import Store


def header(headers: dict[str, str], name: str) -> str | None:
    """Read an HTTP header without relying on dictionary casing."""
    name = name.lower()
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


class AuthenticationError(ValueError):
    """Raised when an LCP request cannot be authenticated."""

    def __init__(self, message: str, code: str = "LCP-002"):
        self.code = code
        super().__init__(message)


def canonical_bytes(timestamp: str, idempotency_key: str | None, body: bytes) -> bytes:
    return f"{timestamp}\n{idempotency_key or ''}\n".encode("utf-8") + body


def signature_for(secret: str, timestamp: str, idempotency_key: str | None, body: bytes) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        canonical_bytes(timestamp, idempotency_key, body),
        hashlib.sha256,
    ).hexdigest()


def _timestamp_is_fresh(timestamp: str, max_skew_seconds: int) -> bool:
    from datetime import datetime, timezone
    try:
        signed_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if signed_at.tzinfo is None:
        return False
    now = datetime.now(timezone.utc)
    return abs((now - signed_at.astimezone(timezone.utc)).total_seconds()) <= max_skew_seconds


class Authenticator:
    def __init__(self, store: Store, config: PlatformConfig):
        self.store = store
        self.config = config

    def authenticate(
        self,
        *,
        sender_id: str | None,
        headers: dict[str, str],
        body: bytes,
        idempotency_key: str | None = None,
        mutating: bool = False,
    ) -> str:
        """Authenticate and return the authenticated sender ID."""
        sender_id = sender_id or header(headers, "X-LCP-Sender-Id")
        authorization = header(headers, "Authorization") or ""
        if not sender_id and authorization.lower().startswith("bearer "):
            sender_id = self.store.find_sender_for_api_key(authorization[7:].strip())
        if not sender_id:
            raise AuthenticationError("Sender identity is required")

        if not self.config.require_auth:
            return sender_id

        credential = self.store.get_credential(sender_id)
        if not credential:
            raise AuthenticationError("Unknown sender", "LCP-002")

        if authorization.lower().startswith("bearer "):
            presented = authorization[7:].strip()
            digest = hashlib.sha256(presented.encode()).hexdigest()
            if not credential["api_key_hash"] or not hmac.compare_digest(
                digest, credential["api_key_hash"]
            ):
                raise AuthenticationError("Invalid bearer credential", "LCP-012")
        else:
            timestamp = header(headers, "X-LCP-Timestamp")
            signature = header(headers, "X-LCP-Signature")
            header_key = header(headers, "X-LCP-Idempotency-Key")
            if not timestamp or not signature:
                raise AuthenticationError("HMAC headers are required", "LCP-012")
            if not _timestamp_is_fresh(timestamp, self.config.replay_window_seconds):
                raise AuthenticationError("Timestamp is outside the replay window", "LCP-012")
            if mutating and not header_key:
                raise AuthenticationError("Idempotency header is required", "LCP-012")
            if header_key and idempotency_key and header_key != idempotency_key:
                raise AuthenticationError("Header/body idempotency keys differ", "LCP-012")
            candidate_secrets = [
                credential["hmac_secret"],
                credential["previous_hmac_secret"],
            ]
            if not any(
                secret
                and hmac.compare_digest(
                    signature.lower(),
                    signature_for(secret, timestamp, header_key, body),
                )
                for secret in candidate_secrets
            ):
                raise AuthenticationError("Invalid HMAC signature", "LCP-012")

        return sender_id

    def secret_for(self, sender_id: str) -> str | None:
        credential = self.store.get_credential(sender_id)
        return credential["hmac_secret"] if credential else None
