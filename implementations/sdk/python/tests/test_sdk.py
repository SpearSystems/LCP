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

    def _ping_envelope(self, ping_id: str, vertical: str, attributes: dict) -> dict:
        return build_envelope(
            "ping",
            "publisher_001",
            "platform_001",
            {
                "ping_id": ping_id,
                "lead_reference": "ref_xyz789",
                "phone_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
                "country_code": "AU",
                "floor_price_cents": 1500,
                "currency": "AUD",
                "vertical": vertical,
                "attributes": attributes,
            },
            timestamp="2026-08-15T10:20:00Z",
            idempotency_key=f"sdk-{ping_id}",
        )

    def test_ping_vertical_name_is_never_used_as_a_path(self) -> None:
        """A malicious vertical must not escape the vertical schema directory."""
        validator = SchemaValidator(ROOT / "schemas")
        envelope = self._ping_envelope(
            "ping_traversal_001", "../../../../etc/passwd", {"home_ownership": "owned"}
        )
        # Must not raise and must not attempt filesystem access; the vertical is
        # resolved against the preloaded schema map only.
        errors = validator.validate_envelope(envelope)
        self.assertEqual(
            errors,
            ["vertical schema '../../../../etc/passwd' not found for ping-safe validation"],
        )

    def test_ping_vertical_resolves_preloaded_schema(self) -> None:
        validator = SchemaValidator(ROOT / "schemas")
        envelope = self._ping_envelope(
            "ping_safe_001", "mortgage", {"loan_type": "refinance"}
        )
        self.assertEqual(validator.validate_envelope(envelope), [])


if __name__ == "__main__":
    unittest.main()
