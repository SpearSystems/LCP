"""`lcp-doctor` — one-shot LCP endpoint check with plain-English fixes.

The doctor is deliberately dependency-free (standard library only) so an
integrator can run it before installing anything else. It performs two
wire checks against the configured endpoint:

1. ``GET /v1/lcp/capabilities`` — reachability, TLS, advertised
   ``lcp_versions``, ``features`` and ``auth_methods``.
2. ``POST /v1/lcp/leads`` — a synthetic ``test: true`` lead envelope with
   a fresh idempotency key, signed with either the API key or the HMAC
   secret.

Every failure is translated through :data:`ERROR_FIXES`, the same data
that powers ``lcp-doctor --explain CODE``. The full reference is
``docs/ERROR-GUIDE.md``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import ssl
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

DOCS_URL = "https://github.com/SpearSystems/LCP/blob/main/docs/ERROR-GUIDE.md"

#: code -> meaning / three common causes / exact fix. First cause is the
#: "likely cause" shown in the inline failure output.
ERROR_FIXES: dict[str, dict[str, Any]] = {
    "LCP-001": {
        "name": "INVALID_FORMAT",
        "http": 400,
        "meaning": "The request is not a well-formed LCP HTTP request.",
        "causes": [
            "The body is not valid JSON.",
            "Content-Type is not application/json.",
            "The URL path or HTTP method is not the LCP route the endpoint serves.",
        ],
        "fix": (
            "POST to <endpoint>/v1/lcp/leads with Content-Type: application/json "
            "and a complete JSON envelope shaped {lcp:{version,message,payload}}. "
            "Remove duplicate or missing path segments from LCP_ENDPOINT."
        ),
    },
    "LCP-002": {
        "name": "UNKNOWN_SENDER",
        "http": 401,
        "meaning": "The receiver does not recognize or authorize this sender.",
        "causes": [
            "sender_id is not provisioned at the receiver.",
            "The API key or HMAC secret does not belong to this sender.",
            "X-LCP-Sender-Id, envelope sender_id, receiver_id, or credential scope disagrees with what the receiver expects.",
        ],
        "fix": (
            "Use the exact sender_id and credential issued by the receiver. Send "
            "Authorization: Bearer <api-key> (or the HMAC headers) plus "
            "X-LCP-Sender-Id equal to lcp.message.sender_id. Set "
            "lcp.message.receiver_id to the receiver's platform ID and confirm the "
            "credential has the lead:submit scope."
        ),
    },
    "LCP-003": {
        "name": "MISSING_REQUIRED_FIELD",
        "http": 400,
        "meaning": "A required field is missing from the message.",
        "causes": [
            "consumer lacks email, phone, first_name+last_name, or full_name.",
            "location lacks country_code, or both state_region and postal_code.",
            "attributes lacks vertical or schema_version.",
        ],
        "fix": (
            "Add the field named in errors[].field. Minimum lead: consumer.email or "
            "consumer.phone or consumer.full_name, location.country_code plus "
            "state_region or postal_code, and attributes.vertical plus "
            "attributes.schema_version."
        ),
    },
    "LCP-004": {
        "name": "INVALID_STATUS_TRANSITION",
        "http": 422,
        "meaning": "The requested lifecycle change is not allowed from the lead's current status.",
        "causes": [
            "A terminal status was moved back to an earlier status.",
            "The transition skipped a required intermediate status.",
            "The status update targeted the wrong lead_id.",
        ],
        "fix": (
            "Read the current status with GET /v1/lcp/leads/{lead_id}, then send only "
            "a transition the payload specification allows for that status. Do not "
            "change a terminal lead."
        ),
    },
    "LCP-005": {
        "name": "DUPLICATE_LEAD",
        "http": 409,
        "meaning": "This lead or idempotency key was already submitted.",
        "causes": [
            "The same idempotency_key was reused for a new message.",
            "The same lead_id was submitted twice.",
            "A retry reused the original key but the receiver treats it as a conflict.",
        ],
        "fix": (
            "For a genuinely new lead, generate a new UUID for lcp.message.id and a "
            "new idempotency_key plus X-LCP-Idempotency-Key (lcp-doctor does this "
            "every run). For a retry of the same message, reuse the original key and "
            "expect the original result."
        ),
    },
    "LCP-006": {
        "name": "UNKNOWN_MESSAGE_TYPE",
        "http": 400,
        "meaning": "message.type is not a message type this receiver accepts.",
        "causes": [
            "A typo or custom value such as 'Lead' instead of 'lead'.",
            "The receiver does not implement that message type.",
            "A v1.2 type (batch, quote, counter, subscription) was sent to a receiver that did not advertise it.",
        ],
        "fix": (
            "Set lcp.message.type to lead, call, ping, post, ack, event, or bid. Send "
            "batch, quote, counter, or subscription only when GET /v1/lcp/capabilities "
            "advertises that message type or feature."
        ),
    },
    "LCP-007": {
        "name": "SCHEMA_VERSION_UNSUPPORTED",
        "http": 422,
        "meaning": "The protocol or schema version in the message is not supported.",
        "causes": [
            "lcp.version is not a full major.minor.patch semver.",
            "The advertised major version is not 1.",
            "attributes.schema_version is not the version advertised for that vertical.",
        ],
        "fix": (
            "Set lcp.version to a version listed in capabilities.lcp_versions (full "
            "semver, e.g. 1.2.0), and set attributes.schema_version to the "
            "schema_version advertised for attributes.vertical."
        ),
    },
    "LCP-008": {
        "name": "PII_IN_PING",
        "http": 400,
        "meaning": "A ping contains a field that the non-PII ping allowlist forbids.",
        "causes": [
            "Raw consumer data (name, email, phone) was put in a ping.",
            "A vertical attribute is not marked ping_safe.",
            "An unknown field was added to the closed ping object.",
        ],
        "fix": (
            "Remove the field named in errors[].field from the ping. Pings may "
            "contain only fields listed in the ping payload schema; send contact "
            "data as phone_hash/email_hash, never raw values."
        ),
    },
    "LCP-009": {
        "name": "INVALID_PHONE",
        "http": 400,
        "meaning": "A phone number is not in E.164 format.",
        "causes": [
            "The number has spaces, dashes, or parentheses.",
            "The number starts with a local trunk 0 instead of a country code.",
            "secondary_phone has the same formatting problem.",
        ],
        "fix": (
            "Set consumer.phone (and secondary_phone) to E.164: a plus sign, country "
            "code, then 2-15 digits with no separators, e.g. +61412345678."
        ),
    },
    "LCP-010": {
        "name": "INVALID_TERRITORY",
        "http": 422,
        "meaning": "The lead's location or territory is not valid for this receiver.",
        "causes": [
            "country_code is lowercase or not ISO 3166-1 alpha-2.",
            "state_region is not valid for that country.",
            "postal_code uses the wrong national format, or both state_region and postal_code are missing.",
        ],
        "fix": (
            "Set location.country_code to an uppercase ISO 3166-1 alpha-2 code (e.g. "
            "AU). Add a valid location.state_region or location.postal_code for that "
            "country, using the receiver's supported list in capabilities.countries."
        ),
    },
    "LCP-011": {
        "name": "RATE_LIMITED",
        "http": 429,
        "meaning": "The receiver is rate-limiting this sender.",
        "causes": [
            "Too many requests per second or minute.",
            "The receiver's daily or hourly capacity is exhausted.",
            "Parallel retries amplified the request rate.",
        ],
        "fix": (
            "Stop and retry after the Retry-After header. Reduce send rate, add "
            "jittered backoff, and check capabilities.capacity for remaining "
            "daily/hourly allowance before resending."
        ),
    },
    "LCP-012": {
        "name": "SIGNATURE_INVALID",
        "http": 401,
        "meaning": "HMAC authentication failed: signature, timestamp, or idempotency header does not verify.",
        "causes": [
            "The signature was computed over different bytes than the body sent.",
            "X-LCP-Timestamp is stale, not UTC, or not whole-second YYYY-MM-DDTHH:MM:SSZ.",
            "X-LCP-Idempotency-Key is missing or differs from lcp.message.idempotency_key.",
        ],
        "fix": (
            "Sign the exact canonical input: timestamp + '\\n' + idempotency-key + "
            "'\\n' + raw request body, HMAC-SHA256 hex. Send the same timestamp in "
            "X-LCP-Timestamp, keep it within 300 seconds of the receiver clock, and "
            "send X-LCP-Idempotency-Key equal to lcp.message.idempotency_key."
        ),
    },
    "LCP-013": {
        "name": "TEST_TRAFFIC_POLICY",
        "http": 400,
        "meaning": "Test traffic and the receiver's environment disagree.",
        "causes": [
            "A production endpoint received test:true or X-LCP-Test:true.",
            "A sandbox received a message without test:true and X-LCP-Test:true.",
            "The envelope test flag and the X-LCP-Test header disagree.",
        ],
        "fix": (
            "For a sandbox, send lcp.message.test=true and X-LCP-Test: true together "
            "(lcp-doctor does this). For production, send test:false and omit "
            "X-LCP-Test. Point LCP_ENDPOINT at the matching environment."
        ),
    },
    "LCP-100": {
        "name": "VALIDATION_ERROR",
        "http": 400,
        "meaning": "The message failed schema or field validation.",
        "causes": [
            "A field has the wrong type, format, enum value, or pattern.",
            "A closed object contains an unknown field.",
            "The payload does not match the vertical's schema.",
        ],
        "fix": (
            "Fix the field named in errors[].field (and the path in "
            "errors[].details). Validate the envelope against schemas/envelope.json, "
            "the payload against schemas/lead.json, and attributes against the "
            "vertical schema before resending."
        ),
    },
    "LCP-500": {
        "name": "INTERNAL_ERROR",
        "http": 500,
        "meaning": "The receiver hit an internal error while processing the request.",
        "causes": [
            "A receiver bug or unhandled exception.",
            "A downstream dependency (database, queue) is unavailable.",
            "The request triggered a timeout in receiver processing.",
        ],
        "fix": (
            "Retry once with the same idempotency_key and body. If it repeats, send "
            "the receiver the UTC time, request ID, endpoint, and full response body; "
            "do not change a valid payload to work around a server fault."
        ),
    },
}

# The first listed cause is the one the inline verdict leads with; keep the
# key present so callers and --json consumers see the same shape.
for _entry in ERROR_FIXES.values():
    _entry["likely_cause"] = _entry["causes"][0]

# HTTP status -> code used when the response carries no parseable LCP errors[].
_STATUS_CODES = {
    400: "LCP-100",
    401: "LCP-002",
    409: "LCP-005",
    422: "LCP-100",
    429: "LCP-011",
    500: "LCP-500",
    502: "LCP-500",
    503: "LCP-500",
    504: "LCP-500",
}

_REJECTING_ACK_STATUSES = {"REJECTED", "ERROR", "FAILED", "INVALID"}


@dataclass
class Config:
    endpoint: str
    sender_id: str
    api_key: str = ""
    hmac_secret: str = ""
    receiver_id: str = ""
    publisher_id: str = ""
    timeout: float = 15.0


@dataclass
class Response:
    status: int
    body: bytes
    headers: Mapping[str, str]
    url: str


class TransportError(Exception):
    """The request never produced an HTTP response."""

    def __init__(self, url: str, reason: str):
        self.url = url
        self.reason = reason
        super().__init__(f"{url}: {reason}")


# ─── configuration ────────────────────────────────────────────────────────


def load_config(env: Mapping[str, str] | None = None) -> tuple[Config, list[str]]:
    """Read the doctor's environment contract and list what is missing."""
    env = os.environ if env is None else env
    endpoint = (env.get("LCP_ENDPOINT") or "").strip()
    sender_id = (env.get("LCP_SENDER_ID") or "").strip()
    api_key = (env.get("LCP_API_KEY") or "").strip()
    hmac_secret = (env.get("LCP_HMAC_SECRET") or "").strip()
    receiver_id = (
        env.get("LCP_RECEIVER_ID") or env.get("LCP_PLATFORM_ID") or ""
    ).strip()
    publisher_id = (env.get("LCP_PUBLISHER_ID") or "").strip()

    problems: list[str] = []
    if not endpoint:
        problems.append("LCP_ENDPOINT is not set (e.g. https://platform.example)")
    else:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            problems.append(
                "LCP_ENDPOINT must be an absolute http(s) URL with a host, "
                f"got {endpoint!r}"
            )
    if not sender_id:
        problems.append(
            "LCP_SENDER_ID is not set (the sender ID issued by the receiver)"
        )
    if not api_key and not hmac_secret:
        problems.append(
            "set LCP_API_KEY for Bearer auth or LCP_HMAC_SECRET for HMAC auth"
        )
    try:
        timeout = float(env.get("LCP_TIMEOUT_SECONDS") or 15.0)
    except ValueError:
        timeout = 15.0
    if not 0 < timeout <= 300:
        timeout = 15.0
    return (
        Config(
            endpoint=endpoint.rstrip("/"),
            sender_id=sender_id,
            api_key=api_key,
            hmac_secret=hmac_secret,
            receiver_id=receiver_id,
            publisher_id=publisher_id,
            timeout=timeout,
        ),
        problems,
    )


