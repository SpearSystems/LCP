#!/usr/bin/env python3
"""LCP conformance runner service (D11) — self-hostable, stdlib only.

Endpoints
  POST /run      {"base_url", "auth_method", "api_key"?, "sender_id"?}
                 -> signed JSON conformance report
  GET  /healthz  -> {"status": "ok", "service": "lcp-conformance-runner"}

The report is built from two families of checks:

- ``transport.*`` — probes against the *target's* live surface: the
  ``GET /v1/lcp/capabilities`` advertisement (§8), anonymous-request
  rejection, and a credentialed read probe. These are the vectors the
  repository suite cannot answer, because they describe a running peer
  rather than a document.
- ``vector.*`` — the repository's own vector suite
  (``test-vectors/conformance.py``), executed in a subprocess with an
  interpreter that has ``jsonschema``. The service itself imports the
  standard library only; running the suite as a child process keeps the
  dependency boundary honest.

Security model (see docs/OPERATIONS.md, "Conformance runner"):

- only ``http``/``https`` targets; credentials in the URL are refused;
- loopback, link-local (including 169.254.169.254), private, unspecified,
  multicast and reserved addresses are refused unless ``--allow-private``
  is passed (the local self-test uses it);
- the hostname is resolved exactly once and every resolved address is
  policy-checked; the connection is then pinned to that address, so a
  second (rebind) resolution can never redirect the request;
- responses are capped by size and by wall clock; requests are not followed
  through redirects;
- the API key is used once, never echoed into the report or the log, and
  every check detail is passed through redaction before publication;
- the in-memory report index keeps only ``{digest, summary}`` — no target
  URL and no credential.

The runner makes no certification or trademark claim (D11). See
docs/DECISIONS-v1.2.md.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import urlsplit
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

SERVICE_NAME = "lcp-conformance-runner"

REPO_ROOT = Path(__file__).resolve().parents[1]
VECTOR_SUITE = REPO_ROOT / "test-vectors" / "conformance.py"

DEFAULT_PORT = 8099
DEFAULT_TIMEOUT_SECONDS = 10.0
SUITE_TIMEOUT_SECONDS = 120.0
MAX_RESPONSE_BYTES = 1 << 20
MAX_REQUEST_BYTES = 8 << 10
MAX_REPORT_INDEX = 256
MAX_DETAIL_CHARS = 400

CAPABILITIES_PATH = "/v1/lcp/capabilities"
OFFERS_PATH = "/v1/lcp/offers"
HISTORY_PATH = "/v1/lcp/history/requests"

AUTH_METHODS = ("none", "bearer", "hmac")
CONFORMANCE_LEVELS = ("L1", "L2", "L3")
REDACTED = "[redacted]"

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
VECTOR_LINE_RE = re.compile(r"^  (PASS|FAIL)  (\S+) (.*)$")
VECTOR_DETAIL_RE = re.compile(r"^        -> (.*)$")

log = logging.getLogger(SERVICE_NAME)


class TargetRefused(ValueError):
    """The target URL fails the runner's SSRF policy (a client error)."""


class TransportError(RuntimeError):
    """The target was unreachable or broke a runner limit."""


# --- Target resolution (SSRF policy + DNS pinning) ---------------------------


@dataclass(frozen=True)
class Target:
    """A validated target whose connection address is pinned."""

    base_url: str
    scheme: str
    host: str
    port: int
    base_path: str
    pinned_ip: str


def blocked_address_reason(address: str) -> str | None:
    """Return why ``address`` must not be contacted, or None when public."""
    try:
        addr = ipaddress.ip_address(address)
    except ValueError:
        return "not an IP address"
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    if addr.is_loopback:
        return "loopback"
    if addr.is_link_local:
        return "link-local"
    if addr.is_unspecified:
        return "unspecified"
    if addr.is_multicast:
        return "multicast"
    if addr.is_reserved:
        return "reserved"
    if addr.is_private:
        return "private"
    return None


