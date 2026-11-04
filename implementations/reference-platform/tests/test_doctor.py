"""End-to-end tests for ``lcp-doctor`` against a local stub receiver.

Standard library only: a ThreadingHTTPServer stubs the two LCP routes, so no
test touches the public network.
"""

from __future__ import annotations

import io
import json
import os
import socket
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest import mock

from lcp_platform import doctor


CAPABILITIES = {
    "lcp_versions": ["1.0.0", "1.2.0"],
    "receiver_id": "platform_001",
    "features": {"processed_ack": "1.2.0", "batch": "1.2.0"},
    "auth_methods": ["bearer", "hmac"],
    "message_types": ["lead", "ack"],
}

ACCEPTED_ACK = {
    "lcp": {
        "version": "1.2.0",
        "message": {
            "id": "stub-ack",
            "type": "ack",
            "timestamp": "2026-01-01T00:00:00Z",
            "sender_id": "platform_001",
            "receiver_id": "pub_test",
            "idempotency_key": "stub-ack",
        },
        "payload": {"status": "RECEIVED", "lead_id": "doctor-stub"},
    }
}


class StubHandler(BaseHTTPRequestHandler):
    mode = "ok"
    requests: list[dict[str, Any]] = []

    def do_GET(self) -> None:  # noqa: N802
        self._record(b"")
        if self.path == "/v1/lcp/capabilities":
            self._send(200, CAPABILITIES)
        else:
            self._send(404, {"errors": [{"code": "LCP-001", "message": "Not found"}]})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self._record(body)
        if self.path != "/v1/lcp/leads":
            self._send(404, {"errors": [{"code": "LCP-001", "message": "Not found"}]})
            return
        if self.mode == "auth401":
            self._send(
                401,
                {
                    "errors": [
                        {
                            "code": "LCP-002",
                            "name": "UNKNOWN_SENDER",
                            "message": "Unknown sender",
                        }
                    ]
                },
            )
            return
        if self.mode == "schema400":
            self._send(
                400,
                {
                    "errors": [
                        {
                            "code": "LCP-100",
                            "name": "VALIDATION_ERROR",
                            "message": "consumer.phone is not valid E.164",
                            "field": "consumer.phone",
                        }
                    ]
                },
            )
            return
        self._send(200, ACCEPTED_ACK)

    def _record(self, body: bytes) -> None:
        self.__class__.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body,
            }
        )

    def _send(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        pass


def start_stub(mode: str) -> tuple[ThreadingHTTPServer, type[StubHandler], str]:
    handler = type(
        f"StubHandler_{mode}",
        (StubHandler,),
        {"mode": mode, "requests": []},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, handler, f"http://{host}:{port}"


def run_doctor(
    env: dict[str, str], argv: list[str] | None = None
) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with mock.patch.dict(os.environ, env, clear=True):
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = doctor.main(argv or [])
    return code, stdout.getvalue(), stderr.getvalue()


def base_env(endpoint: str) -> dict[str, str]:
    return {
        "LCP_ENDPOINT": endpoint,
        "LCP_SENDER_ID": "pub_test",
        "LCP_API_KEY": "test-api-key",
    }


def closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


class DoctorTests(unittest.TestCase):
    def test_capabilities_ok_and_lead_accepted_exits_zero(self) -> None:
        server, handler, endpoint = start_stub("ok")
        try:
            code, out, err = run_doctor(base_env(endpoint))
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(code, 0, out + err)
        self.assertEqual(err, "")
        self.assertIn("PASS  capabilities", out)
        self.assertIn("lcp_versions=1.0.0,1.2.0", out)
        self.assertIn("auth_methods=bearer,hmac", out)
        self.assertIn("PASS  lead", out)
        self.assertIn("ack=RECEIVED", out)
        self.assertNotIn("Traceback", out)

        lead_request = next(
            request for request in handler.requests if request["method"] == "POST"
        )
        headers = lead_request["headers"]
        self.assertEqual(headers["x-lcp-test"], "true")
        self.assertEqual(headers["x-lcp-sender-id"], "pub_test")
        self.assertEqual(headers["authorization"], "Bearer test-api-key")
        envelope = json.loads(lead_request["body"])
        message = envelope["lcp"]["message"]
        self.assertTrue(message["test"])
        self.assertEqual(message["type"], "lead")
        self.assertEqual(message["receiver_id"], "platform_001")
        self.assertEqual(headers["x-lcp-idempotency-key"], message["idempotency_key"])
        lead = envelope["lcp"]["payload"]
        for field in (
            "lead_id",
            "status",
            "channel",
            "consumer",
            "location",
            "attributes",
        ):
            self.assertIn(field, lead)

    def test_auth_401_shows_translated_lcp_002_fix(self) -> None:
        server, _, endpoint = start_stub("auth401")
        try:
            code, out, err = run_doctor(base_env(endpoint))
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(code, 1)
        self.assertEqual(err, "")
        self.assertIn("FAIL  lead", out)
        self.assertIn("LCP-002", out)
        self.assertIn("cause", out)
        self.assertIn("fix", out)
        self.assertIn("Authorization: Bearer", out)
        self.assertIn("receiver_id", out)
        self.assertNotIn("Traceback", out)

    def test_schema_400_names_the_field_and_lcp_100_fix(self) -> None:
        server, _, endpoint = start_stub("schema400")
        try:
            code, out, err = run_doctor(base_env(endpoint))
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(code, 1)
        self.assertEqual(err, "")
        self.assertIn("FAIL  lead", out)
        self.assertIn("LCP-100", out)
        self.assertIn("field=consumer.phone", out)
        self.assertIn("errors[].field", out)
        self.assertNotIn("Traceback", out)

    def test_unreachable_host_prints_one_clear_line_without_traceback(self) -> None:
        endpoint = f"http://127.0.0.1:{closed_port()}"
        code, out, err = run_doctor(base_env(endpoint))

        self.assertEqual(code, 1)
        self.assertEqual(err, "")
        self.assertIn("FAIL  capabilities", out)
        self.assertIn("unreachable", out)
        self.assertIn("Check LCP_ENDPOINT", out)
        self.assertNotIn("Traceback", out)
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, out)

    def test_explain_works_without_an_endpoint(self) -> None:
        code, out, err = run_doctor({}, ["--explain", "LCP-009"])

        self.assertEqual(code, 0, out + err)
        self.assertEqual(err, "")
        self.assertIn("LCP-009 INVALID_PHONE", out)
        self.assertIn("E.164", out)
        self.assertIn("Fix:", out)
        self.assertIn("docs/ERROR-GUIDE.md", out)

    def test_hmac_headers_use_the_spec_canonical_input(self) -> None:
        from lcp_platform.auth import signature_for

        config = doctor.Config(
            endpoint="https://example.invalid",
            sender_id="pub_test",
            hmac_secret="shared-secret",
        )
        body = b'{"lcp":{}}'
        headers = doctor._signature_headers(
            config, body, "idem-123", "2026-01-01T00:00:00Z"
        )

        self.assertEqual(headers["X-LCP-Timestamp"], "2026-01-01T00:00:00Z")
        self.assertEqual(
            headers["X-LCP-Signature"],
            signature_for("shared-secret", "2026-01-01T00:00:00Z", "idem-123", body),
        )
        self.assertNotIn("Authorization", headers)

    def test_json_output_is_machine_readable(self) -> None:
        server, _, endpoint = start_stub("ok")
        try:
            code, out, err = run_doctor(base_env(endpoint), ["--json"])
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(code, 0, out + err)
        self.assertEqual(err, "")
        document = json.loads(out)
        self.assertTrue(document["ok"])
        self.assertEqual(
            [stage["stage"] for stage in document["stages"]],
            ["capabilities", "lead"],
        )
        self.assertEqual(document["capabilities"]["receiver_id"], "platform_001")

    def test_every_spec_error_code_is_translated(self) -> None:
        expected = {
            "LCP-001": 400,
            "LCP-002": 401,
            "LCP-003": 400,
            "LCP-004": 422,
            "LCP-005": 409,
            "LCP-006": 400,
            "LCP-007": 422,
            "LCP-008": 400,
            "LCP-009": 400,
            "LCP-010": 422,
            "LCP-011": 429,
            "LCP-012": 401,
            "LCP-013": 400,
            "LCP-100": 400,
            "LCP-500": 500,
        }
        self.assertEqual(set(doctor.ERROR_FIXES), set(expected))
        for code, http_status in expected.items():
            with self.subTest(code=code):
                entry = doctor.ERROR_FIXES[code]
                self.assertTrue(entry["meaning"])
                self.assertEqual(entry["http"], http_status)
                self.assertGreaterEqual(len(entry["causes"]), 3)
                self.assertEqual(entry["likely_cause"], entry["causes"][0])
                self.assertTrue(entry["fix"])


if __name__ == "__main__":
    unittest.main()
