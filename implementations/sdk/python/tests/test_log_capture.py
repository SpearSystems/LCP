"""Runtime log-cleanliness tests (SDK-ROADMAP invariant 7, dynamic half).

``tools/check_logging_hygiene.py`` statically proves the Python SDK contains
no stdout/stderr output calls or logger hooks. These tests pin the runtime
half of the invariant: around three PII-bearing flows — signing/verifying the
realistic ``post`` fixture, a schema-validation failure, and a webhook
verification failure — the ``lcp_sdk`` package must write nothing to
stdout/stderr, and the exception text an application would log must not
contain any consumer field (name, phone digits, email localpart) or the
serialized envelope.

This file is deliberately network-free and deterministic: the post fixture is
read from ``examples/post.json``, fixed timestamps bound the replay window,
and an explicit ``now`` is injected into every verification call.

Fail-loud note for reviewers: the app-logging simulation in the error flows
(``print(..., file=sys.stderr)`` of the caught SDK error) is the actual leak
route this guards — if an SDK error message ever grows payload/PII content,
the captured stream assertions below go red.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import unittest

from lcp_sdk import (
    SchemaValidator,
    SignatureError,
    ValidationError,
    sign_hmac,
    verify_hmac,
    verify_http_request,
)

ROOT = Path(__file__).resolve().parents[4]

SECRET = "log-capture-shared-secret"
# Fixed instant inside the 300 s replay window of the post fixture timestamp.
NOW = datetime(2026, 8, 15, 10, 20, 40, tzinfo=timezone.utc)

# Consumer identifiers present in examples/post.json. None may surface in any
# captured stream on any tested path.
PII_MARKERS = (
    "Jane",
    "Smith",
    "Jane Smith",
    "+61412345678",
    "412345678",
    "jane.smith",
    "Sydney",
)


def _capture(action):
    """Run ``action`` with stdout/stderr redirected; return both streams."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        action()
    return out.getvalue(), err.getvalue()


class LogCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.body = (ROOT / "examples" / "post.json").read_bytes()
        self.envelope = json.loads(self.body)
        message = self.envelope["lcp"]["message"]
        self.timestamp = message["timestamp"]
        self.idempotency_key = message["idempotency_key"]
        # Serialised envelope variants that must never hit a stream.
        self.serializations = [
            self.body.decode("utf-8"),
            json.dumps(self.envelope),
            json.dumps(self.envelope, separators=(",", ":")),
        ]
        self.validator = SchemaValidator(ROOT / "schemas")

    def _assert_streams_clean(self, out: str, err: str) -> None:
        for stream_name, stream in (("stdout", out), ("stderr", err)):
            for marker in PII_MARKERS:
                self.assertNotIn(
                    marker,
                    stream,
                    f"PII marker {marker!r} appeared on {stream_name}",
                )
            for serialization in self.serializations:
                self.assertNotIn(
                    serialization.strip(),
                    stream,
                    f"serialized envelope appeared on {stream_name}",
                )

    def test_happy_path_sign_and_verify_emits_nothing(self) -> None:
        """Signing and verifying the PII post fixture must be fully silent."""

        def action() -> None:
            signature = sign_hmac(
                SECRET, self.timestamp, self.idempotency_key, self.body
            )
            verify_hmac(
                SECRET,
                signature,
                self.timestamp,
                self.idempotency_key,
                self.body,
                max_skew_seconds=300,
                now=NOW,
            )

        out, err = _capture(action)
        self.assertEqual(out, "", "SDK wrote to stdout on the happy path")
        self.assertEqual(err, "", "SDK wrote to stderr on the happy path")

    def test_malformed_envelope_error_path_stays_clean(self) -> None:
        """A validation failure plus application logging must not echo PII."""
        broken = json.loads(self.body)
        del broken["lcp"]["payload"]["delivered_at"]

        def action() -> None:
            try:
                self.validator.require_valid_envelope(broken)
            except ValidationError as exc:
                # Fail loud if the malformed fixture stops being malformed.
                self.assertIn("delivered_at", str(exc))
                # Simulate the framework default: log the SDK error text.
                print(f"validation failed: {exc}", file=sys.stderr)
                return
            self.fail("malformed post envelope must raise ValidationError")

        out, err = _capture(action)
        self.assertEqual(out, "")
        self._assert_streams_clean(out, err)

    def test_webhook_verify_failure_paths_stay_clean(self) -> None:
        """Webhook rejection over the PII body must not echo the envelope."""
        tampered_headers = {
            "X-LCP-Signature": "0" * 64,
            "X-LCP-Timestamp": self.timestamp,
            "X-LCP-Idempotency-Key": self.idempotency_key,
        }
        missing_headers = {
            "X-LCP-Timestamp": self.timestamp,
            "X-LCP-Idempotency-Key": self.idempotency_key,
        }

        def action() -> None:
            for headers, expected in (
                (tampered_headers, "Invalid X-LCP-Signature"),
                (missing_headers, "Missing required LCP headers"),
            ):
                try:
                    verify_http_request(SECRET, headers, self.body, now=NOW)
                except SignatureError as exc:
                    self.assertIn(expected, str(exc))
                    print(f"webhook rejected: {exc}", file=sys.stderr)
                else:
                    self.fail(f"webhook verification must reject {headers.keys()}")

        out, err = _capture(action)
        self.assertEqual(out, "")
        self._assert_streams_clean(out, err)


if __name__ == "__main__":
    unittest.main()
