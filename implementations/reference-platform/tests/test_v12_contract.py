"""v1.2 contract: PROCESSED acks, FX comparability, ping extension admission.

Decision record: docs/DECISIONS-v1.2.md D2 (PROCESSED), D3
(core_schema_version), D7 (multi-currency auction), W1-05 (ping-safe
assessment extension), D4/D5 (street_address / ip_address are post-only).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest
from typing import Any
from uuid import uuid4

from lcp_platform.config import PlatformConfig
from lcp_platform.messages import _envelope
from lcp_platform.router import Platform, RequestError
from lcp_platform.storage import now_iso
from lcp_platform.validation import ValidationError

ROOT = Path(__file__).resolve().parents[3]
PLATFORM_ID = "platform_001"
PUBLISHER_ID = "pub_123"
BUYER_ID = "buyer_001"


def _compact(envelope: dict[str, Any]) -> bytes:
    return json.dumps(envelope, separators=(",", ":")).encode()


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class V12ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id=PLATFORM_ID,
                require_auth=False,
                allow_insecure_webhooks=True,
                test_mode=True,
            )
        )
        self.platform.upsert_credential(
            BUYER_ID, hmac_secret="buyer-v12-secret", scopes=[]
        )

    def tearDown(self) -> None:
        self.platform.close()

    # ─── helpers ───────────────────────────────────────────────────────────

    def lead(self) -> dict[str, Any]:
        with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
            lead = json.load(handle)
        message = lead["lcp"]["message"]
        message["test"] = True
        message["id"] = str(uuid4())
        message["sender_id"] = PUBLISHER_ID
        message["idempotency_key"] = f"pub-v12-{uuid4().hex}"
        message["correlation_id"] = None
        lead["lcp"]["payload"]["lead_id"] = f"lead_{uuid4().hex[:12]}"
        lead["lcp"]["payload"].pop("expiry", None)
        return lead

    def offer(self, **overrides: Any) -> dict[str, Any]:
        offer = {
            "offer_id": f"offer_{uuid4().hex[:10]}",
            "buyer_id": BUYER_ID,
            "active": True,
            "vertical": "mortgage",
            "countries": ["AU"],
            "floor_price_cents": 1500,
            "currency": "AUD",
            "webhook_url": "http://127.0.0.1:9/buyer",
            "ping_timeout_seconds": 30,
            "routing_mode": "direct",
        }
        offer.update(overrides)
        return offer

    def ingest(self, lead: dict[str, Any]) -> dict[str, Any]:
        return self.platform.ingest(
            lead, headers={"X-LCP-Test": "true"}, raw_body=_compact(lead)
        )

    def delivered_post(self) -> tuple[dict[str, Any], Any]:
        """Ingest a direct-offer lead and mark its post delivery DELIVERED."""
        self.platform.upsert_offer(self.offer())
        lead = self.lead()
        self.ingest(lead)
        lead_id = lead["lcp"]["payload"]["lead_id"]
        deliveries = [
            row
            for row in self.platform.store.deliveries_for_lead(lead_id)
            if row["kind"] == "post"
        ]
        self.assertEqual(len(deliveries), 1, deliveries)
        delivery = deliveries[0]
        self.platform.store.mark_delivery(
            delivery["delivery_id"],
            status="DELIVERED",
            attempts=1,
            next_attempt_at=now_iso(),
        )
        return lead, delivery

    def ack_envelope(
        self,
        original_message_id: str,
        *,
        lead_id: str | None,
        status: str = "PROCESSED",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "original_message_id": original_message_id,
            "status": status,
        }
        if lead_id is not None:
            payload["lead_id"] = lead_id
        return _envelope("ack", BUYER_ID, PLATFORM_ID, payload, test=True)

    def submit_ack(self, ack: dict[str, Any]) -> dict[str, Any]:
        return self.platform.submit_ack(
            ack,
            headers={"X-LCP-Test": "true"},
            raw_body=_compact(ack),
        )

    # ─── D3: capabilities ──────────────────────────────────────────────────

    def test_capabilities_advertise_v12_surfaces_and_keep_existing_keys(self) -> None:
        capabilities = self.platform.capabilities()
        self.assertEqual(
            capabilities["core_schema_version"],
            (ROOT / "SDK_VERSION").read_text(encoding="utf-8").strip(),
        )
        self.assertEqual(capabilities["exclusivity_default"], "shared")
        self.assertEqual(
            capabilities["features"],
            {"processed_ack": "1.2.0", "multi_currency": "1.2.0"},
        )
        # Existing advertisement keys are unchanged.
        for key in (
            "lcp_versions",
            "message_types",
            "verticals",
            "countries",
            "auth_methods",
            "events",
            "post_response_timeout_seconds",
            "attachments",
            "conformance_level",
        ):
            self.assertIn(key, capabilities)

    # ─── D2: PROCESSED ─────────────────────────────────────────────────────

    def test_processed_ack_accepted_for_post_and_does_not_touch_payability(
        self,
    ) -> None:
        lead, delivery = self.delivered_post()
        lead_id = lead["lcp"]["payload"]["lead_id"]
        post_message_id = delivery["message_id"]
        payables_before = list(self.platform.store.payable_for_lead(lead_id))

        response = self.submit_ack(self.ack_envelope(post_message_id, lead_id=lead_id))

        payload = response["lcp"]["payload"]
        self.assertEqual(payload["status"], "PROCESSED")
        self.assertEqual(payload["original_message_id"], post_message_id)
        self.assertEqual(payload["lead_id"], lead_id)
        self.assertEqual(
            self.platform.store.deliveries_for_lead(lead_id)[0]["status"],
            "PROCESSED",
        )
        # PROCESSED never mutates pricing or payability.
        self.assertEqual(
            list(self.platform.store.payable_for_lead(lead_id)), payables_before
        )

    def test_processed_ack_is_idempotent(self) -> None:
        lead, delivery = self.delivered_post()
        lead_id = lead["lcp"]["payload"]["lead_id"]
        first = self.submit_ack(
            self.ack_envelope(delivery["message_id"], lead_id=lead_id)
        )
        second = self.submit_ack(
            self.ack_envelope(delivery["message_id"], lead_id=lead_id)
        )
        self.assertEqual(
            first["lcp"]["payload"]["status"],
            second["lcp"]["payload"]["status"],
        )
        self.assertEqual(
            first["lcp"]["payload"]["original_message_id"],
            second["lcp"]["payload"]["original_message_id"],
        )
        audits = [
            event
            for event in self.platform.store.list_audit_events(
                "delivery", delivery["delivery_id"]
            )
            if event["action"] == "post.processed"
        ]
        self.assertEqual(len(audits), 1, audits)

    def test_processed_ack_rejected_on_a_lead(self) -> None:
        lead, _ = self.delivered_post()
        lead_id = lead["lcp"]["payload"]["lead_id"]
        ack = self.ack_envelope(lead["lcp"]["message"]["id"], lead_id=lead_id)
        with self.assertRaises(RequestError) as caught:
            self.submit_ack(ack)
        self.assertEqual(caught.exception.code, "LCP-004")
        self.assertEqual(caught.exception.status_code, 422)

    def test_processed_ack_rejected_after_post_terminal_states(self) -> None:
        lead, delivery = self.delivered_post()
        lead_id = lead["lcp"]["payload"]["lead_id"]
        for terminal in ("REJECTED", "EXPIRED", "SOLD"):
            with self.subTest(terminal=terminal):
                self.platform.store.mark_delivery(
                    delivery["delivery_id"],
                    status=terminal,
                    attempts=1,
                    next_attempt_at=now_iso(),
                )
                ack = self.ack_envelope(delivery["message_id"], lead_id=lead_id)
                with self.assertRaises(RequestError) as caught:
                    self.submit_ack(ack)
                self.assertEqual(caught.exception.code, "LCP-004")
                self.assertEqual(caught.exception.status_code, 422)

    # ─── D7: FX comparability ──────────────────────────────────────────────

    @staticmethod
    def fx_pricing(
        *,
        rate: str = "0.650000",
        source: str = "ECB",
        age_seconds: int = 0,
        ttl_seconds: int = 300,
    ) -> dict[str, str]:
        now = datetime.now(timezone.utc)
        return {
            "fx_rate": rate,
            "fx_source": source,
            "fx_timestamp": _iso(now - timedelta(seconds=age_seconds)),
            "fx_expires_at": _iso(now + timedelta(seconds=ttl_seconds)),
        }

    def price(
        self,
        amount: int,
        currency: str,
        pricing: dict[str, Any] | None = None,
        *,
        settlement: str,
        counterpart: dict[str, Any] | None = None,
    ) -> tuple[Decimal | None, str | None]:
        return self.platform._settlement_price(
            amount,
            currency,
            pricing or {},
            settlement,
            counterpart=counterpart,
        )

    def test_fx_same_currency_compares_directly(self) -> None:
        converted, reason = self.price(1500, "AUD", settlement="AUD")
        self.assertIsNone(reason)
        self.assertEqual(converted, Decimal(1500))

    def test_fx_cross_currency_valid_rate_converts_to_settlement_units(self) -> None:
        converted, reason = self.price(
            10_000, "USD", self.fx_pricing(), settlement="AUD"
        )
        self.assertIsNone(reason)
        self.assertEqual(converted, Decimal(10_000) / Decimal("0.650000"))

    def test_fx_missing_rate_is_non_comparable_with_reason(self) -> None:
        converted, reason = self.price(10_000, "USD", settlement="AUD")
        self.assertIsNone(converted)
        self.assertEqual(reason, "fx_rate_missing")

    def test_fx_stale_rate_is_non_comparable_with_reason(self) -> None:
        converted, reason = self.price(
            10_000,
            "USD",
            self.fx_pricing(age_seconds=self.platform.config.fx_max_age_seconds + 1),
            settlement="AUD",
        )
        self.assertIsNone(converted)
        self.assertEqual(reason, "fx_rate_stale")

    def test_fx_expired_rate_is_non_comparable_with_reason(self) -> None:
        converted, reason = self.price(
            10_000,
            "USD",
            self.fx_pricing(ttl_seconds=-1),
            settlement="AUD",
        )
        self.assertIsNone(converted)
        self.assertEqual(reason, "fx_rate_expired")

    def test_fx_malformed_rate_is_non_comparable_with_reason(self) -> None:
        converted, reason = self.price(
            10_000, "USD", self.fx_pricing(rate="65e-2"), settlement="AUD"
        )
        self.assertIsNone(converted)
        self.assertEqual(reason, "fx_rate_malformed")

    def test_fx_mixed_sources_are_non_comparable_with_reason(self) -> None:
        converted, reason = self.price(
            10_000,
            "USD",
            self.fx_pricing(source="ECB"),
            settlement="AUD",
            counterpart={"fx_source": "OTHER"},
        )
        self.assertIsNone(converted)
        self.assertEqual(reason, "fx_source_mixed")

    # ─── W1-05 / D7: ping extension admission ─────────────────────────────

    def ping(self, extensions: dict[str, Any] | None = None) -> dict[str, Any]:
        with (ROOT / "examples" / "ping.json").open(encoding="utf-8") as handle:
            ping = json.load(handle)
        message = ping["lcp"]["message"]
        message["test"] = True
        message["id"] = str(uuid4())
        message["idempotency_key"] = f"ping-v12-{uuid4().hex}"
        if extensions is not None:
            ping["lcp"]["payload"]["extensions"] = extensions
        return ping

    def test_ping_admits_registered_ping_safe_assessment_extension(self) -> None:
        ping = self.ping(
            {
                "lcp.platform.assessment": {
                    "score": 87,
                    "model_edition": "2026-11",
                    "model_version": "1.0.0",
                    "reasons": ["verified_phone"],
                }
            }
        )
        self.assertEqual(self.platform.validator.validate_envelope(ping), [])

    def test_ping_refuses_post_only_and_unregistered_extensions_by_name(self) -> None:
        for namespace in (
            "lcp.intake.raw_form",
            "lcp.form.questions",
            "acme.unregistered.namespace",
        ):
            with self.subTest(namespace=namespace):
                ping = self.ping({namespace: {"value": "x"}})
                with self.assertRaises(ValidationError) as caught:
                    self.platform.validator.require_valid_envelope(ping)
                self.assertIn(namespace, str(caught.exception))

    # ─── D4/D5: post-only fields ───────────────────────────────────────────

    def test_street_address_and_ip_address_rejected_in_ping(self) -> None:
        base = self.ping()
        with_location = json.loads(json.dumps(base))
        with_location["lcp"]["payload"]["location"] = {
            "country_code": "AU",
            "state_region": "NSW",
            "street_address": "1 Example St",
        }
        with self.assertRaises(ValidationError) as caught:
            self.platform.validator.require_valid_envelope(with_location)
        self.assertIn("location", str(caught.exception))

        with_provenance = json.loads(json.dumps(base))
        with_provenance["lcp"]["payload"]["provenance"] = {"ip_address": "203.0.113.7"}
        with self.assertRaises(ValidationError) as caught:
            self.platform.validator.require_valid_envelope(with_provenance)
        self.assertIn("provenance", str(caught.exception))

    def test_street_address_and_ip_address_accepted_on_lead_and_post(self) -> None:
        lead = self.lead()
        lead["lcp"]["payload"]["location"]["street_address"] = "1 Example St"
        lead["lcp"]["payload"]["provenance"]["ip_address"] = "203.0.113.7"
        self.assertEqual(self.platform.validator.validate_envelope(lead), [])

        with (ROOT / "examples" / "post.json").open(encoding="utf-8") as handle:
            post = json.load(handle)
        post["lcp"]["payload"]["location"]["street_address"] = "1 Example St"
        post["lcp"]["payload"]["provenance"]["ip_address"] = "203.0.113.7"
        self.assertEqual(self.platform.validator.validate_envelope(post), [])


if __name__ == "__main__":
    unittest.main()
