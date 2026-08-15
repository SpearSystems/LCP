"""HTTP-independent request dispatch for the reference platform."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .auth import AuthenticationError, header
from .router import Platform, RequestError


class PlatformService:
    def __init__(self, platform: Platform):
        self.platform = platform

    def dispatch(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        headers = {key: value for key, value in (headers or {}).items()}
        header_bytes = sum(len(key.encode("utf-8")) + len(value.encode("utf-8")) for key, value in headers.items())
        if header_bytes > self.platform.config.max_header_bytes:
            return self._response(431, {"errors": [{"code": "LCP-001", "message": "Request headers are too large"}]})
        parsed = urlsplit(path)
        route = parsed.path.rstrip("/") or "/"
        try:
            if method == "POST":
                return self._post(route, headers, body)
            if method == "GET":
                return self._get(route, parsed.query, headers)
            return self._response(405, {"errors": [{"code": "LCP-001", "message": "Method not allowed"}]})
        except AuthenticationError as exc:
            return self._response(401, {"errors": [{"code": exc.code, "message": str(exc)}]})
        except RequestError as exc:
            error: dict[str, Any] = {"code": exc.code, "message": str(exc)}
            if exc.details is not None:
                error["details"] = exc.details
            response_headers = {"Retry-After": "60"} if exc.status_code == 429 else None
            return self._response(exc.status_code, {"errors": [error]}, response_headers)
        except json.JSONDecodeError:
            return self._response(
                400,
                {"errors": [{"code": "LCP-001", "message": "Request body must be JSON"}]},
            )
        except Exception:
            return self._response(
                500,
                {"errors": [{"code": "LCP-500", "message": "Internal server error"}]},
            )

    def _post(
        self,
        route: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        if len(body) > self.platform.config.max_body_bytes:
            raise RequestError("Request body is too large", "LCP-001", 413)
        content_type = header(headers, "Content-Type") or ""
        if not content_type.lower().split(";", 1)[0].strip() == "application/json":
            raise RequestError("Content-Type must be application/json", "LCP-001", 415)
        envelope = json.loads(body)
        if route in {"/v1/lcp/leads", "/v1/lcp/calls", "/v1/lcp/bids"}:
            message = envelope.get("lcp", {}).get("message", {})
            header_key = header(headers, "X-LCP-Idempotency-Key")
            if not header_key or header_key != message.get("idempotency_key"):
                raise RequestError(
                    "X-LCP-Idempotency-Key must match the envelope",
                    "LCP-012",
                    401,
                )
        if route == "/v1/lcp/leads":
            result = self.platform.ingest(envelope, headers=headers, raw_body=body)
        elif route == "/v1/lcp/calls":
            result = self.platform.ingest(envelope, headers=headers, raw_body=body)
        elif route == "/v1/lcp/bids":
            result = self.platform.submit_bid(envelope, headers=headers, raw_body=body)
        else:
            raise RequestError("Endpoint not found", "LCP-001", 404)
        return self._response(200, result)

    def _get(
        self,
        route: str,
        query: str,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        if route in {"/health/live", "/v1/lcp/health/live"}:
            return self._response(200, {"status": "ok"})
        if route in {"/health/ready", "/v1/lcp/health/ready"}:
            try:
                self.platform.store.healthcheck()
            except Exception:
                return self._response(503, {"status": "not_ready"})
            return self._response(200, {"status": "ready"})
        if route == "/v1/lcp/capabilities":
            return self._response(200, self.platform.capabilities())
        if route.startswith("/v1/lcp/schemas/"):
            return self._response(200, self.platform.schema(route.removeprefix("/v1/lcp/schemas/")))
        if route == "/v1/lcp/offers":
            self._authenticate_read(headers, required_scope="offer:read")
            vertical = parse_qs(query).get("vertical", [None])[0]
            return self._response(200, self.platform.public_offers(vertical))
        if route.startswith("/v1/lcp/leads/"):
            sender_id = self._authenticate_read(headers, required_scope="lead:read")
            lead_id = route.removeprefix("/v1/lcp/leads/")
            self.platform.authorize_lead_read(lead_id, sender_id)
            return self._response(200, self.platform.lead_status(lead_id))
        raise RequestError("Endpoint not found", "LCP-001", 404)

    def _authenticate_read(self, headers: dict[str, str], *, required_scope: str) -> str:
        return self.platform.auth.authenticate(
            sender_id=header(headers, "X-LCP-Sender-Id"),
            headers=headers,
            body=b"",
            mutating=False,
            required_scope=required_scope,
        )

    @staticmethod
    def _response(
        status: int,
        payload: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        response_headers = {"Content-Type": "application/json; charset=utf-8"}
        response_headers.update(extra_headers or {})
        return status, response_headers, payload
