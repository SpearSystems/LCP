#!/usr/bin/env python3
# example — not for production. A documented manual check, not a CI job.
"""Self-test for the LCP buyer webhook receiver example.

Starts ``python/receiver.py`` on an ephemeral port, signs a sample ``post``
delivery and a sample event-subscription delivery exactly the way the platform
does, and asserts:

* both surfaces answer ``200`` with a well-formed ``ack`` envelope;
* a body changed after signing is refused with ``401`` / ``LCP-012``;
* a replayed delivery answers ``200`` with ``ack.status = DUPLICATE``;
* the rejection response never echoes the request body.

Run:
  python3 examples/integrations/buyer-webhook/selftest.py
"""

from __future__ import annotations

import http.client
from datetime import datetime, timezone
import hmac
from hashlib import sha256
import json
import os
import select
import subprocess
import sys
from typing import Any
from uuid import uuid4

HERE = os.path.dirname(os.path.abspath(__file__))
RECEIVER = os.path.join(HERE, "python", "receiver.py")
SECRET = "selftest-pair-secret"
PLATFORM_ID = "platform_001"
BUYER_ID = "buyer_001"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def wire_bytes(envelope: dict[str, Any]) -> bytes:
    """Serialize exactly like the platform's webhook sender."""
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sign(timestamp: str, term: str, body: bytes) -> str:
    mac = hmac.new(SECRET.encode(), digestmod=sha256)
    mac.update(f"{timestamp}\n{term}\n".encode())
    mac.update(body)
    return mac.hexdigest()


