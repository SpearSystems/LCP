"""HTTP client for LCP-compatible endpoints."""

from __future__ import annotations

import json
import time
from typing import Any, Mapping
from urllib.parse import quote
from uuid import uuid4

import httpx

from .envelope import message_metadata, utc_timestamp
from .signing import sign_hmac
from .validation import SchemaValidator

MAX_BODY_CHARS = 2048
MAX_BATCH_ITEMS = 100  # LEP-0001 W2-01 batch cap (schemas/batch.json)
_TRUNCATION_SUFFIX = "…[truncated]"
_REQUEST_ID_MAX_CHARS = 64  # request ids are server-supplied; bound the echo


def _bounded_body(body: Any) -> Any:
    """Cap the error body retained on the exception (see LCPHTTPError)."""
    if isinstance(body, str):
        if len(body) <= MAX_BODY_CHARS:
            return body
        return body[:MAX_BODY_CHARS] + _TRUNCATION_SUFFIX
    try:
        rendered = json.dumps(body, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        rendered = repr(body)
    if len(rendered) <= MAX_BODY_CHARS:
        return body
    return {
        "truncated": True,
        "preview": rendered[:MAX_BODY_CHARS] + _TRUNCATION_SUFFIX,
    }


class LCPHTTPError(RuntimeError):
    """Raised when an LCP endpoint returns a non-success response.

    The response body is deliberately excluded from ``str()``/``repr()``:
    exceptions land in logs and tracebacks, and a server-controlled body can
    carry PII in error messages or be arbitrarily large (issue #136). The
    error string carries the status code and the request id only — the
    pattern the TypeScript (``LcpHttpError``) and Go (``HTTPError``) SDKs
    already follow. The body stays available on ``.body`` for programmatic
    handling and bounded operator debugging, truncated past
    ``MAX_BODY_CHARS`` (2048 characters of the value or of its compact JSON
    serialisation, suffixed with ``…[truncated]``); an over-bound structured
    body collapses to ``{"truncated": True, "preview": ...}``, while a body
    inside the bound is kept intact so callers can still read error codes.
    """

    def __init__(
        self,
        status_code: int,
        body: Any,
        *,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        if not request_id and isinstance(body, dict):
            request_id = body.get("request_id")
        self.request_id = (
            str(request_id)[:_REQUEST_ID_MAX_CHARS] if request_id else None
        )
        self.body = _bounded_body(body)
        identity = f" request-id {self.request_id}" if self.request_id else ""
        super().__init__(f"LCP HTTP {status_code}{identity}")

    def __repr__(self) -> str:
        return (
            f"LCPHTTPError(status_code={self.status_code!r}, "
            f"request_id={self.request_id!r})"
        )


class LCPClient:
    """Small, dependency-light client for the LCP HTTP binding."""

    def __init__(
        self,
        endpoint: str,
        *,
        sender_id: str | None = None,
        api_key: str | None = None,
        hmac_secret: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        validator: SchemaValidator | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.sender_id = sender_id
        self.api_key = api_key
        self.hmac_secret = hmac_secret
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.validator = validator

    def _headers(
        self,
        body: bytes,
        *,
        idempotency_key: str | None,
        test: bool = False,
    ) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.sender_id:
            headers["X-LCP-Sender-Id"] = self.sender_id
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.hmac_secret:
            timestamp = utc_timestamp()
            headers["X-LCP-Timestamp"] = timestamp
            if idempotency_key:
                headers["X-LCP-Idempotency-Key"] = idempotency_key
            headers["X-LCP-Signature"] = sign_hmac(
                self.hmac_secret, timestamp, idempotency_key, body
            )
        elif idempotency_key:
            headers["X-LCP-Idempotency-Key"] = idempotency_key
        if test:
            headers["X-LCP-Test"] = "true"
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        test: bool = False,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        """Perform one LCP request and return the decoded JSON body."""
        data, _headers = self.request_with_headers(
            method,
            path,
            payload=payload,
            params=params,
            idempotency_key=idempotency_key,
            test=test,
            max_retries=max_retries,
        )
        return data

    def request_with_headers(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        test: bool = False,
        max_retries: int | None = None,
    ) -> tuple[dict[str, Any], Mapping[str, str]]:
        """Perform one LCP request and return ``(body, response headers)``.

        Surface used by consumers that need transport metadata the JSON body
        cannot carry — e.g. the MCP adapter's request-history pagination
        (LEP-0005 §3 requires the adapter to surface the header values inside
        the tool result). Signing, retries, and error mapping are identical to
        :meth:`request`; this method is the single implementation of both.
        """
        body = b""
        if payload is not None:
            body = json.dumps(
                payload, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            if idempotency_key is None:
                try:
                    idempotency_key = message_metadata(payload)["idempotency_key"]
                except (KeyError, ValueError):
                    pass
        headers = self._headers(body, idempotency_key=idempotency_key, test=test)
        url = f"{self.endpoint}/{path.lstrip('/')}"
        retryable = method.upper() in {"GET", "HEAD", "PUT", "DELETE", "POST"}
        last_error: Exception | None = None
        retries = self.max_retries if max_retries is None else max(0, max_retries)

        for attempt in range(retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.request(
                        method,
                        url,
                        content=body or None,
                        headers=headers,
                        params=params,
                    )
            except httpx.RequestError as exc:
                last_error = exc
                if not retryable or attempt >= retries:
                    raise
                time.sleep(2**attempt)
                continue

            try:
                data = response.json()
            except ValueError:
                data = {"raw": response.text}
            if response.is_success:
                return data, response.headers
            if response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                retry_after = response.headers.get("Retry-After")
                delay = (
                    float(retry_after)
                    if retry_after and retry_after.isdigit()
                    else 2**attempt
                )
                time.sleep(delay)
                continue
            raise LCPHTTPError(
                response.status_code,
                data,
                request_id=response.headers.get("X-LCP-Request-Id"),
            )

        if last_error:
            raise last_error
        raise RuntimeError("LCP request failed without a response")

    def submit_lead(
        self, envelope: dict[str, Any], *, test: bool = False
    ) -> dict[str, Any]:
        self._validate(envelope)
        return self.request("POST", "/v1/lcp/leads", payload=envelope, test=test)

    def submit_call(
        self, envelope: dict[str, Any], *, test: bool = False
    ) -> dict[str, Any]:
        self._validate(envelope)
        return self.request("POST", "/v1/lcp/calls", payload=envelope, test=test)

    def submit_bid(
        self, envelope: dict[str, Any], *, test: bool = False
    ) -> dict[str, Any]:
        self._validate(envelope)
        return self.request("POST", "/v1/lcp/bids", payload=envelope, test=test)

    def submit_leads_batch(
        self,
        items: list[dict[str, Any]],
        request_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Submit up to ``MAX_BATCH_ITEMS`` lead items in one request.

        Each item is ``{"item_id": ..., "idempotency_key": ..., "payload": ...}``
        with the lead payload at ``payload`` (schemas/batch.json). Returns the
        per-item receipts in input order; an HTTP 207 partial success is a
        successful response. The batch is never retried as a whole — retrying
        only ``rejected``/``error`` receipts is the caller's job, and per-item
        idempotency keys make a full resend safe but wasteful.
        """
        if not items or len(items) > MAX_BATCH_ITEMS:
            raise ValueError(
                f"batch supports 1..{MAX_BATCH_ITEMS} items, got {len(items)}"
            )
        body = {"request_id": request_id or str(uuid4()), "items": items}
        response = self.request(
            "POST", "/v1/lcp/leads/batch", payload=body, max_retries=0
        )
        results = response.get("results")
        if not isinstance(results, list):
            raise ValueError("batch response missing results")
        order = {item.get("item_id"): index for index, item in enumerate(items)}
        return sorted(
            results,
            key=lambda result: order.get(result.get("item_id"), len(items)),
        )

    def query_lead_status(self, lead_id: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/lcp/leads/{quote(lead_id, safe='')}")

    def get_schema(self, name: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/lcp/schemas/{quote(name, safe='/')}")

    def get_capabilities(self) -> dict[str, Any]:
        return self.request("GET", "/v1/lcp/capabilities")

    def list_offers(self, vertical: str | None = None) -> dict[str, Any]:
        params = {"vertical": vertical} if vertical else None
        return self.request("GET", "/v1/lcp/offers", params=params)

    def _validate(self, envelope: dict[str, Any]) -> None:
        if self.validator:
            self.validator.require_valid_envelope(envelope)
