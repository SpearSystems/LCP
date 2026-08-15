"""Helpers for verifying inbound LCP HTTP requests before JSON parsing."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from .signing import SignatureError, verify_hmac


_REQUIRED_HEADERS = (
    "X-LCP-Signature",
    "X-LCP-Timestamp",
    "X-LCP-Idempotency-Key",
)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Read a header case-insensitively from a framework mapping."""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def verify_http_request(
    secret: str | bytes,
    headers: Mapping[str, str],
    body: bytes,
    *,
    max_skew_seconds: int = 300,
    now: datetime | None = None,
) -> None:
    """Verify an LCP HMAC request using its raw body.

    This function deliberately does not parse JSON or claim idempotency. The
    application must make its durable idempotency claim after this succeeds.
    """
    values = {name: _header(headers, name) for name in _REQUIRED_HEADERS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise SignatureError(f"Missing required LCP headers: {', '.join(missing)}")
    verify_hmac(
        secret,
        values["X-LCP-Signature"] or "",
        values["X-LCP-Timestamp"] or "",
        values["X-LCP-Idempotency-Key"],
        body,
        max_skew_seconds=max_skew_seconds,
        now=now,
    )
