#!/usr/bin/env python3
"""Illustrative buyer webhook for an LCP platform.

This is an integration template, not a production server. Replace SEEN_KEYS
with a durable database/queue claim, put the endpoint behind TLS/WAF, and add
structured operational logging that never records raw PII or secrets.

Environment:
  LCP_BUYER_ID, LCP_PLATFORM_ENDPOINT, LCP_PLATFORM_ID,
  LCP_BUYER_HMAC_SECRET, and optionally LCP_SCHEMA_DIR.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Lock

from lcp_sdk import LCPClient, SchemaValidator, SignatureError, verify_hmac, build_envelope


BUYER_ID = os.environ.get("LCP_BUYER_ID", "buyer_001")
PLATFORM_ID = os.environ.get("LCP_PLATFORM_ID", "platform_001")
PLATFORM_ENDPOINT = os.environ.get("LCP_PLATFORM_ENDPOINT", "https://platform.example")
HMAC_SECRET = os.environ.get("LCP_BUYER_HMAC_SECRET", "")
VALIDATOR = SchemaValidator(os.environ.get("LCP_SCHEMA_DIR"))
SEEN_KEYS: set[str] = set()  # Replace with a durable unique claim in production.
SEEN_LOCK = Lock()


def header(handler: BaseHTTPRequestHandler, name: str) -> str:
    return handler.headers.get(name, "")


def claim_once(key: str) -> bool:
    with SEEN_LOCK:
        if key in SEEN_KEYS:
            return False
        SEEN_KEYS.add(key)
        return True


def release_claim(key: str) -> None:
    with SEEN_LOCK:
        SEEN_KEYS.discard(key)


def submit_bid(ping: dict) -> None:
    message = ping["lcp"]["message"]
    payload = ping["lcp"]["payload"]
    test = bool(message.get("test", False))
    bid = build_envelope(
        "bid",
        sender_id=BUYER_ID,
        receiver_id=PLATFORM_ID,
        correlation_id=message["id"],
        test=test,
        payload={
            "ping_id": payload["ping_id"],
            "decision": "accept",
            "bid_price_cents": max(2200, payload["floor_price_cents"]),
            "currency": payload["currency"],
            "estimated_contact_seconds": 45,
            "buyer_reference": f"example-{payload['ping_id']}",
        },
    )
    LCPClient(
        PLATFORM_ENDPOINT,
        sender_id=BUYER_ID,
        hmac_secret=HMAC_SECRET,
        validator=VALIDATOR,
    ).submit_bid(bid, test=test)


class BuyerHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 2_000_000:
            self.send_error(413, "invalid request size")
            return
        raw_body = self.rfile.read(length)
        idempotency_key = header(self, "X-LCP-Idempotency-Key")
        claimed = False
        try:
            if header(self, "X-LCP-Sender-Id") != PLATFORM_ID:
                raise SignatureError("unexpected platform sender")
            verify_hmac(
                HMAC_SECRET,
                header(self, "X-LCP-Signature"),
                header(self, "X-LCP-Timestamp"),
                idempotency_key,
                raw_body,
            )
            envelope = json.loads(raw_body)
            VALIDATOR.require_valid_envelope(envelope)
            if envelope["lcp"]["message"]["receiver_id"] != BUYER_ID:
                raise ValueError("message receiver is not this buyer")
            if not claim_once(idempotency_key):
                self.send_response(409)
                self.end_headers()
                return
            claimed = True
            message_type = envelope["lcp"]["message"]["type"]
            if message_type == "ping":
                submit_bid(envelope)
            elif message_type == "post":
                # Replace with an idempotent CRM/dialler handoff.
                print("received synthetic post", envelope["lcp"]["payload"]["lead_id"])
            elif message_type == "event":
                print("received lifecycle event", envelope["lcp"]["payload"]["event"])
            else:
                raise ValueError(f"unexpected webhook type: {message_type}")
        except (KeyError, TypeError, ValueError, SignatureError, json.JSONDecodeError) as exc:
            if claimed:
                release_claim(idempotency_key)
            print(f"rejected buyer webhook: {type(exc).__name__}")
            self.send_error(400, "invalid LCP webhook")
            return
        except Exception as exc:
            if claimed:
                release_claim(idempotency_key)
            print(f"buyer webhook processing failed: {type(exc).__name__}")
            self.send_error(502, "temporary buyer processing failure")
            return
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        print(format % args)


if __name__ == "__main__":
    if not HMAC_SECRET:
        raise SystemExit("Set LCP_BUYER_HMAC_SECRET before starting")
    ThreadingHTTPServer(("0.0.0.0", 8090), BuyerHandler).serve_forever()
