"""Helpers for constructing LCP envelopes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_timestamp() -> str:
    """Return the current UTC time in the canonical LCP format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_envelope(
    message_type: str,
    sender_id: str,
    receiver_id: str,
    payload: dict[str, Any],
    *,
    version: str = "1.0.0",
    message_id: str | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    timestamp: str | None = None,
    test: bool = False,
) -> dict[str, Any]:
    """Build a complete LCP envelope around a message payload."""
    message_id = message_id or str(uuid4())
    idempotency_key = idempotency_key or f"{sender_id}-{message_type}-{uuid4().hex}"
    return {
        "lcp": {
            "version": version,
            "message": {
                "id": message_id,
                "type": message_type,
                "timestamp": timestamp or utc_timestamp(),
                "sender_id": sender_id,
                "receiver_id": receiver_id,
                "correlation_id": correlation_id,
                "idempotency_key": idempotency_key,
                "test": test,
            },
            "payload": payload,
        }
    }


def message_metadata(envelope: dict[str, Any]) -> dict[str, Any]:
    """Return the message header or raise for a malformed envelope."""
    try:
        return envelope["lcp"]["message"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Envelope must contain lcp.message") from exc
