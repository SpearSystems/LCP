from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest

from lcp_sdk import (
    LCPClient,
    LCPHTTPError,
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


class _RefusingHandler(BaseHTTPRequestHandler):
    """Serves one canned 401 whose message carries a do-not-log marker."""

    def do_GET(self):
        payload = (
            b'{"errors":[{"code":"LCP-002","message":"jane.smith@example.com '
            b'+61412345678 DO-NOT-LOG"}],"request_id":"req-server-1"}'
        )
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-LCP-Request-Id", "req-server-1")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # keep output clean
        pass


class HTTPErrorHygieneTests(unittest.TestCase):
    """#136: LCPHTTPError must not embed the response body in str()/repr().

    Exceptions end up in logs and tracebacks; a body echoed there leaks
    whatever the server put in it (PII in error messages, arbitrarily large
    payloads). The body stays on ``.body`` for programmatic access, bounded,
    while the error string carries status + request id only — the pattern the
    TypeScript (LcpHttpError) and Go (HTTPError) SDKs already follow.
    """

    def test_error_string_excludes_body_keeps_status_and_request_id(self) -> None:
        body = {
            "errors": [{"code": "LCP-002", "message": "jane.smith DO-NOT-LOG"}],
            "request_id": "req-42",
        }
        error = LCPHTTPError(401, body, request_id="req-42")
        for rendered in (str(error), repr(error)):
            self.assertNotIn("DO-NOT-LOG", rendered)
            self.assertNotIn("jane.smith", rendered)
            self.assertIn("401", rendered)
            self.assertIn("req-42", rendered)
        # Body stays available verbatim when it fits the documented bound.
        self.assertEqual(error.body, body)

    def test_body_is_bounded_for_operator_debugging(self) -> None:
        from lcp_sdk.client import MAX_BODY_CHARS

        huge = "x" * (MAX_BODY_CHARS + 10_000)
        str_error = LCPHTTPError(500, huge)
        self.assertLessEqual(len(str_error.body), MAX_BODY_CHARS + 64)
        self.assertTrue(str_error.body.endswith("[truncated]"))

        # A structured body past the bound collapses to a preview marker;
        # one inside the bound is kept intact so callers can read codes.
        big = {"errors": [{"code": "LCP-100", "message": "y" * (MAX_BODY_CHARS + 1)}]}
        dict_error = LCPHTTPError(400, big)
        self.assertEqual(dict_error.body["truncated"], True)
        self.assertLessEqual(len(dict_error.body["preview"]), MAX_BODY_CHARS + 64)
        small = {"errors": [{"code": "LCP-002", "message": "Unknown sender"}]}
        self.assertEqual(LCPHTTPError(401, small).body, small)

    def test_client_error_carries_server_request_id_and_hides_body(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RefusingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = LCPClient(
                f"http://127.0.0.1:{server.server_address[1]}",
                api_key="any-key",
                max_retries=0,
            )
            with self.assertRaises(LCPHTTPError) as caught:
                client.query_lead_status("lead_1")
            error = caught.exception
            self.assertEqual(error.status_code, 401)
            self.assertEqual(error.request_id, "req-server-1")
            self.assertNotIn("DO-NOT-LOG", str(error))
            self.assertNotIn("DO-NOT-LOG", repr(error))
            # Structured access is intact for callers that need the details.
            self.assertEqual(error.body["errors"][0]["code"], "LCP-002")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
