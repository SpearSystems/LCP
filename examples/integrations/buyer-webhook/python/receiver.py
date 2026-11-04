#!/usr/bin/env python3
# example — not for production. Durable idempotency, TLS/WAF, secret
# management, and durable CRM handoff belong to the deployment (README.md).
"""Minimal buyer-side webhook receiver for LCP deliveries, stdlib only.

It verifies the two signed surfaces the platform sends and answers each with
an ``ack`` envelope:

* ``post`` delivery — HMAC-SHA256 over
  ``<X-LCP-Timestamp>\\n<X-LCP-Idempotency-Key>\\n<raw body>``
  (spec/01-transport.md §9).
* event subscription delivery — HMAC-SHA256 over
  ``<X-LCP-Timestamp>\\n<X-LCP-Event-Id>\\n<raw body>``
  (docs/OPERATIONS.md "Event subscriptions"); the event id is the signature's
  idempotency term, so ``X-LCP-Idempotency-Key`` is not the envelope key here.

Rejections log a reason only — never the body, the secret, or consumer data —
and answer an LCP error (``401``/``LCP-012`` for a signature or replay failure)
before any PII is touched.

Usage:
  python3 receiver.py --port 8090 --secret "$LCP_WEBHOOK_SECRET" [--print-body]

Secret may also come from ``LCP_WEBHOOK_SECRET``.
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
from hashlib import sha256
import json
import logging
import os
import sys
from typing import Any
from uuid import uuid4

MAX_BODY_BYTES = 1_000_000
REPLAY_WINDOW_SECONDS = 300
MAX_SEEN_IDS = 4096

LOG = logging.getLogger("lcp-buyer-receiver")


def now_iso() -> str:
    """Whole-second UTC wire form (LEP-0002): ``YYYY-MM-DDTHH:MM:SSZ``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(value: str) -> datetime | None:
    """Lenient clock parsing for the replay window, never for the digest.

    The signature is always recomputed over the received header string
    verbatim; this parse exists only to decide freshness.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def sign(secret: str, timestamp: str, term: str, body: bytes) -> str:
    """Lowercase hex HMAC-SHA256 over ``<timestamp>\\n<term>\\n<body>``."""
    mac = hmac.new(secret.encode("utf-8"), digestmod=sha256)
    mac.update(timestamp.encode("utf-8"))
    mac.update(b"\n")
    mac.update(term.encode("utf-8"))
    mac.update(b"\n")
    mac.update(body)
    return mac.hexdigest()


class Rejection(Exception):
    """A request refused before any processing, with a log-safe reason."""

    reason: str
    status: int
    code: str

    def __init__(self, reason: str, status: int = 401, code: str = "LCP-012"):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.code = code


class SeenIds:
    """Bounded in-memory de-duplication set (demo only; use a durable claim)."""

    _limit: int

    def __init__(self, limit: int = MAX_SEEN_IDS) -> None:
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._limit = limit

    def claim(self, key: str) -> bool:
        """Return True the first time ``key`` is seen, False for duplicates."""
        if key in self._ids:
            return False
        self._ids[key] = None
        while len(self._ids) > self._limit:
            _ = self._ids.popitem(last=False)
        return True


def build_ack(envelope: dict[str, Any], status: str) -> dict[str, Any]:
    """The ``ack`` envelope a buyer returns for a verified delivery.

    ``sender_id`` is the buyer this delivery was addressed to and
    ``receiver_id`` the platform that sent it, so the ack travels back on the
    same pair. The ack's own idempotency key follows the platform convention
    ``<sender_id>-ack-<message id>``.
    """
    message = envelope["lcp"]["message"]
    buyer_id = message["receiver_id"]
    ack_id = str(uuid4())
    payload: dict[str, Any] = {
        "original_message_id": message["id"],
        "status": status,
        "request_id": f"req_{ack_id.replace('-', '')[:12]}",
    }
    payload_block = envelope["lcp"].get("payload")
    lead_id = payload_block.get("lead_id") if isinstance(payload_block, dict) else None
    if isinstance(lead_id, str) and lead_id:
        payload["lead_id"] = lead_id
    return {
        "lcp": {
            "version": "1.0.0",
            "message": {
                "id": ack_id,
                "type": "ack",
                "timestamp": now_iso(),
                "sender_id": buyer_id,
                "receiver_id": message["sender_id"],
                "correlation_id": message["id"],
                "idempotency_key": f"{buyer_id}-ack-{ack_id}",
                "test": bool(message.get("test", False)),
            },
            "payload": payload,
        }
    }


class Receiver(BaseHTTPRequestHandler):
    server_version = "lcp-buyer-receiver"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    secret: str = ""
    print_body: bool = False
    seen: SeenIds = SeenIds()

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path.split("?", 1)[0] == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        self._send_error_json(
            405, "LCP-001", "only POST deliveries and GET /healthz are served"
        )

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        try:
            envelope, surface, dedupe_key = self._read_and_verify()
        except Rejection as exc:
            LOG.warning("rejected delivery: %s", exc.reason)
            message = (
                "signature verification failed"
                if exc.status == 401
                else exc.reason
            )
            self._send_error_json(exc.status, exc.code, message)
            return
        except Exception as exc:  # never leave a delivery unanswered
            LOG.error("delivery verification failed: %s", type(exc).__name__)
            self._send_error_json(500, "LCP-500", "internal error")
            return
        try:
            if self.print_body:
                # Explicit opt-in debug dump; stdout only, never a log stream.
                sys.stdout.write(json.dumps(envelope, indent=2, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            status = "RECEIVED" if self.seen.claim(dedupe_key) else "DUPLICATE"
            ack = build_ack(envelope, status)
        except Exception as exc:  # never leave a delivery unanswered
            LOG.error("delivery handling failed: %s", type(exc).__name__)
            self._send_error_json(500, "LCP-500", "internal error")
            return
        LOG.info("accepted %s delivery (status=%s)", surface, status)
        self._send_json(200, ack)

    # -- verification ----------------------------------------------------

    def _read_and_verify(self) -> tuple[dict[str, Any], str, str]:
        """Read the exact bytes, verify, and return the parsed envelope."""
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not raw_length.isdigit():
            raise Rejection("missing or invalid Content-Length", 400, "LCP-001")
        length = int(raw_length)
        if length <= 0:
            raise Rejection("empty body", 400, "LCP-001")
        if length > MAX_BODY_BYTES:
            # The unread body would desynchronize a kept-alive connection.
            self.close_connection = True
            raise Rejection(f"body exceeds {MAX_BODY_BYTES} bytes", 413, "LCP-001")
        body = self.rfile.read(length)
        if len(body) != length:
            self.close_connection = True
            raise Rejection("truncated body", 400, "LCP-001")

        signature = self.headers.get("X-LCP-Signature", "")
        timestamp = self.headers.get("X-LCP-Timestamp", "")
        event_id = self.headers.get("X-LCP-Event-Id", "")
        idempotency_key = self.headers.get("X-LCP-Idempotency-Key", "")
        if not signature:
            raise Rejection("missing X-LCP-Signature")
        if not timestamp:
            raise Rejection("missing X-LCP-Timestamp")

        # Event deliveries sign the stable event id; post deliveries sign the
        # envelope's idempotency key.
        surface = "event" if event_id else "post"
        term = event_id if event_id else idempotency_key
        if not term:
            raise Rejection("missing X-LCP-Idempotency-Key")

        signed_at = parse_timestamp(timestamp)
        if signed_at is None:
            raise Rejection("X-LCP-Timestamp is not a timezone-aware timestamp")
        skew = abs((datetime.now(timezone.utc) - signed_at).total_seconds())
        if skew > REPLAY_WINDOW_SECONDS:
            raise Rejection("X-LCP-Timestamp is outside the replay window")

        expected = sign(self.secret, timestamp, term, body)
        if not hmac.compare_digest(expected, signature):
            raise Rejection("signature mismatch")

        envelope = self._parse_envelope(body)
        message = envelope["lcp"]["message"]
        if surface == "post" and message.get("idempotency_key") != idempotency_key:
            raise Rejection("X-LCP-Idempotency-Key disagrees with the envelope")
        return envelope, surface, term

    def _parse_envelope(self, body: bytes) -> dict[str, Any]:
        """Structural checks only — the receiver never needs consumer fields."""
        try:
            envelope = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise Rejection("body is not JSON", 400, "LCP-001") from None
        lcp = envelope.get("lcp") if isinstance(envelope, dict) else None
        message = lcp.get("message") if isinstance(lcp, dict) else None
        if not isinstance(message, dict):
            raise Rejection("body has no lcp.message object", 400, "LCP-003")
        for field in ("id", "type", "sender_id", "receiver_id"):
            if not isinstance(message.get(field), str) or not message[field]:
                raise Rejection(f"lcp.message.{field} is missing", 400, "LCP-003")
        if message["type"] not in ("post", "event"):
            raise Rejection("delivery is not a post or event", 400, "LCP-006")
        return envelope

    # -- responses -------------------------------------------------------

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_error_json(self, status: int, code: str, message: str) -> None:
        names = {
            "LCP-001": "INVALID_FORMAT",
            "LCP-003": "MISSING_REQUIRED_FIELD",
            "LCP-006": "UNKNOWN_MESSAGE_TYPE",
            "LCP-012": "SIGNATURE_INVALID",
            "LCP-500": "INTERNAL_ERROR",
        }
        self._send_json(
            status,
            {
                "errors": [
                    {
                        "code": code,
                        "name": names.get(code, "INVALID_FORMAT"),
                        "message": message,
                    }
                ]
            },
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify LCP webhook deliveries (post + event) and answer with an ack."
    )
    parser.add_argument(
        "--port", type=int, default=8090, help="listen port (0 = ephemeral)"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="bind address (default 0.0.0.0)"
    )
    parser.add_argument(
        "--secret",
        default=os.environ.get("LCP_WEBHOOK_SECRET", ""),
        help="HMAC secret (or set LCP_WEBHOOK_SECRET)",
    )
    parser.add_argument(
        "--print-body",
        action="store_true",
        help="print each verified body; never printed on rejection",
    )
    args = parser.parse_args(argv)

    if not args.secret:
        print("error: pass --secret or set LCP_WEBHOOK_SECRET", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    Receiver.secret = args.secret
    Receiver.print_body = bool(args.print_body)

    server = ThreadingHTTPServer((args.host, args.port), Receiver)
    host, port = server.server_address[0], server.server_address[1]
    print(f"listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
