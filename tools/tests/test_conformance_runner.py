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
from pathlib import Path

import pytest

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


@pytest.fixture()
def stub_target():
    server, thread, base_url = runner.start_stub_target(secret=SECRET)
    try:
        yield base_url
    finally:
        runner.stop_stub_target(server, thread)


# --- SSRF policy -------------------------------------------------------------


@pytest.mark.parametrize(
    "base_url",
    [
        "file:///etc/passwd",
        "ftp://example.com/vector",
        "gopher://example.com:70/_x",
        "javascript:alert(1)",
        "example.com/v1/lcp",  # no scheme at all
    ],
)
def test_refuses_non_http_schemes(base_url: str) -> None:
    with pytest.raises(runner.TargetRefused):
        runner.resolve_target(base_url)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8099",
        "http://127.1.2.3/",
        "http://localhost:8099",
        "http://[::1]:8099",
        "https://0.0.0.0/",
    ],
)
def test_refuses_loopback_and_unspecified_targets(base_url: str) -> None:
    with pytest.raises(runner.TargetRefused):
        runner.resolve_target(base_url)


def test_refuses_cloud_metadata_address() -> None:
    with pytest.raises(runner.TargetRefused) as excinfo:
        runner.resolve_target("http://169.254.169.254/latest/meta-data/")
    assert "link-local" in str(excinfo.value)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://10.0.0.5/",
        "http://172.16.5.4:8443/",
        "http://192.168.1.10/",
        "http://[fd00::1]/",
    ],
)
def test_refuses_private_targets(base_url: str) -> None:
    with pytest.raises(runner.TargetRefused):
        runner.resolve_target(base_url)


def test_refuses_credentials_in_target_url() -> None:
    with pytest.raises(runner.TargetRefused):
        runner.resolve_target("https://user:pass@example.com/")


def test_address_policy_classifies_link_local_v6_and_reserved() -> None:
    assert runner.blocked_address_reason("fe80::1") == "link-local"
    assert runner.blocked_address_reason("169.254.169.254") == "link-local"
    assert runner.blocked_address_reason("::ffff:127.0.0.1") == "loopback"
    assert runner.blocked_address_reason("240.0.0.1") == "reserved"
    assert runner.blocked_address_reason("93.184.216.34") is None


def test_allow_private_permits_loopback_and_pins_it() -> None:
    target = runner.resolve_target("http://127.0.0.1:8099/v1", allow_private=True)
    assert target.pinned_ip == "127.0.0.1"
    assert target.port == 8099
    assert target.base_path == "/v1"


def test_dns_answer_is_rechecked_after_resolution(monkeypatch) -> None:
    """A public-looking name that resolves privately is still refused."""
    monkeypatch.setattr(
        runner.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
        ],
    )
    with pytest.raises(runner.TargetRefused) as excinfo:
        runner.resolve_target("http://rebind.attacker.example/")
    assert "loopback" in str(excinfo.value)


def test_request_connects_to_the_pinned_address(monkeypatch) -> None:
    """DNS is resolved once and the socket goes to that exact address."""
    resolve_calls: list[tuple] = []

    def fake_getaddrinfo(host, port, *args, **kwargs):
        resolve_calls.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(runner.socket, "getaddrinfo", fake_getaddrinfo)

    client_sock, server_sock = socket.socketpair()
    server_sock.settimeout(5)
    connected: list[tuple] = []
    seen_request: dict[str, bytes] = {}

    def fake_create_connection(address, timeout=None, source_address=None):
        connected.append(address)
        return client_sock

    monkeypatch.setattr(runner.socket, "create_connection", fake_create_connection)

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

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    target = runner.resolve_target("http://rebind.example/")
    status, headers, body = runner.http_request(
        target, "GET", "/v1/lcp/capabilities", timeout=5
    )
    thread.join(timeout=5)
    client_sock.close()

    assert (status, headers["x-test"], body) == (200, "1", b"ok")
    assert connected == [("93.184.216.34", 80)]
    assert len(resolve_calls) == 1
    # The Host header and certificate check still use the advertised name.
    assert b"Host: rebind.example" in seen_request["raw"]


# --- report shape and digest -------------------------------------------------


