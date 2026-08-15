"""LCP HTTP authentication helpers.

The canonical signing input is:

    <timestamp>\n<idempotency-key>\n<raw-request-body>

The body must be the exact bytes sent on the wire.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
from email.utils import parsedate_to_datetime


class SignatureError(ValueError):
    """Raised when an LCP signature cannot be verified."""


def canonical_signing_bytes(
    timestamp: str,
    idempotency_key: str | None,
    body: bytes,
) -> bytes:
    """Build the exact UTF-8 bytes covered by the LCP HMAC."""
    key = idempotency_key or ""
    return f"{timestamp}\n{key}\n".encode("utf-8") + body


def sign_hmac(
    secret: str | bytes,
    timestamp: str,
    idempotency_key: str | None,
    body: bytes,
) -> str:
    """Return a lowercase hexadecimal HMAC-SHA256 signature."""
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    return hmac.new(
        key,
        canonical_signing_bytes(timestamp, idempotency_key, body),
        hashlib.sha256,
    ).hexdigest()


def _parse_timestamp(timestamp: str) -> datetime:
    try:
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        try:
            value = parsedate_to_datetime(timestamp)
        except (TypeError, ValueError) as exc:
            raise SignatureError("Invalid X-LCP-Timestamp") from exc
    if value.tzinfo is None:
        raise SignatureError("X-LCP-Timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def verify_hmac(
    secret: str | bytes,
    signature: str,
    timestamp: str,
    idempotency_key: str | None,
    body: bytes,
    *,
    max_skew_seconds: int = 300,
    now: datetime | None = None,
) -> None:
    """Verify an LCP signature and timestamp, raising SignatureError on failure."""
    if not signature:
        raise SignatureError("Missing X-LCP-Signature")
    signed_at = _parse_timestamp(timestamp)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if abs((current - signed_at).total_seconds()) > max_skew_seconds:
        raise SignatureError("X-LCP-Timestamp is outside the replay window")

    expected = sign_hmac(secret, timestamp, idempotency_key, body)
    if not hmac.compare_digest(signature.lower(), expected):
        raise SignatureError("Invalid X-LCP-Signature")