def _address_rank(address: str) -> int:
    try:
        return 0 if ipaddress.ip_address(address).version == 4 else 1
    except ValueError:
        return 2


def resolve_once(host: str, port: int) -> list[str]:
    """Resolve ``host`` exactly once; callers must connect only to these."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        address = sockaddr[0]
        if address not in addresses:
            addresses.append(address)
    addresses.sort(key=_address_rank)
    return addresses


def resolve_target(base_url: str, *, allow_private: bool = False) -> Target:
    """Validate a target URL and pin it to one resolved address."""
    if not isinstance(base_url, str) or not base_url.strip():
        raise TargetRefused("base_url must be a non-empty string")
    parts = urlsplit(base_url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise TargetRefused(
            f"scheme {scheme or '(none)'!r} is not allowed; use http or https"
        )
    if parts.username or parts.password:
        raise TargetRefused("credentials embedded in the target URL are refused")
    host = parts.hostname
    if not host:
        raise TargetRefused("target URL has no host")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise TargetRefused(f"target URL has an invalid port: {exc}") from None
    if not 0 < port < 65536:
        raise TargetRefused(f"target port {port} is out of range")

    try:
        addresses = resolve_once(host, port)
    except OSError as exc:
        raise TargetRefused(f"could not resolve {host}: {exc}") from None
    if not addresses:
        raise TargetRefused(f"{host} did not resolve to any address")

    if not allow_private:
        for address in addresses:
            reason = blocked_address_reason(address)
            if reason:
                raise TargetRefused(
                    f"target address {address} is {reason}-range; refusing to "
                    "connect (pass --allow-private to override)"
                )

    return Target(
        base_url=base_url.strip(),
        scheme=scheme,
        host=host,
        port=port,
        base_path=parts.path.rstrip("/"),
        pinned_ip=addresses[0],
    )


# --- Pinned HTTP client ------------------------------------------------------


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, target: Target, timeout: float) -> None:
        super().__init__(target.host, target.port, timeout=timeout)
        self._pinned_ip = target.pinned_ip

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, target: Target, timeout: float) -> None:
        super().__init__(
            target.host,
            target.port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._pinned_ip = target.pinned_ip

    def connect(self) -> None:
        sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
            sock = self.sock
        # SNI and certificate verification still use the advertised hostname,
        # never the pinned address.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _read_capped(
    response: http.client.HTTPResponse, max_bytes: int, deadline: float
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        if time.monotonic() > deadline:
            raise TransportError("response time cap exceeded")
        chunk = response.read(min(65536, max_bytes + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        chunks.append(chunk)
        if total > max_bytes:
            raise TransportError(f"response exceeded the {max_bytes}-byte cap")
    return b"".join(chunks)


def http_request(
    target: Target,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> tuple[int, dict[str, str], bytes]:
    """Issue one request against the pinned address, never following redirects."""
    deadline = time.monotonic() + timeout
    if target.scheme == "https":
        conn: http.client.HTTPConnection = _PinnedHTTPSConnection(target, timeout)
    else:
        conn = _PinnedHTTPConnection(target, timeout)
    try:
        conn.request(
            method,
            target.base_path + path,
            body=body or None,
            headers=dict(headers or {}),
        )
        response = conn.getresponse()
        payload = _read_capped(response, max_bytes, deadline)
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        return response.status, response_headers, payload
    except (OSError, http.client.HTTPException) as exc:
        raise TransportError(f"{type(exc).__name__}: {exc}") from None
    finally:
        conn.close()


# --- Credentials -------------------------------------------------------------


def request_headers(
    auth_method: str,
    api_key: str | None,
    sender_id: str | None,
    *,
    body: bytes = b"",
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Build probe headers. The key travels in a header, never in a URL."""
    headers = {"Accept": "application/json", "User-Agent": SERVICE_NAME}
    if sender_id:
        headers["X-LCP-Sender-Id"] = sender_id
    if not api_key:
        return headers
    if auth_method == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    elif auth_method == "hmac":
        stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        canonical = (stamp + "\n" + (idempotency_key or "") + "\n").encode(
            "utf-8"
        ) + body
        headers["X-LCP-Timestamp"] = stamp
        if idempotency_key:
            headers["X-LCP-Idempotency-Key"] = idempotency_key
        headers["X-LCP-Signature"] = hmac.new(
            api_key.encode("utf-8"), canonical, hashlib.sha256
        ).hexdigest()
    return headers