def _sample_report() -> dict:
    return runner.build_report(
        "https://receiver.example",
        "bearer",
        ["1.2.0"],
        [runner.Check("transport.capabilities", "pass", "ok")],
    )


def test_digest_is_sha256_over_the_canonical_body() -> None:
    report = _sample_report()
    expected = hashlib.sha256(runner.canonical_report_bytes(report)).hexdigest()
    assert report["digest"] == expected
    assert len(report["digest"]) == 64
    assert "digest" not in json.loads(runner.canonical_report_bytes(report))


def test_digest_changes_when_a_check_changes() -> None:
    report = _sample_report()
    tampered = copy.deepcopy(report)
    tampered["checks"][0]["detail"] = "different"
    assert runner.report_digest(tampered) != report["digest"]


def test_digest_is_key_order_independent() -> None:
    report = _sample_report()
    reordered = dict(reversed(list(report.items())))
    assert runner.report_digest(reordered) == report["digest"]


def test_report_shape() -> None:
    report = _sample_report()
    assert set(report) == {
        "report_id",
        "generated_at",
        "target",
        "lcp_versions",
        "checks",
        "summary",
        "digest",
    }
    assert report["summary"] == {
        "total": 1,
        "passed": 1,
        "failed": 0,
        "skipped": 0,
        "status": "pass",
    }
    assert report["target"] == {
        "base_url": "https://receiver.example",
        "auth_method": "bearer",
    }


def test_parse_suite_output() -> None:
    stdout = (
        "\n  L1 - l1-envelope.json\n"
        "  PASS  L1-001 valid_lead_envelope\n"
        "  FAIL  L1-002 broken_vector\n"
        "        -> expected failure but message validated\n"
        "        -> second detail\n"
        "\n  L1: 1 passed, 1 failed\n"
    )
    checks = runner.parse_suite_output(stdout)
    assert [(c.id, c.status) for c in checks] == [
        ("vector.L1-001", "pass"),
        ("vector.L1-002", "fail"),
    ]
    assert checks[1].detail == ("expected failure but message validated; second detail")


# --- credential handling -----------------------------------------------------


def test_redact_strips_the_secret() -> None:
    assert SECRET not in runner.redact(f"target echoed {SECRET}!", SECRET)
    assert runner.REDACTED in runner.redact(f"echo {SECRET}", SECRET)


def test_api_key_absent_from_report_and_logs(stub_target, caplog) -> None:
    runner.configure(allow_private=True)
    try:
        with caplog.at_level(logging.INFO, logger=runner.SERVICE_NAME):
            report = runner.run_conformance(
                stub_target,
                "bearer",
                SECRET,
                allow_private=True,
                run_schema_checks=False,
            )
    finally:
        runner.configure()

    assert SECRET not in json.dumps(report)
    assert SECRET not in caplog.text
    assert report["summary"]["status"] == "pass"
    assert report["lcp_versions"] == ["1.1.0", "1.2.0"]
    assert report["digest"] == runner.report_digest(report)


def test_report_index_keeps_only_digest_and_summary(stub_target) -> None:
    report = runner.run_conformance(
        stub_target, "bearer", SECRET, allow_private=True, run_schema_checks=False
    )
    stored = runner._REPORT_INDEX[report["report_id"]]
    assert set(stored) == {"digest", "summary"}
    assert stored["digest"] == report["digest"]
    assert SECRET not in json.dumps(stored)
    assert stub_target not in json.dumps(stored)


def test_echoed_secret_is_redacted_from_details(stub_target, monkeypatch) -> None:
    def echoing_transport(target, auth_method, api_key, sender_id, timeout):
        return [runner.Check("transport.capabilities", "fail", f"echo {api_key}")], []

    monkeypatch.setattr(runner, "transport_checks", echoing_transport)
    report = runner.run_conformance(
        stub_target, "bearer", SECRET, allow_private=True, run_schema_checks=False
    )
    assert SECRET not in json.dumps(report)
    assert report["checks"][0]["detail"] == f"echo {runner.REDACTED}"


def test_hmac_probe_is_signed_and_accepted(stub_target) -> None:
    report = runner.run_conformance(
        stub_target,
        "hmac",
        SECRET,
        sender_id="sender-1",
        allow_private=True,
        run_schema_checks=False,
    )
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["transport.auth.credentialed_read"]["status"] == "pass"
    assert checks["transport.capabilities.auth_methods"]["status"] == "pass"