def envelope(message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """A platform-sent envelope laid out exactly like the reference sender."""
    message_id = str(uuid4())
    return {
        "lcp": {
            "version": "1.0.0",
            "message": {
                "id": message_id,
                "type": message_type,
                "timestamp": now_iso(),
                "sender_id": PLATFORM_ID,
                "receiver_id": BUYER_ID,
                "correlation_id": str(uuid4()),
                "idempotency_key": f"{PLATFORM_ID}-{message_type}-{message_id}",
                "test": False,
            },
            "payload": payload,
        }
    }


def sample_post() -> tuple[dict[str, Any], bytes, str]:
    """A post delivery: the signature term is the envelope idempotency key."""
    post = envelope(
        "post",
        {
            "lead_id": "lead_selftest_001",
            "delivered_at": now_iso(),
            "price_cents": 2200,
            "currency": "AUD",
            "buyer_id": BUYER_ID,
            "consumer": {
                "first_name": "Jane",
                "last_name": "Smith",
                "email": "jane.smith@example.com",
                "phone": "+61412345678",
            },
        },
    )
    return post, wire_bytes(post), post["lcp"]["message"]["idempotency_key"]


def sample_event() -> tuple[dict[str, Any], bytes, str]:
    """An event delivery: the signature term is the stable X-LCP-Event-Id."""
    event = envelope(
        "event",
        {
            "lead_id": "lead_selftest_001",
            "event": "CONVERTED",
            "timestamp": now_iso(),
            "details": {"conversion_type": "loan_settled"},
            "external_reference": "buyer-crm-selftest",
        },
    )
    return event, wire_bytes(event), str(uuid4())


def request(
    port: int, body: bytes, headers: dict[str, str]
) -> tuple[int, dict[str, Any] | None, str]:
    """POST to the receiver and return (status, parsed JSON body, raw text)."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(
            "POST",
            "/",
            body=body,
            headers={"Content-Type": "application/json", **headers},
        )
        response = connection.getresponse()
        text = response.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = None
        return response.status, parsed, text
    finally:
        connection.close()


def start_receiver() -> tuple[subprocess.Popen[bytes], int]:
    """Start the receiver on an ephemeral port and read the bound port back."""
    process = subprocess.Popen(
        [sys.executable, RECEIVER, "--port", "0", "--secret", SECRET],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    ready, _, _ = select.select([process.stdout], [], [], 15)
    if not ready:
        process.kill()
        _, stderr = process.communicate(timeout=10)
        raise SystemExit(f"receiver did not start: {stderr.decode('utf-8', 'replace')}")
    line = process.stdout.readline().decode("utf-8", "replace").strip()
    if not line.startswith("listening on http://"):
        process.kill()
        raise SystemExit(f"unexpected receiver banner: {line!r}")
    return process, int(line.rsplit(":", 1)[1])


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_ack(
    status: int,
    payload: dict[str, Any] | None,
    original_id: str,
    expected: str,
    label: str,
) -> None:
    check(status == 200, f"{label}: expected 200, got {status}")
    check(payload is not None, f"{label}: empty response body")
    assert payload is not None
    lcp = payload.get("lcp") or {}
    message = lcp.get("message") or {}
    ack = lcp.get("payload") or {}
    check(message.get("type") == "ack", f"{label}: message.type is not ack")
    check(message.get("sender_id") == BUYER_ID, f"{label}: ack sender is not the buyer")
    check(
        message.get("receiver_id") == PLATFORM_ID,
        f"{label}: ack receiver is not the platform",
    )
    check(
        message.get("correlation_id") == original_id,
        f"{label}: correlation_id mismatch",
    )
    check(
        bool(message.get("idempotency_key")), f"{label}: ack idempotency_key is missing"
    )
    check(
        ack.get("original_message_id") == original_id,
        f"{label}: original_message_id mismatch",
    )
    check(
        ack.get("status") == expected,
        f"{label}: ack status is {ack.get('status')!r}, want {expected!r}",
    )
    check(
        str(ack.get("request_id", "")).startswith("req_"),
        f"{label}: request_id is missing",
    )


def check_rejected(
    status: int, payload: dict[str, Any] | None, text: str, label: str
) -> None:
    check(status == 401, f"{label}: expected 401, got {status}")
    errors = (payload or {}).get("errors") or []
    check(
        errors and errors[0].get("code") == "LCP-012",
        f"{label}: expected LCP-012, got {errors}",
    )
    check("Jane" not in text, f"{label}: rejection echoed the request body")


def post_case(port: int) -> str:
    """Post delivery: valid, tampered, and duplicate."""
    post, body, key = sample_post()
    message_id = post["lcp"]["message"]["id"]
    timestamp = now_iso()
    headers = {
        "X-LCP-Sender-Id": PLATFORM_ID,
        "X-LCP-Timestamp": timestamp,
        "X-LCP-Idempotency-Key": key,
        "X-LCP-Signature": sign(timestamp, key, body),
    }

    status, payload, _ = request(port, body, headers)
    check_ack(status, payload, message_id, "RECEIVED", "post valid")

    status, payload, text = request(port, body + b" ", headers)
    check_rejected(status, payload, text, "post tampered")

    status, payload, _ = request(port, body, headers)
    check_ack(status, payload, message_id, "DUPLICATE", "post replay")

    return "valid=200 ack=RECEIVED  tampered=401 LCP-012  replay=200 ack=DUPLICATE"


def event_case(port: int) -> str:
    """Event subscription delivery: valid and tampered."""
    event, body, event_id = sample_event()
    message_id = event["lcp"]["message"]["id"]
    timestamp = now_iso()
    headers = {
        "X-LCP-Sender-Id": PLATFORM_ID,
        "X-LCP-Timestamp": timestamp,
        "X-LCP-Idempotency-Key": event_id,
        "X-LCP-Event-Id": event_id,
        "X-LCP-Subscription-Id": str(uuid4()),
        "X-LCP-Signature": sign(timestamp, event_id, body),
    }

    status, payload, _ = request(port, body, headers)
    check_ack(status, payload, message_id, "RECEIVED", "event valid")

    tampered = body.replace(b"loan_settled", b"loan_settled ")
    check(tampered != body, "event tamper probe did not change the body")
    status, payload, text = request(port, tampered, headers)
    check_rejected(status, payload, text, "event tampered")

    return "valid=200 ack=RECEIVED  tampered=401 LCP-012"


def main() -> int:
    process, port = start_receiver()
    try:
        post_line = post_case(port)
        event_line = event_case(port)
    except AssertionError as exc:
        process.kill()
        _, stderr = process.communicate(timeout=10)
        print(f"FAIL: {exc}", file=sys.stderr)
        print(stderr.decode("utf-8", "replace"), file=sys.stderr)
        return 1
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - hard cleanup
                process.kill()
                process.communicate()

    print(f"post delivery  : {post_line}")
    print(f"event delivery : {event_line}")
    print("ok: both signed delivery surfaces verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