def redact(text: str, secret: str | None) -> str:
    """Strip the credential from any text that could reach a report or log."""
    text = text[:MAX_DETAIL_CHARS]
    if secret:
        text = text.replace(secret, REDACTED)
    return text


# --- Checks ------------------------------------------------------------------


@dataclass
class Check:
    id: str
    status: str  # "pass" | "fail" | "skipped"
    detail: str = ""


def _json_object(payload: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransportError(f"response is not JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise TransportError("response JSON is not an object")
    return parsed


def transport_checks(
    target: Target,
    auth_method: str,
    api_key: str | None,
    sender_id: str | None,
    timeout: float,
) -> tuple[list[Check], list[str]]:
    """Probe the target's advertised capabilities and auth enforcement."""
    checks: list[Check] = []
    lcp_versions: list[str] = []

    try:
        status, _, payload = http_request(
            target, "GET", CAPABILITIES_PATH, timeout=timeout
        )
    except TransportError as exc:
        checks.append(Check("transport.capabilities", "fail", str(exc)))
        return checks, lcp_versions

    if status != 200:
        checks.append(
            Check(
                "transport.capabilities",
                "fail",
                f"GET {CAPABILITIES_PATH} returned {status}",
            )
        )
        return checks, lcp_versions

    try:
        capabilities = _json_object(payload)
    except TransportError as exc:
        checks.append(Check("transport.capabilities", "fail", str(exc)))
        return checks, lcp_versions

    checks.append(
        Check(
            "transport.capabilities",
            "pass",
            f"GET {CAPABILITIES_PATH} returned 200 with {len(capabilities)} keys",
        )
    )

    advertised_versions = capabilities.get("lcp_versions")
    if (
        isinstance(advertised_versions, list)
        and advertised_versions
        and all(
            isinstance(version, str) and SEMVER_RE.match(version)
            for version in advertised_versions
        )
    ):
        lcp_versions = list(advertised_versions)
        checks.append(
            Check(
                "transport.capabilities.lcp_versions",
                "pass",
                ", ".join(lcp_versions),
            )
        )
    else:
        checks.append(
            Check(
                "transport.capabilities.lcp_versions",
                "fail",
                "lcp_versions must be a non-empty list of major.minor.patch "
                f"strings, got {advertised_versions!r}",
            )
        )

    level = capabilities.get("conformance_level")
    checks.append(
        Check(
            "transport.capabilities.conformance_level",
            "pass" if level in CONFORMANCE_LEVELS else "fail",
            f"conformance_level={level!r}",
        )
    )

    advertised_auth = capabilities.get("auth_methods")
    if auth_method == "none":
        checks.append(
            Check(
                "transport.capabilities.auth_methods",
                "skipped",
                "no auth_method requested",
            )
        )
    elif isinstance(advertised_auth, list) and auth_method in advertised_auth:
        checks.append(
            Check(
                "transport.capabilities.auth_methods",
                "pass",
                f"advertises {auth_method}",
            )
        )
    else:
        checks.append(
            Check(
                "transport.capabilities.auth_methods",
                "fail",
                f"requested {auth_method}, advertised {advertised_auth!r}",
            )
        )

    try:
        status, _, _ = http_request(target, "GET", HISTORY_PATH, timeout=timeout)
    except TransportError as exc:
        checks.append(Check("transport.auth.anonymous_rejected", "fail", str(exc)))
    else:
        rejected = status in (401, 403)
        checks.append(
            Check(
                "transport.auth.anonymous_rejected",
                "pass" if rejected else "fail",
                f"GET {HISTORY_PATH} without credentials returned {status}"
                + ("" if rejected else "; expected 401 or 403"),
            )
        )

    if auth_method == "none":
        checks.append(
            Check(
                "transport.auth.credentialed_read",
                "skipped",
                "no credential supplied",
            )
        )
    elif auth_method == "hmac" and not sender_id:
        checks.append(
            Check(
                "transport.auth.credentialed_read",
                "skipped",
                "the hmac probe needs sender_id to identify the caller",
            )
        )
    else:
        headers = request_headers(auth_method, api_key, sender_id)
        try:
            status, _, _ = http_request(
                target, "GET", OFFERS_PATH, headers=headers, timeout=timeout
            )
        except TransportError as exc:
            checks.append(Check("transport.auth.credentialed_read", "fail", str(exc)))
        else:
            accepted = status not in (401, 403)
            checks.append(
                Check(
                    "transport.auth.credentialed_read",
                    "pass" if accepted else "fail",
                    f"GET {OFFERS_PATH} with {auth_method} returned {status}"
                    + ("" if accepted else "; the credential was not accepted"),
                )
            )

    return checks, lcp_versions


# --- Repository vector suite -------------------------------------------------


def suite_interpreter() -> str:
    """Interpreter used for the vector suite (it needs jsonschema)."""
    override = os.environ.get("LCP_CONFORMANCE_PYTHON")
    if override:
        return override
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def parse_suite_output(stdout: str) -> list[Check]:
    """Turn `conformance.py --verbose` output into per-vector checks."""
    checks: list[Check] = []
    for line in stdout.splitlines():
        match = VECTOR_LINE_RE.match(line)
        if match:
            outcome, vector_id, _name = match.groups()
            checks.append(
                Check(
                    f"vector.{vector_id}",
                    "pass" if outcome == "PASS" else "fail",
                    "",
                )
            )
            continue
        detail = VECTOR_DETAIL_RE.match(line)
        if detail and checks and checks[-1].status == "fail":
            existing = checks[-1].detail
            joined = f"{existing}; {detail.group(1)}" if existing else detail.group(1)
            checks[-1].detail = joined
    return checks


def schema_checks(timeout: float = SUITE_TIMEOUT_SECONDS) -> list[Check]:
    """Execute the repository's own vector suite against the local schemas."""
    if not VECTOR_SUITE.exists():
        return [Check("vector.suite", "fail", f"{VECTOR_SUITE} is missing")]
    try:
        proc = subprocess.run(
            [suite_interpreter(), str(VECTOR_SUITE), "--tier", "all", "--verbose"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [
            Check(
                "vector.suite",
                "fail",
                f"could not run {VECTOR_SUITE.name}: {type(exc).__name__}",
            )
        ]

    checks = parse_suite_output(proc.stdout)
    if checks:
        return checks

    stderr = (proc.stderr or "").strip()
    if (
        "No module named 'jsonschema'" in stderr
        or "No module named 'referencing'" in stderr
    ):
        return [
            Check(
                "vector.suite",
                "skipped",
                "the vector suite needs jsonschema + referencing; point "
                "LCP_CONFORMANCE_PYTHON at an interpreter that has them",
            )
        ]
    return [
        Check(
            "vector.suite",
            "fail",
            f"vector suite produced no results (exit {proc.returncode}): "
            f"{stderr[:MAX_DETAIL_CHARS]}",
        )
    ]


# --- Report ------------------------------------------------------------------


def canonical_report_bytes(report: dict[str, Any]) -> bytes:
    """Canonical body of a report: everything except ``digest``."""
    body = {key: value for key, value in report.items() if key != "digest"}
    return json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def report_digest(report: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_report_bytes(report)).hexdigest()


def summarize(checks: Iterable[Check]) -> dict[str, Any]:
    checks = list(checks)
    passed = sum(1 for check in checks if check.status == "pass")
    failed = sum(1 for check in checks if check.status == "fail")
    skipped = sum(1 for check in checks if check.status == "skipped")
    return {
        "total": len(checks),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "status": "pass" if failed == 0 else "fail",
    }


def build_report(
    target_url: str,
    auth_method: str,
    lcp_versions: list[str],
    checks: list[Check],
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "report_id": str(uuid.uuid4()),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target": {"base_url": target_url, "auth_method": auth_method},
        "lcp_versions": list(lcp_versions),
        "checks": [
            {"id": check.id, "status": check.status, "detail": check.detail}
            for check in checks
        ],
        "summary": summarize(checks),
    }
    report["digest"] = report_digest(report)
    return report


_REPORT_INDEX: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def remember(report: dict[str, Any]) -> None:
    """Keep only the digest and summary — never the target URL or a credential."""
    _REPORT_INDEX[report["report_id"]] = {
        "digest": report["digest"],
        "summary": dict(report["summary"]),
    }
    while len(_REPORT_INDEX) > MAX_REPORT_INDEX:
        _REPORT_INDEX.popitem(last=False)


def run_conformance(
    base_url: str,
    auth_method: str = "none",
    api_key: str | None = None,
    *,
    sender_id: str | None = None,
    allow_private: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    run_schema_checks: bool = True,
) -> dict[str, Any]:
    """Run the full conformance report for one target."""
    auth_method = (auth_method or "none").lower()
    if auth_method not in AUTH_METHODS:
        raise TargetRefused(
            f"auth_method {auth_method!r} is not one of {', '.join(AUTH_METHODS)}"
        )
    if auth_method != "none" and not api_key:
        raise TargetRefused(f"auth_method {auth_method!r} requires an api_key")

    target = resolve_target(base_url, allow_private=allow_private)
    checks, lcp_versions = transport_checks(
        target, auth_method, api_key, sender_id, timeout
    )
    if run_schema_checks:
        checks.extend(schema_checks())
    else:
        checks.append(Check("vector.suite", "skipped", "schema checks disabled"))

    for check in checks:
        check.detail = redact(check.detail, api_key)

    report = build_report(target.base_url, auth_method, lcp_versions, checks)
    remember(report)
    log.info(
        "conformance report id=%s host=%s summary=%s",
        report["report_id"],
        target.host,
        report["summary"]["status"],
    )
    return report


# --- Local stub target (self-test and unit tests) ----------------------------

SELF_TEST_KEY = "self-test-key-not-a-secret"

STUB_CAPABILITIES: dict[str, Any] = {
    "lcp_versions": ["1.1.0", "1.2.0"],
    "core_schema_version": "1.2.0",
    "exclusivity_default": "shared",
    "features": {"processed_ack": "1.2.0", "multi_currency": "1.2.0"},
    "message_types": ["lead", "call", "ping", "bid", "post", "ack", "event"],
    "verticals": [{"id": "personal_loans", "schema_version": "1.0.0"}],
    "countries": [],
    "auth_methods": ["bearer", "hmac"],
    "events": ["DELIVERED", "ACCEPTED", "REJECTED"],
    "conformance_level": "L2",
}


class StubTarget:
    """Minimal WSGI LCP peer: capabilities, auth enforcement, read probe."""

    def __init__(self, secret: str = SELF_TEST_KEY) -> None:
        self.secret = secret
        self.seen_headers: list[dict[str, str]] = []

    def __call__(self, environ: dict[str, Any], start_response: Callable):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = (environ.get("PATH_INFO") or "/").rstrip("/") or "/"
        headers = {
            key[5:].replace("_", "-").lower(): value
            for key, value in environ.items()
            if key.startswith("HTTP_")
        }
        self.seen_headers.append(headers)

        if method == "GET" and path == CAPABILITIES_PATH:
            return _respond(start_response, 200, STUB_CAPABILITIES)
        if method == "GET" and path == HISTORY_PATH:
            return _respond(
                start_response,
                401,
                {
                    "errors": [
                        {"code": "LCP-002", "message": "Sender identity is required"}
                    ]
                },
            )
        if method == "GET" and path == OFFERS_PATH:
            if self._authenticated(headers):
                return _respond(start_response, 200, {"offers": []})
            return _respond(
                start_response,
                401,
                {"errors": [{"code": "LCP-002", "message": "Authentication required"}]},
            )
        return _respond(
            start_response,
            404,
            {"errors": [{"code": "LCP-001", "message": "Endpoint not found"}]},
        )

    def _authenticated(self, headers: dict[str, str]) -> bool:
        authorization = headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            presented = authorization[7:].strip()
            return hmac.compare_digest(presented, self.secret)
        signature = headers.get("x-lcp-signature")
        timestamp = headers.get("x-lcp-timestamp")
        if not signature or not timestamp or not headers.get("x-lcp-sender-id"):
            return False
        canonical = (
            timestamp + "\n" + headers.get("x-lcp-idempotency-key", "") + "\n"
        ).encode("utf-8")
        expected = hmac.new(
            self.secret.encode("utf-8"), canonical, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature.lower())


def _respond(start_response: Callable, status: int, payload: dict[str, Any]):
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    start_response(
        f"{status} {'OK' if status < 400 else 'ERROR'}",
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(encoded))),
        ],
    )
    return [encoded]


class _QuietStubHandler(WSGIRequestHandler):
    """Request handler that keeps the stub's access log out of test output."""

    def log_message(self, *args: Any, **kwargs: Any) -> None:
        return


def start_stub_target(
    secret: str = SELF_TEST_KEY,
) -> tuple[Any, threading.Thread, str]:
    """Serve ``StubTarget`` on 127.0.0.1 and return (server, thread, base_url)."""
    server = make_server(
        "127.0.0.1", 0, StubTarget(secret), handler_class=_QuietStubHandler
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, thread, f"http://{host}:{port}"


def stop_stub_target(server: Any, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def self_test(timeout: float = DEFAULT_TIMEOUT_SECONDS) -> int:
    """End-to-end proof that the runner works. Returns a process exit code."""
    server, thread, base_url = start_stub_target()
    try:
        report = run_conformance(
            base_url, "bearer", SELF_TEST_KEY, allow_private=True, timeout=timeout
        )
    finally:
        stop_stub_target(server, thread)

    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)

    transport_failures = [
        check
        for check in report["checks"]
        if check["status"] == "fail" and check["id"].startswith("transport.")
    ]
    if not report.get("digest") or transport_failures:
        print("SELF-TEST FAILED", file=sys.stderr)
        return 1
    print(
        f"SELF-TEST OK: digest {report['digest'][:16]}… "
        f"transport checks pass; vector suite {report['summary']['passed']} "
        f"passed, {report['summary']['failed']} failed, "
        f"{report['summary']['skipped']} skipped",
        file=sys.stderr,
    )
    return 0


# --- WSGI service ------------------------------------------------------------

_ALLOW_PRIVATE = False
_TIMEOUT = DEFAULT_TIMEOUT_SECONDS
_RUN_SCHEMA_CHECKS = True


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    """One thread per request: a run takes seconds and must not block /healthz."""

    daemon_threads = True


def configure(
    *,
    allow_private: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    run_schema_checks: bool = True,
) -> None:
    global _ALLOW_PRIVATE, _TIMEOUT, _RUN_SCHEMA_CHECKS
    _ALLOW_PRIVATE = allow_private
    _TIMEOUT = timeout
    _RUN_SCHEMA_CHECKS = run_schema_checks


def _json_response(start_response: Callable, status: int, payload: dict[str, Any]):
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    start_response(
        f"{status} {http.client.responses.get(status, 'ERROR')}",
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(encoded))),
            ("Cache-Control", "no-store"),
        ],
    )
    return [encoded]