# ─── HTTP ─────────────────────────────────────────────────────────────────


def _request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 15.0,
) -> Response:
    request = Request(url, data=body, headers=dict(headers or {}), method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return Response(
                status=int(response.status),
                body=response.read(),
                headers=dict(response.headers.items()),
                url=url,
            )
    except HTTPError as error:
        return Response(
            status=int(error.code),
            body=error.read(),
            headers=dict(error.headers.items()) if error.headers else {},
            url=url,
        )
    except URLError as error:
        raise TransportError(url, _reason_text(error.reason)) from error
    except (ssl.SSLError, TimeoutError, OSError) as error:
        raise TransportError(url, _reason_text(error)) from error


def _reason_text(reason: object) -> str:
    text = str(reason or "").strip()
    return text or "connection failed"


def _parse_json(body: bytes) -> Any | None:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


# ─── error extraction and translation ─────────────────────────────────────


def first_error(payload: Any) -> dict[str, Any] | None:
    """Return the first structured LCP error anywhere in the response body."""
    for errors in _error_lists(payload):
        for item in errors:
            if isinstance(item, dict) and item.get("code"):
                return item
    return None


def _error_lists(payload: Any) -> Iterable[list[Any]]:
    if not isinstance(payload, dict):
        return
    for key in ("errors",):
        value = payload.get(key)
        if isinstance(value, list):
            yield value
    ack = payload.get("ack")
    if isinstance(ack, dict):
        value = ack.get("errors")
        if isinstance(value, list):
            yield value
    lcp = payload.get("lcp")
    if isinstance(lcp, dict):
        inner = lcp.get("payload")
        if isinstance(inner, dict):
            value = inner.get("errors")
            if isinstance(value, list):
                yield value
            nested_ack = inner.get("ack")
            if isinstance(nested_ack, dict):
                value = nested_ack.get("errors")
                if isinstance(value, list):
                    yield value


def ack_status(payload: Any) -> str | None:
    """Read the ack status from either flat or envelope-shaped responses."""
    if not isinstance(payload, dict):
        return None
    candidates = [payload.get("ack"), payload]
    lcp = payload.get("lcp")
    if isinstance(lcp, dict):
        candidates.append(lcp.get("payload"))
    for candidate in candidates:
        if isinstance(candidate, dict):
            status = candidate.get("status")
            if isinstance(status, str) and status:
                return status
    return None


def translate(code: str) -> dict[str, Any] | None:
    return ERROR_FIXES.get(code.upper())


def _fallback_code(status: int) -> str:
    return _STATUS_CODES.get(status, f"HTTP-{status}")


def _error_detail(error: dict[str, Any] | None, status: int) -> tuple[str, str, str]:
    """Return (code, message, field) for a failed HTTP response."""
    if error:
        code = str(error.get("code") or _fallback_code(status))
        message = str(error.get("message") or error.get("name") or "")
        field = str(error.get("field") or "")
        details = error.get("details")
        if not field and isinstance(details, dict):
            for key in ("field", "path"):
                value = details.get(key)
                if isinstance(value, str) and value:
                    field = value
                    break
        if not field and isinstance(details, list):
            for item in details:
                if isinstance(item, str) and "/" in item:
                    field = item
                    break
        return code, message, field
    return _fallback_code(status), "", ""


# ─── checks ───────────────────────────────────────────────────────────────


def _stage(
    name: str,
    ok: bool,
    detail: str,
    *,
    code: str = "",
    field: str = "",
    fix: str = "",
    http_status: int = 0,
    fatal: bool = False,
) -> dict[str, Any]:
    return {
        "stage": name,
        "ok": ok,
        "detail": detail,
        "code": code,
        "field": field,
        "fix": fix,
        "http_status": http_status,
        "fatal": fatal,
    }


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    host = host.lower()
    return host in {"localhost", "::1"} or host.startswith("127.")


def check_capabilities(config: Config) -> tuple[dict[str, Any], dict[str, Any] | None]:
    url = f"{config.endpoint}/v1/lcp/capabilities"
    try:
        response = _request(
            "GET", url, headers={"Accept": "application/json"}, timeout=config.timeout
        )
    except TransportError as error:
        return (
            _stage(
                "capabilities",
                False,
                f"{url} unreachable: {error.reason}. "
                "Fix: Check LCP_ENDPOINT (scheme, host, port), DNS, "
                "VPN/firewall, and that the endpoint is running.",
                fatal=True,
            ),
            None,
        )

    payload = _parse_json(response.body)
    if response.status >= 400:
        code, message, field = _error_detail(first_error(payload), response.status)
        return (
            _stage(
                "capabilities",
                False,
                f"GET {url} -> HTTP {response.status}"
                + (f" {code}: {message}" if message else f" ({code})"),
                code=code,
                field=field,
                http_status=response.status,
            ),
            payload if isinstance(payload, dict) else None,
        )

    if not isinstance(payload, dict):
        return (
            _stage(
                "capabilities",
                False,
                f"GET {url} returned HTTP {response.status} but the body is not a JSON object",
                fix=(
                    "Confirm LCP_ENDPOINT is the receiver base URL; "
                    "GET /v1/lcp/capabilities must return the LCP capabilities JSON."
                ),
            ),
            None,
        )

    versions = payload.get("lcp_versions")
    if not isinstance(versions, list) or not all(
        isinstance(item, str) for item in versions
    ):
        return (
            _stage(
                "capabilities",
                False,
                f"GET {url} is not an LCP capabilities document "
                "(lcp_versions is missing or not a list of strings)",
                fix=(
                    "Check LCP_ENDPOINT points at an LCP receiver and that the "
                    "capabilities route exists at /v1/lcp/capabilities."
                ),
            ),
            payload,
        )

    scheme = urlsplit(config.endpoint).scheme.lower()
    host = urlsplit(config.endpoint).hostname
    tls_ok = scheme == "https" or (scheme == "http" and _is_loopback(host))
    if scheme == "https":
        tls_detail = "TLS verified"
    elif _is_loopback(host):
        tls_detail = "TLS not used (loopback)"
    else:
        tls_detail = (
            "TLS not used (plain HTTP is not allowed for non-loopback endpoints)"
        )

    features = payload.get("features")
    feature_names = (
        ",".join(str(key) for key in features) if isinstance(features, dict) else "none"
    )
    auth_methods = payload.get("auth_methods")
    auth_names = (
        ",".join(str(item) for item in auth_methods)
        if isinstance(auth_methods, list)
        else "unspecified"
    )
    detail = (
        f"reachable; {tls_detail}; "
        f"lcp_versions={','.join(versions) or 'none'}; "
        f"features={feature_names or 'none'}; auth_methods={auth_names}"
    )
    return (
        _stage(
            "capabilities",
            tls_ok,
            detail,
            http_status=response.status,
            fatal=not tls_ok,
            fix=(
                ""
                if tls_ok
                else "Use an https:// endpoint (LCP requires TLS); lcp-doctor "
                "will not send credentials over remote plain HTTP."
            ),
        ),
        payload,
    )


def _highest_version(payload: Mapping[str, Any] | None) -> str:
    if not payload:
        return "1.0.0"
    versions = payload.get("lcp_versions")
    if not isinstance(versions, list):
        return "1.0.0"
    parsed: list[tuple[int, int, int]] = []
    for item in versions:
        if not isinstance(item, str):
            continue
        parts = item.split(".")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            continue
        parsed.append((int(parts[0]), int(parts[1]), int(parts[2])))
    if not parsed:
        return "1.0.0"
    major, minor, patch = max(parsed)
    return f"{major}.{minor}.{patch}"


def _vertical_schema_version(payload: Mapping[str, Any] | None, vertical: str) -> str:
    if payload:
        verticals = payload.get("verticals")
        if isinstance(verticals, list):
            for item in verticals:
                if (
                    isinstance(item, dict)
                    and item.get("id") == vertical
                    and isinstance(item.get("schema_version"), str)
                    and item["schema_version"].strip()
                ):
                    return item["schema_version"].strip()
    return "1.0.0"


def _receiver_id(config: Config, capabilities: Mapping[str, Any] | None) -> str:
    if config.receiver_id:
        return config.receiver_id
    if capabilities:
        for key in ("receiver_id", "platform_id"):
            value = capabilities.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if config.publisher_id:
        return config.publisher_id
    return urlsplit(config.endpoint).hostname or "platform"


def build_test_lead(
    config: Config,
    capabilities: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], str, str]:
    """Build a valid ``test: true`` lead envelope; return envelope, key, timestamp."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    lead_id = f"doctor-{uuid4().hex}"
    idempotency_key = f"lcp-doctor-{uuid4().hex}"
    receiver_id = _receiver_id(config, capabilities)
    envelope: dict[str, Any] = {
        "lcp": {
            "version": _highest_version(capabilities),
            "message": {
                "id": str(uuid4()),
                "type": "lead",
                "timestamp": timestamp,
                "sender_id": config.sender_id,
                "receiver_id": receiver_id,
                "correlation_id": None,
                "idempotency_key": idempotency_key,
                "test": True,
            },
            "payload": {
                "lead_id": lead_id,
                "external_id": f"doctor-{uuid4().hex[:12]}",
                "submitted_at": timestamp,
                "status": "NEW",
                "channel": "form",
                "consumer": {
                    "full_name": "LCP Doctor Synthetic Consumer",
                    "email": "lcp-doctor@example.invalid",
                    "phone": "+61400000000",
                },
                "location": {
                    "country_code": "AU",
                    "state_region": "NSW",
                    "postal_code": "2000",
                },
                "compliance": {
                    "consent_timestamp": timestamp,
                    "consent_purposes": ["calls", "email"],
                    "consent_evidence": [
                        {
                            "type": "synthetic_test",
                            "provider": "lcp-doctor",
                            "token_or_url": f"doctor-{uuid4().hex}",
                        }
                    ],
                },
                "provenance": {
                    "source_type": "synthetic",
                    "acquisition_method": "synthetic_test",
                    "platform_source": "lcp-doctor",
                },
                "attributes": {
                    "vertical": "mortgage",
                    "schema_version": _vertical_schema_version(
                        capabilities, "mortgage"
                    ),
                },
            },
        }
    }
    return envelope, idempotency_key, timestamp


def _signature_headers(
    config: Config, body: bytes, idempotency_key: str, timestamp: str
) -> dict[str, str]:
    if config.api_key:
        return {"Authorization": f"Bearer {config.api_key}"}
    canonical = f"{timestamp}\n{idempotency_key}\n".encode("utf-8") + body
    signature = hmac.new(
        config.hmac_secret.encode("utf-8"), canonical, hashlib.sha256
    ).hexdigest()
    return {
        "X-LCP-Timestamp": timestamp,
        "X-LCP-Signature": signature,
    }


def check_lead(
    config: Config, capabilities: Mapping[str, Any] | None
) -> dict[str, Any]:
    url = f"{config.endpoint}/v1/lcp/leads"
    envelope, idempotency_key, timestamp = build_test_lead(config, capabilities)
    body = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-LCP-Sender-Id": config.sender_id,
        "X-LCP-Idempotency-Key": idempotency_key,
        "X-LCP-Test": "true",
        **_signature_headers(config, body, idempotency_key, timestamp),
    }
    try:
        response = _request(
            "POST", url, headers=headers, body=body, timeout=config.timeout
        )
    except TransportError as error:
        return _stage(
            "lead",
            False,
            f"POST {url} unreachable: {error.reason}",
            fix=(
                "Check LCP_ENDPOINT (scheme, host, port), DNS, VPN/firewall, "
                "and that the endpoint is running."
            ),
        )

    payload = _parse_json(response.body)
    error = first_error(payload)
    status = ack_status(payload)

    if not 200 <= response.status < 300:
        code, message, field = _error_detail(error, response.status)
        detail = f"POST {url} -> HTTP {response.status}"
        if message:
            detail += f" {code}: {message}"
        else:
            detail += f" ({code})"
        if field:
            detail += f" [field={field}]"
        return _stage(
            "lead",
            False,
            detail,
            code=code,
            field=field,
            http_status=response.status,
        )

    if error:
        code, message, field = _error_detail(error, response.status)
        detail = f"POST {url} -> HTTP {response.status} with error {code}"
        if message:
            detail += f": {message}"
        if field:
            detail += f" [field={field}]"
        return _stage(
            "lead",
            False,
            detail,
            code=code,
            field=field,
            http_status=response.status,
        )

    if status and status.upper() in _REJECTING_ACK_STATUSES:
        return _stage(
            "lead",
            False,
            f"POST {url} -> HTTP {response.status} but ack status is {status}",
            code="LCP-100",
            http_status=response.status,
        )

    detail = f"HTTP {response.status}"
    if status:
        detail += f" ack={status}"
    else:
        detail += " accepted"
    detail += f"; idempotency_key={idempotency_key}"
    return _stage("lead", True, detail, http_status=response.status)


# ─── output ───────────────────────────────────────────────────────────────


def _stage_line(stage: Mapping[str, Any]) -> str:
    label = "PASS" if stage["ok"] else "FAIL"
    return f"{label:<4}  {stage['stage']:<12}  {stage['detail']}"


def _print_failure(stage: Mapping[str, Any]) -> None:
    code = str(stage.get("code") or "")
    entry = translate(code) if code else None
    if entry:
        print(f"      code   {code} {entry['name']} (HTTP {entry['http']})")
        print(f"      cause  {entry['likely_cause']}")
        print(f"      fix    {entry['fix']}")
        return
    if code:
        print(f"      code   {code}")
    fix = str(stage.get("fix") or "")
    if fix:
        print(f"      fix    {fix}")


def render_human(stages: Sequence[Mapping[str, Any]]) -> None:
    for stage in stages:
        print(_stage_line(stage))
        if not stage["ok"]:
            _print_failure(stage)


def render_json(
    stages: Sequence[Mapping[str, Any]],
    capabilities: Mapping[str, Any] | None,
) -> None:
    document: dict[str, Any] = {
        "ok": all(stage["ok"] for stage in stages),
        "stages": [],
        "capabilities": capabilities,
    }
    for stage in stages:
        record = {
            key: stage[key]
            for key in ("stage", "ok", "detail", "code", "field", "http_status")
            if stage.get(key) not in ("", 0, None)
        }
        entry = translate(str(stage.get("code") or ""))
        if entry:
            record["meaning"] = entry["meaning"]
            record["likely_cause"] = entry["likely_cause"]
            record["fix"] = entry["fix"]
        elif stage.get("fix"):
            record["fix"] = stage["fix"]
        document["stages"].append(record)
    print(json.dumps(document, indent=2, sort_keys=True))


def explain(code: str, as_json: bool) -> int:
    normalized = code.upper()
    entry = translate(normalized)
    if not entry:
        print(
            f"Unknown LCP error code {code!r}. "
            f"Known codes: {', '.join(sorted(ERROR_FIXES))}. See {DOCS_URL}"
        )
        return 1
    if as_json:
        print(
            json.dumps(
                {
                    "code": normalized,
                    **{
                        key: entry[key]
                        for key in (
                            "name",
                            "http",
                            "meaning",
                            "likely_cause",
                            "causes",
                            "fix",
                        )
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    print(f"{normalized} {entry['name']} (HTTP {entry['http']})")
    print(f"Meaning: {entry['meaning']}")
    print("Common causes:")
    for cause in entry["causes"]:
        print(f"  - {cause}")
    print(f"Fix: {entry['fix']}")
    print(f"Guide: {DOCS_URL}")
    return 0


# ─── entry point ──────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lcp-doctor",
        description=(
            "Check an LCP endpoint end to end and get plain-English fixes "
            "instead of a stack trace."
        ),
        epilog=(
            "Environment:\n"
            "  LCP_ENDPOINT        receiver base URL (required)\n"
            "  LCP_SENDER_ID       sender ID issued by the receiver (required)\n"
            "  LCP_API_KEY         Bearer credential (or set LCP_HMAC_SECRET)\n"
            "  LCP_HMAC_SECRET     HMAC shared secret (instead of LCP_API_KEY)\n"
            "  LCP_RECEIVER_ID     receiver platform ID (optional; also reads\n"
            "                      LCP_PLATFORM_ID, capabilities.receiver_id,\n"
            "                      capabilities.platform_id, LCP_PUBLISHER_ID,\n"
            "                      then the URL host)\n"
            "  LCP_TIMEOUT_SECONDS request timeout (default 15)\n"
            "\n"
            f"Full error guide: {DOCS_URL}\n"
            "Per-code fix: lcp-doctor --explain LCP-002"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of the human report",
    )
    parser.add_argument(
        "--explain",
        metavar="CODE",
        help="print the meaning, common causes, and fix for one LCP error code "
        "(no endpoint or environment needed)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.explain:
        return explain(args.explain, args.json)

    config, problems = load_config()
    if problems:
        stages = [
            _stage(
                "config",
                False,
                "; ".join(problems),
                fix=(
                    "Export the required environment variables and run lcp-doctor "
                    "again. See lcp-doctor --help."
                ),
            )
        ]
        render_json(stages, None) if args.json else render_human(stages)
        return 1

    stages: list[dict[str, Any]] = []
    capabilities_stage, capabilities = check_capabilities(config)
    stages.append(capabilities_stage)

    # Unreachable or non-TLS endpoints stop here: every later stage would
    # repeat the verdict, and credentials must never cross remote plain HTTP.
    if capabilities_stage.get("fatal"):
        render_json(stages, capabilities) if args.json else render_human(stages)
        return 1

    stages.append(check_lead(config, capabilities))

    if args.json:
        render_json(stages, capabilities)
    else:
        render_human(stages)
    return 0 if all(stage["ok"] for stage in stages) else 1


if __name__ == "__main__":
    raise SystemExit(main())
