"""Tests for the self-hostable conformance runner (D11).

Deterministic and offline: every target is either a URL that the SSRF policy
must refuse before any socket is opened, or the in-process WSGI stub. The
repository vector suite is covered by its own CI job, so these tests exercise
the runner's plumbing (`run_schema_checks=False`).
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))

import conformance_runner as runner  # noqa: E402

SECRET = "sk-live-do-not-echo-0123456789"


# --- helpers -----------------------------------------------------------------


def _call_wsgi(
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    raw: bytes | None = None,
    content_length: str | None = None,
):
    body = (
        raw
        if raw is not None
        else (json.dumps(payload).encode("utf-8") if payload is not None else b"")
    )
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": (
            str(len(body)) if content_length is None else content_length
        ),
        "wsgi.input": io.BytesIO(body),
    }
    captured: dict = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    chunks = runner.application(environ, start_response)
    return captured["status"], json.loads(b"".join(chunks))


class _CaptureHandler(logging.Handler):
    """Collects emitted records so a test can assert on the log text."""

    def __init__(self) -> None:
        super().__init__()
        self.stream = io.StringIO()
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self.stream.write(self.format(record) + "\n")

    @property
    def text(self) -> str:
        return self.stream.getvalue()


def _sample_report() -> dict:
    return runner.build_report(
        "https://receiver.example",
        "bearer",
        ["1.2.0"],
        [runner.Check("transport.capabilities", "pass", "ok")],
    )


class ConformanceRunnerTests(unittest.TestCase):
    def _stub_target(self) -> str:
        server, thread, base_url = runner.start_stub_target(secret=SECRET)
        self.addCleanup(runner.stop_stub_target, server, thread)
        return base_url

    # --- SSRF policy ---------------------------------------------------------

    def test_refuses_non_http_schemes(self) -> None:
        for base_url in (
            "file:///etc/passwd",
            "ftp://example.com/vector",
            "gopher://example.com:70/_x",
            "javascript:alert(1)",
            "example.com/v1/lcp",  # no scheme at all
        ):
            with self.subTest(base_url=base_url):
                with self.assertRaises(runner.TargetRefused):
                    runner.resolve_target(base_url)

    def test_refuses_loopback_and_unspecified_targets(self) -> None:
        for base_url in (
            "http://127.0.0.1:8099",
            "http://127.1.2.3/",
            "http://localhost:8099",
            "http://[::1]:8099",
            "https://0.0.0.0/",
        ):
            with self.subTest(base_url=base_url):
                with self.assertRaises(runner.TargetRefused):
                    runner.resolve_target(base_url)

    def test_refuses_cloud_metadata_address(self) -> None:
        with self.assertRaises(runner.TargetRefused) as ctx:
            runner.resolve_target("http://169.254.169.254/latest/meta-data/")
        self.assertIn("link-local", str(ctx.exception))

    def test_refuses_private_targets(self) -> None:
        for base_url in (
            "http://10.0.0.5/",
            "http://172.16.5.4:8443/",
            "http://192.168.1.10/",
            "http://[fd00::1]/",
        ):
            with self.subTest(base_url=base_url):
                with self.assertRaises(runner.TargetRefused):
                    runner.resolve_target(base_url)

    def test_refuses_credentials_in_target_url(self) -> None:
        with self.assertRaises(runner.TargetRefused):
            runner.resolve_target("https://user:pass@example.com/")

    def test_address_policy_classifies_link_local_v6_and_reserved(self) -> None:
        self.assertEqual(runner.blocked_address_reason("fe80::1"), "link-local")
        self.assertEqual(runner.blocked_address_reason("169.254.169.254"), "link-local")
        self.assertEqual(runner.blocked_address_reason("::ffff:127.0.0.1"), "loopback")
        self.assertEqual(runner.blocked_address_reason("240.0.0.1"), "reserved")
        self.assertIsNone(runner.blocked_address_reason("93.184.216.34"))

    def test_allow_private_permits_loopback_and_pins_it(self) -> None:
        target = runner.resolve_target("http://127.0.0.1:8099/v1", allow_private=True)
        self.assertEqual(target.pinned_ip, "127.0.0.1")
        self.assertEqual(target.port, 8099)
        self.assertEqual(target.base_path, "/v1")

    def test_dns_answer_is_rechecked_after_resolution(self) -> None:
        """A public-looking name that resolves privately is still refused."""
        with mock.patch.object(
            runner.socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
            ],
        ):
            with self.assertRaises(runner.TargetRefused) as ctx:
                runner.resolve_target("http://rebind.attacker.example/")
        self.assertIn("loopback", str(ctx.exception))

    def test_request_connects_to_the_pinned_address(self) -> None:
        """DNS is resolved once and the socket goes to that exact address."""
        resolve_calls: list[tuple] = []

        def fake_getaddrinfo(host, port, *args, **kwargs):
            resolve_calls.append((host, port))
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
            ]

        client_sock, server_sock = socket.socketpair()
        server_sock.settimeout(5)
        connected: list[tuple] = []
        seen_request: dict[str, bytes] = {}

        def fake_create_connection(address, timeout=None, source_address=None):
            connected.append(address)
            return client_sock

        def serve() -> None:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = server_sock.recv(65536)
                if not chunk:
                    break
                data += chunk
            seen_request["raw"] = data
            server_sock.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nX-Test: 1\r\n\r\nok"
            )
            server_sock.close()

        try:
            with (
                mock.patch.object(runner.socket, "getaddrinfo", fake_getaddrinfo),
                mock.patch.object(
                    runner.socket, "create_connection", fake_create_connection
                ),
            ):
                thread = threading.Thread(target=serve, daemon=True)
                thread.start()

                target = runner.resolve_target("http://rebind.example/")
                status, headers, body = runner.http_request(
                    target, "GET", "/v1/lcp/capabilities", timeout=5
                )
                thread.join(timeout=5)
        finally:
            client_sock.close()

        self.assertEqual((status, headers["x-test"], body), (200, "1", b"ok"))
        self.assertEqual(connected, [("93.184.216.34", 80)])
        self.assertEqual(len(resolve_calls), 1)
        # The Host header and certificate check still use the advertised name.
        self.assertIn(b"Host: rebind.example", seen_request["raw"])

    # --- report shape and digest ---------------------------------------------

    def test_digest_is_sha256_over_the_canonical_body(self) -> None:
        report = _sample_report()
        expected = hashlib.sha256(runner.canonical_report_bytes(report)).hexdigest()
        self.assertEqual(report["digest"], expected)
        self.assertEqual(len(report["digest"]), 64)
        self.assertNotIn("digest", json.loads(runner.canonical_report_bytes(report)))

    def test_digest_changes_when_a_check_changes(self) -> None:
        report = _sample_report()
        tampered = copy.deepcopy(report)
        tampered["checks"][0]["detail"] = "different"
        self.assertNotEqual(runner.report_digest(tampered), report["digest"])

    def test_digest_is_key_order_independent(self) -> None:
        report = _sample_report()
        reordered = dict(reversed(list(report.items())))
        self.assertEqual(runner.report_digest(reordered), report["digest"])

    def test_report_shape(self) -> None:
        report = _sample_report()
        self.assertEqual(
            set(report),
            {
                "report_id",
                "generated_at",
                "target",
                "lcp_versions",
                "checks",
                "summary",
                "digest",
            },
        )
        self.assertEqual(
            report["summary"],
            {
                "total": 1,
                "passed": 1,
                "failed": 0,
                "skipped": 0,
                "status": "pass",
            },
        )
        self.assertEqual(
            report["target"],
            {
                "base_url": "https://receiver.example",
                "auth_method": "bearer",
            },
        )

    def test_parse_suite_output(self) -> None:
        stdout = (
            "\n  L1 - l1-envelope.json\n"
            "  PASS  L1-001 valid_lead_envelope\n"
            "  FAIL  L1-002 broken_vector\n"
            "        -> expected failure but message validated\n"
            "        -> second detail\n"
            "\n  L1: 1 passed, 1 failed\n"
        )
        checks = runner.parse_suite_output(stdout)
        self.assertEqual(
            [(c.id, c.status) for c in checks],
            [
                ("vector.L1-001", "pass"),
                ("vector.L1-002", "fail"),
            ],
        )
        self.assertEqual(
            checks[1].detail, ("expected failure but message validated; second detail")
        )

    # --- credential handling -------------------------------------------------

    def test_redact_strips_the_secret(self) -> None:
        self.assertNotIn(SECRET, runner.redact(f"target echoed {SECRET}!", SECRET))
        self.assertIn(runner.REDACTED, runner.redact(f"echo {SECRET}", SECRET))

    def test_api_key_absent_from_report_and_logs(self) -> None:
        handler = _CaptureHandler()
        logger = logging.getLogger(runner.SERVICE_NAME)
        previous_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(logger.setLevel, previous_level)

        stub_target = self._stub_target()
        runner.configure(allow_private=True)
        try:
            report = runner.run_conformance(
                stub_target,
                "bearer",
                SECRET,
                allow_private=True,
                run_schema_checks=False,
            )
        finally:
            runner.configure()

        self.assertNotIn(SECRET, json.dumps(report))
        self.assertNotIn(SECRET, handler.text)
        self.assertEqual(report["summary"]["status"], "pass")
        self.assertEqual(report["lcp_versions"], ["1.1.0", "1.2.0"])
        self.assertEqual(report["digest"], runner.report_digest(report))

    def test_report_index_keeps_only_digest_and_summary(self) -> None:
        stub_target = self._stub_target()
        report = runner.run_conformance(
            stub_target, "bearer", SECRET, allow_private=True, run_schema_checks=False
        )
        stored = runner._REPORT_INDEX[report["report_id"]]
        self.assertEqual(set(stored), {"digest", "summary"})
        self.assertEqual(stored["digest"], report["digest"])
        self.assertNotIn(SECRET, json.dumps(stored))
        self.assertNotIn(stub_target, json.dumps(stored))

    def test_echoed_secret_is_redacted_from_details(self) -> None:
        def echoing_transport(target, auth_method, api_key, sender_id, timeout):
            return [
                runner.Check("transport.capabilities", "fail", f"echo {api_key}")
            ], []

        stub_target = self._stub_target()
        with mock.patch.object(runner, "transport_checks", echoing_transport):
            report = runner.run_conformance(
                stub_target,
                "bearer",
                SECRET,
                allow_private=True,
                run_schema_checks=False,
            )
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertEqual(report["checks"][0]["detail"], f"echo {runner.REDACTED}")

    def test_hmac_probe_is_signed_and_accepted(self) -> None:
        stub_target = self._stub_target()
        report = runner.run_conformance(
            stub_target,
            "hmac",
            SECRET,
            sender_id="sender-1",
            allow_private=True,
            run_schema_checks=False,
        )
        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["transport.auth.credentialed_read"]["status"], "pass")
        self.assertEqual(
            checks["transport.capabilities.auth_methods"]["status"], "pass"
        )

    def test_hmac_probe_without_sender_id_is_skipped(self) -> None:
        stub_target = self._stub_target()
        report = runner.run_conformance(
            stub_target, "hmac", SECRET, allow_private=True, run_schema_checks=False
        )
        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(
            checks["transport.auth.credentialed_read"]["status"], "skipped"
        )

    def test_unknown_auth_method_is_refused(self) -> None:
        for auth_method in ("basic", "digest", ""):
            with self.subTest(auth_method=auth_method):
                with self.assertRaises(runner.TargetRefused):
                    runner.run_conformance(
                        "https://receiver.example",
                        auth_method,
                        SECRET,
                        run_schema_checks=False,
                    )

    def test_credentialed_method_requires_a_key(self) -> None:
        with self.assertRaises(runner.TargetRefused):
            runner.run_conformance(
                "https://receiver.example", "bearer", None, run_schema_checks=False
            )

    # --- local self-test against the stub target -----------------------------

    def test_self_test_report_against_stub_target(self) -> None:
        stub_target = self._stub_target()
        report = runner.run_conformance(
            stub_target, "bearer", SECRET, allow_private=True, run_schema_checks=False
        )
        transport = [c for c in report["checks"] if c["id"].startswith("transport.")]
        self.assertTrue(
            transport and all(check["status"] == "pass" for check in transport)
        )
        skipped = [c for c in report["checks"] if c["status"] == "skipped"]
        self.assertEqual([c["id"] for c in skipped], ["vector.suite"])
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertEqual(report["target"]["base_url"], stub_target)
        self.assertEqual(report["target"]["auth_method"], "bearer")

    # --- WSGI surface --------------------------------------------------------

    def test_healthz(self) -> None:
        status, payload = _call_wsgi("GET", "/healthz")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(payload, {"status": "ok", "service": runner.SERVICE_NAME})

    def test_healthz_rejects_post(self) -> None:
        status, payload = _call_wsgi("POST", "/healthz")
        self.assertTrue(status.startswith("405"))
        self.assertEqual(payload["errors"][0]["code"], "RUNNER-METHOD-NOT-ALLOWED")

    def test_unknown_route(self) -> None:
        status, payload = _call_wsgi("GET", "/nope")
        self.assertTrue(status.startswith("404"))
        self.assertEqual(payload["errors"][0]["code"], "RUNNER-NOT-FOUND")

    def test_run_requires_post(self) -> None:
        status, _payload = _call_wsgi("GET", "/run")
        self.assertTrue(status.startswith("405"))

    def test_run_rejects_invalid_json(self) -> None:
        status, payload = _call_wsgi("POST", "/run", raw=b"{not json")
        self.assertTrue(status.startswith("400"))
        self.assertEqual(payload["errors"][0]["code"], "RUNNER-BAD-JSON")

    def test_run_rejects_oversized_body(self) -> None:
        status, payload = _call_wsgi(
            "POST", "/run", raw=b"{}", content_length=str(runner.MAX_REQUEST_BYTES + 1)
        )
        self.assertTrue(status.startswith("413"))
        self.assertEqual(payload["errors"][0]["code"], "RUNNER-BODY-TOO-LARGE")

    def test_run_refuses_ssrf_target_through_http(self) -> None:
        status, payload = _call_wsgi(
            "POST",
            "/run",
            {"base_url": "http://169.254.169.254/", "auth_method": "none"},
        )
        self.assertTrue(status.startswith("400"))
        self.assertEqual(payload["errors"][0]["code"], "RUNNER-TARGET-REFUSED")

    def test_run_rejects_non_string_api_key(self) -> None:
        status, payload = _call_wsgi(
            "POST",
            "/run",
            {
                "base_url": "https://receiver.example",
                "auth_method": "bearer",
                "api_key": 7,
            },
        )
        self.assertTrue(status.startswith("400"))
        self.assertEqual(payload["errors"][0]["code"], "RUNNER-BAD-REQUEST")

    def test_run_returns_report_for_stub_target(self) -> None:
        stub_target = self._stub_target()
        with (
            mock.patch.object(runner, "_ALLOW_PRIVATE", True),
            mock.patch.object(runner, "_RUN_SCHEMA_CHECKS", False),
        ):
            status, payload = _call_wsgi(
                "POST",
                "/run",
                {
                    "base_url": stub_target,
                    "auth_method": "bearer",
                    "api_key": SECRET,
                },
            )
        self.assertTrue(status.startswith("200"))
        self.assertEqual(payload["digest"], runner.report_digest(payload))
        self.assertEqual(payload["summary"]["status"], "pass")
        self.assertNotIn(SECRET, json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
