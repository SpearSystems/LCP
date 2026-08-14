"""LCP HTTP client — thin wrapper around the LCP REST API.

Stateless: each call makes one HTTP request. No sessions, no state.
Supports both Bearer (API key) and HMAC authentication.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any
from urllib.parse import quote

import httpx


class LCPClient:
    """Minimal HTTP client for an LCP-compliant REST endpoint."""

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        hmac_secret: str | None = None,
        sender_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.endpoint = (endpoint or os.environ.get("LCP_ENDPOINT", "http://localhost:8000")).rstrip("/")
        self.api_key = api_key or os.environ.get("LCP_API_KEY")
        self.hmac_secret = hmac_secret or os.environ.get("LCP_HMAC_SECRET")
        self.sender_id = sender_id or os.environ.get("LCP_SENDER_ID", "")
        self.timeout = timeout

    def _auth_headers(self, body: bytes | None = None) -> dict[str, str]:
        """Build auth headers. Uses Bearer token if available, else HMAC."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif self.hmac_secret and body is not None:
            timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            sig = hmac.new(
                self.hmac_secret.encode(), body + timestamp.encode(), hashlib.sha256
            ).hexdigest()
            headers["X-LCP-Signature"] = sig
            headers["X-LCP-Timestamp"] = timestamp
        return headers

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST an LCP envelope to the endpoint."""
        body = json.dumps(payload).encode()
        headers = self._auth_headers(body)
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{self.endpoint}{path}", content=body, headers=headers)
            return _parse_response(resp)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET from the endpoint."""
        headers = self._auth_headers()
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(f"{self.endpoint}{path}", params=params, headers=headers)
            return _parse_response(resp)

    # ─── LCP endpoint wrappers ────────────────────────────────────────────

    def submit_lead(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self.post("/v1/lcp/leads", envelope)

    def submit_call(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self.post("/v1/lcp/calls", envelope)

    def query_lead_status(self, lead_id: str) -> dict[str, Any]:
        return self.get(f"/v1/lcp/leads/{quote(lead_id, safe='')}")

    def get_schema(self, name: str) -> dict[str, Any]:
        return self.get(f"/v1/lcp/schemas/{quote(name, safe='')}")

    def get_capabilities(self) -> dict[str, Any]:
        return self.get("/v1/lcp/capabilities")

    def list_offers(self, vertical: str | None = None) -> dict[str, Any]:
        params = {"vertical": vertical} if vertical else None
        return self.get("/v1/lcp/offers", params=params)


def _parse_response(resp: httpx.Response) -> dict[str, Any]:
    """Parse an HTTP response into a dict. Raises on transport errors."""
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    data["_status_code"] = resp.status_code
    data["_ok"] = resp.is_success
    return data