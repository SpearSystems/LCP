#!/usr/bin/env python3
"""Python SDK interop probe for tools/sdk_interop_matrix.py (LEP-0002 step 1).

Signs the fixed vector message through the SDK's own signing entry points and
formats the SDK's real timestamp generator against a frozen clock (epoch
1786789200.123456789 = 2026-08-15T10:20:00.123456789Z, non-zero sub-second).
Emits one ``LCP_INTEROP {json}`` line; never asserts, never fails on shape.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

# Reuse the SDK package whether installed or checked out (PYTHONPATH fallback).
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

from lcp_sdk import canonical_signing_bytes, sign_hmac  # noqa: E402
import lcp_sdk.envelope as envelope_module  # noqa: E402

FROZEN_NANOS = 1_786_789_200_123_456_789


def resolve_vector_path() -> str:
    if len(sys.argv) > 2 and sys.argv[1] == "--vector":
        return sys.argv[2]
    if os.environ.get("LCP_VECTOR"):
        return os.environ["LCP_VECTOR"]
    directory = os.getcwd()
    while True:
        candidate = os.path.join(directory, "test-vectors", "sdk", "hmac.json")
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:
            raise SystemExit("hmac.json vector not found (pass --vector)")
        directory = parent


def frozen_utc_timestamp() -> str:
    """Drive the SDK's own utc_timestamp() at a frozen sub-second instant.

    lcp_sdk.envelope.utc_timestamp reads module-level ``datetime``; patching it
    is the SDK-supported seam (same object the client's header path calls).
    """

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime(2026, 8, 15, 10, 20, 0, 123456, tzinfo=tz or timezone.utc)

    original = envelope_module.datetime
    envelope_module.datetime = FrozenDateTime
    try:
        return envelope_module.utc_timestamp()
    finally:
        envelope_module.datetime = original


def main() -> int:
    with open(resolve_vector_path(), encoding="utf-8") as handle:
        vector = json.load(handle)
    body = vector["body_utf8"].encode()
    canonical = canonical_signing_bytes(
        vector["timestamp"], vector["idempotency_key"], body
    )
    result = {
        "sdk": "python",
        "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
        "signature_hex": sign_hmac(
            vector["secret"], vector["timestamp"], vector["idempotency_key"], body
        ),
        "emitted_timestamp": frozen_utc_timestamp(),
    }
    line = "LCP_INTEROP " + json.dumps(result, sort_keys=True)
    print(line)
    out = os.environ.get("LCP_INTEROP_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(result, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
