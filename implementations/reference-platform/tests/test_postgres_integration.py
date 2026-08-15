from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import unittest
from uuid import uuid4

from lcp_platform.auth import signature_for
from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform
from lcp_platform.service import PlatformService
from lcp_platform.storage import now_iso


ROOT = Path(__file__).resolve().parents[3]
POSTGRES_URL = os.environ.get("LCP_TEST_POSTGRES_URL")
TEST_ENCRYPTION_KEY = base64.urlsafe_b64encode(b"lcp-postgres-integration-key-32!").decode()


@unittest.skipUnless(
    POSTGRES_URL,
    "Set LCP_TEST_POSTGRES_URL to run the real Postgres integration test",
)
class PostgresIntegrationTests(unittest.TestCase):
    """Run against a disposable Postgres database, never the SQLite fallback."""

    def setUp(self) -> None:
        suffix = uuid4().hex[:12]
        self.platform_id = f"pg_platform_{suffix}"
        self.routing_tenant_id = f"pg_tenant_{suffix}"
        self.publisher_id = f"pg_publisher_{suffix}"
        self.buyer_id = f"pg_buyer_{suffix}"
        self.publisher_secret = "publisher-integration-secret-32-bytes"
        self.buyer_secret = "buyer-integration-secret-32-bytes"
        self.platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                database_url=POSTGRES_URL,
                schema_root=ROOT / "schemas",
                platform_id=self.platform_id,
                routing_tenant_id=self.routing_tenant_id,
                require_auth=True,
                pii_encryption_key=TEST_ENCRYPTION_KEY,
                test_mode=True,
                allow_insecure_webhooks=True,
            )
        )
        self.platform.upsert_credential(
            self.publisher_id,
            tenant_id=f"publisher_tenant_{suffix}",
            scopes=["lead:submit", "lead:read"],
            hmac_secret=self.publisher_secret,
        )
        self.platform.upsert_credential(
            self.buyer_id,
            tenant_id=f"buyer_tenant_{suffix}",
            scopes=["bid:submit", "lead:read"],
            hmac_secret=self.buyer_secret,
        )
        self.platform.upsert_offer(
            {
                "offer_id": f"pg-direct-{suffix}",
                "buyer_id": self.buyer_id,
                "tenant_id": self.routing_tenant_id,
                "active": True,
                "routing_mode": "direct",
                "vertical": "mortgage",
                "countries": ["AU"],
                "floor_price_cents": 1500,
                "currency": "AUD",
                "require_consent_evidence": True,
                "webhook_url": "http://127.0.0.1:9/test-webhook",
            }
        )

    def tearDown(self) -> None:
        self.platform.close()

    def _lead(self) -> dict:
        with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
            lead = json.load(handle)
        message = lead["lcp"]["message"]
        payload = lead["lcp"]["payload"]
        message["id"] = str(uuid4())
        message["timestamp"] = now_iso()
        message["sender_id"] = self.publisher_id
        message["receiver_id"] = self.platform_id
        message["idempotency_key"] = f"{self.publisher_id}-lead-{uuid4().hex}"
        message["test"] = True
        payload["lead_id"] = f"pg-lead-{uuid4().hex}"
        return lead

    def _post_lead(self, lead: dict) -> tuple[dict, bytes]:
        body = json.dumps(lead, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        message = lead["lcp"]["message"]
        timestamp = message["timestamp"]
        idempotency_key = message["idempotency_key"]
        headers = {
            "Content-Type": "application/json",
            "X-LCP-Sender-Id": self.publisher_id,
            "X-LCP-Timestamp": timestamp,
            "X-LCP-Idempotency-Key": idempotency_key,
            "X-LCP-Signature": signature_for(
                self.publisher_secret, timestamp, idempotency_key, body
            ),
            "X-LCP-Test": "true",
        }
        status, _, response = PlatformService(self.platform).dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )
        self.assertEqual(status, 200, response)
        return response, body

    def test_postgres_encrypted_intake_idempotency_and_erasure(self) -> None:
        lead = self._lead()
        response, body = self._post_lead(lead)
        lead_id = lead["lcp"]["payload"]["lead_id"]
        self.assertEqual(response["lcp"]["payload"]["status"], "RECEIVED")

        row = self.platform.store.get_lead(lead_id)
        self.assertIsNotNone(row)
        self.assertTrue(row["envelope_json"].startswith("enc:v1:"))
        self.assertNotIn("jane.smith@example.com", row["envelope_json"])
        self.assertEqual(self.platform.store.decode_envelope(row["envelope_json"])["lcp"]["payload"]["lead_id"], lead_id)

        duplicate, _ = self._post_lead(lead)
        self.assertEqual(duplicate["lcp"]["payload"]["status"], "DUPLICATE")
        self.assertEqual(self.platform.lead_status(lead_id)["status"], "POSTED")

        self.platform.erase_lead(lead_id, actor_id="postgres-integration-test")
        erased = self.platform.store.get_lead(lead_id)
        self.assertEqual(erased["status"], "ERASED")
        self.assertNotIn("jane.smith@example.com", erased["envelope_json"])
        self.assertNotIn("consumer", self.platform.store.decode_envelope(erased["envelope_json"])["lcp"]["payload"])


if __name__ == "__main__":
    unittest.main()
