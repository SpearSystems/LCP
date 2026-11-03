"""SPEC §9 delivered-post response deadline (issue #193).

The reference platform enforced a bid-side 30-second window but a delivered
post never expired, so a receiver that never acknowledged one left the lead
``POSTED`` indefinitely. These tests pin the post-delivery leg:

* an unacknowledged post expires the lead once the window elapses, marks the
  delivery ``FAILED``, dead-letters it, and audits the transition;
* an acknowledged (2xx) post is never expired by the sweep — it is the
  response the platform observes and the event that makes the post payable;
* the window is advertised in the capability response and configurable.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from uuid import uuid4

from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform

ROOT = Path(__file__).resolve().parents[3]
DEAD_TARGET = "http://127.0.0.1:1/buyer"
WINDOW = 30


def _lead(lead_id: str) -> dict:
    with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
        lead = json.load(handle)
    message = lead["lcp"]["message"]
    payload = lead["lcp"]["payload"]
    message["test"] = True
    message["id"] = str(uuid4())
    message["idempotency_key"] = f"post-deadline-{lead_id}"
    payload["lead_id"] = lead_id
    payload.pop("expiry", None)  # no payload expiry: only the §9 window can expire
    return lead


class PostResponseDeadlineTests(unittest.TestCase):
    def _platform(
        self, webhook_url: str = DEAD_TARGET, **overrides: object
    ) -> Platform:
        config = PlatformConfig(
            database_path=Path(":memory:"),
            schema_root=ROOT / "schemas",
            platform_id="platform_001",
            require_auth=False,
            allow_insecure_webhooks=True,
            test_mode=True,
        )
        if overrides:
            config = config.__class__(**{**config.__dict__, **overrides})
        platform = Platform(config)
        # A direct post needs a bilateral secret to be built at all.
        platform.upsert_credential("buyer_001", hmac_secret="post-deadline-secret")
        platform.upsert_offer(
            {
                "offer_id": "post-deadline",
                "buyer_id": "buyer_001",
                "active": True,
                "vertical": "mortgage",
                "countries": ["AU"],
                "floor_price_cents": 1500,
                "currency": "AUD",
                "routing_mode": "direct",
                "webhook_url": webhook_url,
                "ping_timeout_seconds": 300,
            }
        )
        self.addCleanup(platform.close)
        return platform

    def _award(self, platform: Platform, lead_id: str) -> None:
        platform.ingest(_lead(lead_id), headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        platform.process_once()
        self.assertEqual(platform.store.get_lead(lead_id)["status"], "POSTED")

    # ── SEC-12 (post axis): unacknowledged post expires the lead ────────────

    def test_unacknowledged_post_expires_lead_within_the_window(self) -> None:
        platform = self._platform()
        self._award(platform, "post-deadline-silent")

        delivery = platform.store.deliveries_for_lead("post-deadline-silent")[-1]
        self.assertEqual(delivery["kind"], "post")
        self.assertIn(delivery["status"], {"PENDING", "RETRY"})

        # Inside the window: nothing happens.
        platform._expire_unacknowledged_posts(
            now=datetime.now(timezone.utc) + timedelta(seconds=WINDOW - 5)
        )
        self.assertEqual(
            platform.store.get_lead("post-deadline-silent")["status"], "POSTED"
        )

        # Past the window: the lead expires.
        platform._expire_unacknowledged_posts(
            now=datetime.now(timezone.utc) + timedelta(seconds=WINDOW + 5)
        )
        self.assertEqual(
            platform.store.get_lead("post-deadline-silent")["status"], "EXPIRED"
        )

        updated = [
            row
            for row in platform.store.deliveries_for_lead("post-deadline-silent")
            if row["delivery_id"] == delivery["delivery_id"]
        ][0]
        self.assertEqual(updated["status"], "FAILED")
        self.assertEqual(updated["last_error"], "Post response timeout")

        dead = platform.store.list_dead_letters(status="OPEN")
        self.assertTrue(any(d["resource_id"] == delivery["delivery_id"] for d in dead))

        audits = platform.store.list_audit_events(
            resource_type="lead", resource_id="post-deadline-silent"
        )
        self.assertTrue(
            any(a["action"] == "lead.post_response_timeout" for a in audits)
        )

    def test_acknowledged_post_is_never_expired_by_the_sweep(self) -> None:
        received: list[str] = []

        class BuyerHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                message = json.loads(self.rfile.read(length))
                received.append(message["lcp"]["message"]["type"])
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), BuyerHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            platform = self._platform(
                webhook_url=f"http://127.0.0.1:{server.server_port}/webhook"
            )
            self._award(platform, "post-deadline-acked")
            self.assertIn("post", received)

            deliveries = platform.store.deliveries_for_lead("post-deadline-acked")
            post = [d for d in deliveries if d["kind"] == "post"][-1]
            self.assertEqual(post["status"], "DELIVERED")

            platform._expire_unacknowledged_posts(
                now=datetime.now(timezone.utc) + timedelta(seconds=WINDOW * 10)
            )
            self.assertEqual(
                platform.store.get_lead("post-deadline-acked")["status"], "POSTED"
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    # ── CAP-08: the window is advertised and configurable ───────────────────

    def test_capabilities_advertise_the_default_window(self) -> None:
        platform = self._platform()
        self.assertEqual(platform.capabilities()["post_response_timeout_seconds"], 30.0)

    def test_configured_window_is_advertised_and_enforced(self) -> None:
        platform = self._platform(post_response_timeout_seconds=2.0)
        self.assertEqual(platform.capabilities()["post_response_timeout_seconds"], 2.0)
        self._award(platform, "post-deadline-short")
        platform._expire_unacknowledged_posts(
            now=datetime.now(timezone.utc) + timedelta(seconds=5)
        )
        self.assertEqual(
            platform.store.get_lead("post-deadline-short")["status"], "EXPIRED"
        )


if __name__ == "__main__":
    unittest.main()
