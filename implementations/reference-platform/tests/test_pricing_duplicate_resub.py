"""SPEC §4: `pricing.is_duplicate_resub` distinguishes an accidental
resubmission from the exclusivity/shared-slot axis (issue #177, maintainer
ruling A: delivery-side prior-contact dedup keyed on the lead + buyer pair).

The flag is computed at the post build: true only when this buyer has already
been delivered this lead. A second *buyer* on a shared lead is not a
duplicate resubmission, and a first delivery reports false.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from lcp_platform.auth import signature_for
from lcp_platform.config import PlatformConfig
from lcp_platform.messages import build_post
from lcp_platform.router import Platform
from lcp_platform.storage import now_iso

ROOT = Path(__file__).resolve().parents[3]
PUBLISHER_SECRET = "dup-resub-publisher-secret"
BUYER_SECRET = "dup-resub-buyer-secret"
_NAMESPACE = uuid5(NAMESPACE_URL, "lcp-dup-resub")


def _compact(envelope: dict) -> bytes:
    return json.dumps(envelope, separators=(",", ":")).encode()


def _lead(lead_id: str) -> dict:
    with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
        lead = json.load(handle)
    message = lead["lcp"]["message"]
    message["id"] = str(uuid5(_NAMESPACE, lead_id))
    message["test"] = True
    message["idempotency_key"] = f"dup-{lead_id}"
    lead["lcp"]["payload"]["lead_id"] = lead_id
    lead["lcp"]["payload"].pop("expiry", None)
    return lead


class DuplicateResubmissionTests(unittest.TestCase):
    def _platform(self) -> Platform:
        platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=True,
                allow_insecure_webhooks=True,
                test_mode=True,
            )
        )
        self.addCleanup(platform.close)
        platform.upsert_credential(
            "pub_123", tenant_id="tenant_a", scopes=["lead:submit"], hmac_secret=PUBLISHER_SECRET
        )
        platform.upsert_credential(
            "buyer_001", tenant_id="tenant_a", scopes=["bid:submit"], hmac_secret=BUYER_SECRET
        )
        platform.upsert_credential(
            "buyer_002", tenant_id="tenant_a", scopes=["bid:submit"], hmac_secret="second-buyer-secret"
        )
        return platform

    def _ingest(self, platform: Platform, lead_id: str) -> None:
        envelope = _lead(lead_id)
        body = _compact(envelope)
        message = envelope["lcp"]["message"]
        timestamp = now_iso()
        headers = {
            "Content-Type": "application/json",
            "X-LCP-Test": "true",
            "X-LCP-Sender-Id": message["sender_id"],
            "X-LCP-Timestamp": timestamp,
            "X-LCP-Idempotency-Key": message["idempotency_key"],
            "X-LCP-Signature": signature_for(
                PUBLISHER_SECRET, timestamp, message["idempotency_key"], body
            ),
        }
        platform.ingest(envelope, headers=headers, raw_body=body)

    def test_store_helper_tracks_prior_post_deliveries_per_pair(self) -> None:
        platform = self._platform()
        self._ingest(platform, "dup-lead-1")
        self.assertFalse(
            platform.store.has_prior_post_delivery("dup-lead-1", "buyer_001")
        )
        platform.store.insert_delivery(
            lead_id="dup-lead-1",
            ping_id=None,
            offer_id="offer-1",
            buyer_id="buyer_001",
            kind="post",
            webhook_url="http://127.0.0.1:1/x",
            envelope={
                "lcp": {
                    "message": {"id": "prior-delivery-1", "idempotency_key": "pd-1"},
                    "payload": {"lead_id": "dup-lead-1"},
                }
            },
        )
        self.assertTrue(
            platform.store.has_prior_post_delivery("dup-lead-1", "buyer_001")
        )
        # A different buyer on the same lead is not a duplicate resubmission.
        self.assertFalse(
            platform.store.has_prior_post_delivery("dup-lead-1", "buyer_002")
        )

    def test_build_post_emits_the_flag(self) -> None:
        platform = self._platform()
        self._ingest(platform, "dup-lead-2")
        lead = platform.store.get_lead("dup-lead-2")
        envelope = platform.store.decode_envelope(lead["envelope_json"])
        offer = {
            "offer_id": "offer-1",
            "buyer_id": "buyer_001",
            "active": True,
            "vertical": "mortgage",
            "countries": ["AU"],
            "floor_price_cents": 1500,
            "currency": "AUD",
            "require_consent_evidence": True,
            "webhook_url": "http://127.0.0.1:1/x",
            "ping_timeout_seconds": 300,
        }
        first = build_post(
            envelope,
            offer,
            platform_id="platform_001",
            price_cents=1500,
            buyer_reference=None,
            correlation_id="c-1",
        )
        self.assertFalse(first["lcp"]["payload"]["pricing"]["is_duplicate_resub"])
        second = build_post(
            envelope,
            offer,
            platform_id="platform_001",
            price_cents=1500,
            buyer_reference=None,
            correlation_id="c-2",
            duplicate_resubmission=True,
        )
        self.assertTrue(second["lcp"]["payload"]["pricing"]["is_duplicate_resub"])


if __name__ == "__main__":
    unittest.main()
