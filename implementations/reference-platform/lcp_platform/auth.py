"""Authentication for the reference platform."""

from __future__ import annotations

import hashlib
import hmac
import json

from .config import PlatformConfig
from .secrets import FileSecretProvider
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
        self.secrets = FileSecretProvider(config.secrets_file)

    def principal(self, sender_id: str) -> dict[str, object] | None:
        row = self.store.get_credential(sender_id)
        principal = dict(row) if row else None
        external = self.secrets.get(sender_id)
        if external:
            principal = principal or {
                "sender_id": sender_id,
                "tenant_id": external.get("tenant_id", "default"),
                "scopes_json": "[]",
                "active": 1,
            }
            for field in ("hmac_secret", "previous_hmac_secret"):
                if field in external:
                    principal[field] = external[field]
            if external.get("api_key"):
                principal["api_key_hash"] = hashlib.sha256(
                    str(external["api_key"]).encode()
                ).hexdigest()
            if "scopes" in external:
                principal["scopes_json"] = json.dumps(external["scopes"])
            principal.setdefault("hmac_secret", None)
            principal.setdefault("previous_hmac_secret", None)
            principal.setdefault("api_key_hash", None)
        return principal

    def authenticate(
        self,
        *,
        sender_id: str | None,
        headers: dict[str, str],
        body: bytes,
        idempotency_key: str | None = None,
        mutating: bool = False,
        required_scope: str | None = None,
    ) -> str:
        """Authenticate and return the authenticated sender ID."""
        header_sender = header(headers, "X-LCP-Sender-Id")
        if sender_id and header_sender and sender_id != header_sender:
            raise AuthenticationError("Sender header does not match the envelope", "LCP-002")
        sender_id = sender_id or header_sender
        authorization = header(headers, "Authorization") or ""
        if not sender_id and authorization.lower().startswith("bearer "):
            sender_id = self.store.find_sender_for_api_key(authorization[7:].strip())
        if not sender_id:
            raise AuthenticationError("Sender identity is required")

        if not self.config.require_auth:
            return sender_id

        credential = self.principal(sender_id)
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

        if required_scope and not self.has_scope(sender_id, required_scope):
            raise AuthenticationError("Sender is not authorized for this operation", "LCP-002")
        return sender_id

    def has_scope(self, sender_id: str, required_scope: str) -> bool:
        scopes = self.scopes_for(sender_id)
        return "*" in scopes or required_scope in scopes or "platform:admin" in scopes

    def secret_for(self, sender_id: str) -> str | None:
        credential = self.principal(sender_id)
        return str(credential["hmac_secret"]) if credential and credential.get("hmac_secret") else None

    def tenant_for(self, sender_id: str) -> str | None:
        principal = self.principal(sender_id)
        return str(principal["tenant_id"]) if principal and principal.get("tenant_id") else None

    def scopes_for(self, sender_id: str) -> set[str]:
        principal = self.principal(sender_id)
        if not principal:
            return set()
        try:
            decoded = json.loads(str(principal.get("scopes_json", "[]")))
        except (TypeError, ValueError):
            return set()
        if not isinstance(decoded, list) or not all(isinstance(scope, str) for scope in decoded):
            return set()
        return set(decoded)
