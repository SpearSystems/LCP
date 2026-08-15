#!/usr/bin/env python3
"""Synthetic buyer for the LCP sandbox; never use with real PII."""

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from urllib.request import Request, urlopen
from uuid import uuid4

PLATFORM_URL = os.environ.get("LCP_PLATFORM_URL", "http://lcp-platform-sandbox:8080")


def envelope(message_type: str, receiver_id: str, payload: dict) -> dict:
    message_id = str(uuid4())
    return {
        "lcp": {
            "version": "1.0.0",
            "message": {
                "id": message_id,
                "type": message_type,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sender_id": "buyer_sandbox",
                "receiver_id": receiver_id,
                "correlation_id": None,
                "idempotency_key": f"buyer-sandbox-{message_type}-{message_id}",
                "test": True,
            },
            "payload": payload,
        }
    }


class BuyerHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        message = json.loads(self.rfile.read(length))
        message_type = message["lcp"]["message"]["type"]
        if message_type == "ping":
            ping = message["lcp"]["payload"]
            bid = envelope(
                "bid",
                message["lcp"]["message"]["sender_id"],
                {
                    "ping_id": ping["ping_id"],
                    "decision": "accept",
                    "bid_price_cents": 2200,
                    "currency": ping["currency"],
                    "estimated_contact_seconds": 45,
                    "buyer_reference": "sandbox-buyer",
                },
            )
            body = json.dumps(bid).encode()
            request = Request(
                f"{PLATFORM_URL}/v1/lcp/bids",
                data=body,
                headers={"Content-Type": "application/json", "X-LCP-Test": "true"},
                method="POST",
            )
            with urlopen(request, timeout=10) as response:
                print(f"sandbox bid response: {response.status}")
        elif message_type == "post":
            lead_id = message["lcp"]["payload"]["lead_id"]
            print(f"sandbox buyer received post: {lead_id}")
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        print(format % args)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), BuyerHandler).serve_forever()