def _error(start_response: Callable, status: int, code: str, message: str):
    return _json_response(
        start_response, status, {"errors": [{"code": code, "message": message}]}
    )


def application(environ: dict[str, Any], start_response: Callable):
    """WSGI entry point: POST /run and GET /healthz."""
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = (environ.get("PATH_INFO") or "/").rstrip("/") or "/"

    if path == "/healthz":
        if method != "GET":
            return _error(
                start_response, 405, "RUNNER-METHOD-NOT-ALLOWED", "GET required"
            )
        return _json_response(
            start_response, 200, {"status": "ok", "service": SERVICE_NAME}
        )

    if path != "/run":
        return _error(start_response, 404, "RUNNER-NOT-FOUND", "unknown route")

    if method != "POST":
        return _error(start_response, 405, "RUNNER-METHOD-NOT-ALLOWED", "POST required")

    raw_length = environ.get("CONTENT_LENGTH") or "0"
    try:
        length = int(raw_length)
    except ValueError:
        return _error(
            start_response, 400, "RUNNER-BAD-REQUEST", "invalid Content-Length"
        )
    if length < 0 or length > MAX_REQUEST_BYTES:
        return _error(
            start_response,
            413,
            "RUNNER-BODY-TOO-LARGE",
            f"request body must be at most {MAX_REQUEST_BYTES} bytes",
        )
    body = environ["wsgi.input"].read(length) if length else b""
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error(
            start_response, 400, "RUNNER-BAD-JSON", "request body must be JSON"
        )
    if not isinstance(payload, dict):
        return _error(
            start_response, 400, "RUNNER-BAD-JSON", "request body must be an object"
        )

    api_key = payload.get("api_key")
    sender_id = payload.get("sender_id")
    auth_method = payload.get("auth_method", "none")
    for name, value in (("api_key", api_key), ("sender_id", sender_id)):
        if value is not None and not isinstance(value, str):
            return _error(
                start_response, 400, "RUNNER-BAD-REQUEST", f"{name} must be a string"
            )
    if not isinstance(auth_method, str):
        return _error(
            start_response, 400, "RUNNER-BAD-REQUEST", "auth_method must be a string"
        )

    try:
        report = run_conformance(
            payload.get("base_url"),
            auth_method,
            api_key,
            sender_id=sender_id,
            allow_private=_ALLOW_PRIVATE,
            timeout=_TIMEOUT,
            run_schema_checks=_RUN_SCHEMA_CHECKS,
        )
    except TargetRefused as exc:
        return _error(start_response, 400, "RUNNER-TARGET-REFUSED", str(exc))
    return _json_response(start_response, 200, report)


app = application


# --- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "LCP conformance runner service. The report it returns is not a "
            "certification (D11)."
        )
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="bind port")
    parser.add_argument(
        "--allow-private",
        action="store_true",
        help="allow loopback/link-local/private targets (local self-test only)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="per-request timeout in seconds",
    )
    parser.add_argument(
        "--no-schema-checks",
        action="store_true",
        help="skip the repository vector suite (transport probes only)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run against a local stub target, print the report, and exit",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test(timeout=args.timeout)

    configure(
        allow_private=args.allow_private,
        timeout=args.timeout,
        run_schema_checks=not args.no_schema_checks,
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    with make_server(
        args.host, args.port, application, server_class=_ThreadingWSGIServer
    ) as httpd:
        print(
            f"{SERVICE_NAME} listening on http://{args.host}:{args.port}",
            file=sys.stderr,
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