def test_hmac_probe_without_sender_id_is_skipped(stub_target) -> None:
    report = runner.run_conformance(
        stub_target, "hmac", SECRET, allow_private=True, run_schema_checks=False
    )
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["transport.auth.credentialed_read"]["status"] == "skipped"


@pytest.mark.parametrize(
    "auth_method",
    ["basic", "digest", ""],
)
def test_unknown_auth_method_is_refused(auth_method: str) -> None:
    with pytest.raises(runner.TargetRefused):
        runner.run_conformance(
            "https://receiver.example", auth_method, SECRET, run_schema_checks=False
        )


def test_credentialed_method_requires_a_key() -> None:
    with pytest.raises(runner.TargetRefused):
        runner.run_conformance(
            "https://receiver.example", "bearer", None, run_schema_checks=False
        )


# --- local self-test against the stub target ---------------------------------


def test_self_test_report_against_stub_target(stub_target) -> None:
    report = runner.run_conformance(
        stub_target, "bearer", SECRET, allow_private=True, run_schema_checks=False
    )
    transport = [c for c in report["checks"] if c["id"].startswith("transport.")]
    assert transport and all(check["status"] == "pass" for check in transport)
    skipped = [c for c in report["checks"] if c["status"] == "skipped"]
    assert [c["id"] for c in skipped] == ["vector.suite"]
    assert report["summary"]["failed"] == 0
    assert report["target"]["base_url"] == stub_target
    assert report["target"]["auth_method"] == "bearer"


# --- WSGI surface ------------------------------------------------------------


def test_healthz() -> None:
    status, payload = _call_wsgi("GET", "/healthz")
    assert status.startswith("200")
    assert payload == {"status": "ok", "service": runner.SERVICE_NAME}


def test_healthz_rejects_post() -> None:
    status, payload = _call_wsgi("POST", "/healthz")
    assert status.startswith("405")
    assert payload["errors"][0]["code"] == "RUNNER-METHOD-NOT-ALLOWED"


def test_unknown_route() -> None:
    status, payload = _call_wsgi("GET", "/nope")
    assert status.startswith("404")
    assert payload["errors"][0]["code"] == "RUNNER-NOT-FOUND"


def test_run_requires_post() -> None:
    status, payload = _call_wsgi("GET", "/run")
    assert status.startswith("405")


def test_run_rejects_invalid_json() -> None:
    status, payload = _call_wsgi("POST", "/run", raw=b"{not json")
    assert status.startswith("400")
    assert payload["errors"][0]["code"] == "RUNNER-BAD-JSON"


def test_run_rejects_oversized_body() -> None:
    status, payload = _call_wsgi(
        "POST", "/run", raw=b"{}", content_length=str(runner.MAX_REQUEST_BYTES + 1)
    )
    assert status.startswith("413")
    assert payload["errors"][0]["code"] == "RUNNER-BODY-TOO-LARGE"


def test_run_refuses_ssrf_target_through_http() -> None:
    status, payload = _call_wsgi(
        "POST", "/run", {"base_url": "http://169.254.169.254/", "auth_method": "none"}
    )
    assert status.startswith("400")
    assert payload["errors"][0]["code"] == "RUNNER-TARGET-REFUSED"


def test_run_rejects_non_string_api_key() -> None:
    status, payload = _call_wsgi(
        "POST",
        "/run",
        {"base_url": "https://receiver.example", "auth_method": "bearer", "api_key": 7},
    )
    assert status.startswith("400")
    assert payload["errors"][0]["code"] == "RUNNER-BAD-REQUEST"


def test_run_returns_report_for_stub_target(stub_target, monkeypatch) -> None:
    monkeypatch.setattr(runner, "_ALLOW_PRIVATE", True)
    monkeypatch.setattr(runner, "_RUN_SCHEMA_CHECKS", False)
    status, payload = _call_wsgi(
        "POST",
        "/run",
        {"base_url": stub_target, "auth_method": "bearer", "api_key": SECRET},
    )
    assert status.startswith("200")
    assert payload["digest"] == runner.report_digest(payload)
    assert payload["summary"]["status"] == "pass"
    assert SECRET not in json.dumps(payload)
