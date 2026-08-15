from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest

from lcp_sdk import (
    SchemaValidator,
    SignatureError,
    build_envelope,
    sign_hmac,
    verify_hmac,
    verify_http_request,
)


ROOT = Path(__file__).resolve().parents[4]


class SDKTests(unittest.TestCase):
    def test_build_envelope_and_validate(self) -> None:
        envelope = build_envelope(
            "lead",
            "publisher_001",
            "platform_001",
            {
                "lead_id": "lead_sdk_001",
                "status": "NEW",
                "channel": "form",
                "consumer": {"phone": "+61412345678"},
                "location": {"country_code": "AU", "postal_code": "2000"},
                "attributes": {"vertical": "mortgage", "schema_version": "1.0.0"},
            },
            timestamp="2026-08-15T10:20:00Z",
            idempotency_key="sdk-test-001",
        )
        validator = SchemaValidator(ROOT / "schemas")
        self.assertEqual(validator.validate_envelope(envelope), [])

    def test_canonical_signature_round_trip(self) -> None:
        body = b'{"hello":"world"}'
        timestamp = "2026-08-15T10:20:00Z"
        signature = sign_hmac("secret", timestamp, "key-001", body)
        verify_hmac(
            "secret",
            signature,
            timestamp,
            "key-001",
            body,
            now=datetime(2026, 8, 15, 10, 20, 1, tzinfo=timezone.utc),
        )

    def test_webhook_verification_uses_raw_body_and_case_insensitive_headers(self) -> None:
        body = b'{"hello":"world"}'
        timestamp = "2026-08-15T10:20:00Z"
        key = "sdk-vector-001"
        signature = sign_hmac("sdk-shared-secret", timestamp, key, body)
        verify_http_request(
            "sdk-shared-secret",
            {
                "x-lcp-signature": signature,
                "x-lcp-timestamp": timestamp,
                "x-lcp-idempotency-key": key,
            },
            body,
            now=datetime(2026, 8, 15, 10, 20, 1, tzinfo=timezone.utc),
        )

    def test_stale_signature_is_rejected(self) -> None:
        timestamp = "2026-08-15T10:00:00Z"
        signature = sign_hmac("secret", timestamp, "key-001", b"{}")
        with self.assertRaises(SignatureError):
            verify_hmac(
                "secret",
                signature,
                timestamp,
                "key-001",
                b"{}",
                now=datetime(2026, 8, 15, 10, 10, tzinfo=timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
