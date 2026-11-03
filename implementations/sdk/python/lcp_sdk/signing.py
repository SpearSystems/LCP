"""LCP HTTP authentication helpers.

The canonical signing input is:

    <timestamp>\n<idempotency-key>\n<raw-request-body>

The body must be the exact bytes sent on the wire.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
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
    return f"{timestamp}\n{key}\n".encode() + body


def _hmac_sha256_hex(key: bytes, data: bytes) -> str:
    """The SDK's single HMAC-SHA256→lowercase-hex construction (SPEC.md §9)."""
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def sign_hmac(
    secret: str | bytes,
    timestamp: str,
    idempotency_key: str | None,
    body: bytes,
) -> str:
    """Return a lowercase hexadecimal HMAC-SHA256 signature."""
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    return _hmac_sha256_hex(
        key, canonical_signing_bytes(timestamp, idempotency_key, body)
    )


CONTACT_HASH_DOMAINS = ("phone", "email")


def normalize_contact(value: str, domain: str) -> str:
    """Return the SPEC.md §9 pinned contact preimage for a value.

    Phone: the wire value verbatim, byte-for-byte — no folding, stripping,
    or reformatting (the reference platform's ``phone_hash`` builder is the
    executed baseline; ``consumer.phone`` is E.164 by core schema on
    conforming traffic). Email: the §9 pinned candidate canonical — trim
    surrounding whitespace, ASCII-lowercase the domain, keep the local-part
    bytes verbatim, plus-addresses distinct. Email-form ``contacted_hashes``
    entries stay emission-gated (no email-form entry ships until #153 T9
    executed vectors publish); this helper computes the pinned rule so the
    SDK can reproduce every ``test-vectors/sdk/contact-hash.json`` row.
    """
    if domain == "phone":
        return value
    if domain == "email":
        trimmed = value.strip()
        local, separator, domain_part = trimmed.rpartition("@")
        if not separator:
            raise ValueError("email contact preimage must contain '@'")
        return f"{local}@{_ascii_lowercase(domain_part)}"
    raise ValueError(f"unknown contact domain: {domain!r} (expected phone or email)")


def _ascii_lowercase(value: str) -> str:
    """Lowercase ASCII letters only; non-ASCII bytes are kept verbatim."""
    return "".join(
        chr(ord(char) + 32) if "A" <= char <= "Z" else char for char in value
    )


def contact_hash(pair_secret: str | bytes, value: str, *, domain: str = "phone") -> str:
    """Return the per-pair contact hash (SPEC.md §9, LEP-0003 §3): lowercase-hex

    ``HMAC-SHA256(pair_secret, normalized_preimage)`` keyed with the secret
    shared with the message's receiver. ``phone_hash``, ``email_hash``, and
    ``contacted_hashes`` entries all ride this one construction — the same
    digest path as ``sign_hmac`` (never an unsalted digest, never SHA-1).
    """
    key = pair_secret.encode("utf-8") if isinstance(pair_secret, str) else pair_secret
    return _hmac_sha256_hex(key, normalize_contact(value, domain).encode("utf-8"))


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
