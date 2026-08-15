"""HTTP client for LCP-compatible endpoints."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import quote

import httpx

from .envelope import message_metadata, utc_timestamp
from .signing import sign_hmac
from .validation import SchemaValidator


class LCPHTTPError(RuntimeError):
    """Raised when an LCP endpoint returns a non-success response."""

    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self.body = body
        super().__init__(f"LCP HTTP {status_code}: {body}")


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
    ) -> dict[str, Any]:
        body = b""
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
            if idempotency_key is None:
                try:
                    idempotency_key = message_metadata(payload)["idempotency_key"]
                except (KeyError, ValueError):
                    pass
        headers = self._headers(body, idempotency_key=idempotency_key, test=test)
        url = f"{self.endpoint}/{path.lstrip('/')}"
        retryable = method.upper() in {"GET", "HEAD", "PUT", "DELETE", "POST"}
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
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
                if not retryable or attempt >= self.max_retries:
                    raise
                time.sleep(2**attempt)
                continue

            try:
                data = response.json()
            except ValueError:
                data = {"raw": response.text}
            if response.is_success:
                return data
            if response.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                time.sleep(delay)
                continue
            raise LCPHTTPError(response.status_code, data)

        if last_error:
            raise last_error
        raise RuntimeError("LCP request failed without a response")

    def submit_lead(self, envelope: dict[str, Any], *, test: bool = False) -> dict[str, Any]:
        self._validate(envelope)
        return self.request("POST", "/v1/lcp/leads", payload=envelope, test=test)

    def submit_call(self, envelope: dict[str, Any], *, test: bool = False) -> dict[str, Any]:
        self._validate(envelope)
        return self.request("POST", "/v1/lcp/calls", payload=envelope, test=test)

    def submit_bid(self, envelope: dict[str, Any], *, test: bool = False) -> dict[str, Any]:
        self._validate(envelope)
        return self.request("POST", "/v1/lcp/bids", payload=envelope, test=test)

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
