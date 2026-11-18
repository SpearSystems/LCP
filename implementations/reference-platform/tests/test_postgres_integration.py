from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import threading
import time
import unittest
from uuid import uuid4

from lcp_platform.auth import signature_for
from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform
from lcp_platform.service import PlatformService
from lcp_platform.messages import build_event
from lcp_platform.storage import EventIdempotencyConflict, now_iso


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
        self.direct_offer_id = f"pg-direct-{suffix}"
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
                "offer_id": self.direct_offer_id,
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
        # The checked-in example intentionally demonstrates an historical
        # expiry. Non-expiry tests pop it so the lead is accepted at intake.
        payload.pop("expiry", None)
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

    def test_postgres_offer_discovery_quota_and_candidate_filtering(self) -> None:
        self.platform.store.upsert_offer(
            {
                "offer_id": f"pg-other-tenant-{self.direct_offer_id}",
                "buyer_id": self.buyer_id,
                "tenant_id": "other-tenant",
                "active": True,
                "vertical": "mortgage",
                "countries": ["AU"],
            }
        )
        self.platform.store.upsert_offer(
            {
                "offer_id": f"pg-other-vertical-{self.direct_offer_id}",
                "buyer_id": self.buyer_id,
                "tenant_id": self.routing_tenant_id,
                "active": True,
                "vertical": "solar",
                "countries": ["AU"],
            }
        )
        self.platform.store.upsert_offer(
            {
                "offer_id": f"pg-inactive-{self.direct_offer_id}",
                "buyer_id": self.buyer_id,
                "tenant_id": self.routing_tenant_id,
                "active": False,
                "vertical": "mortgage",
                "countries": ["AU"],
            }
        )

        tenant_offers = self.platform.store.list_offers(tenant_id=self.routing_tenant_id)
        tenant_ids = {offer["offer_id"] for offer in tenant_offers}
        self.assertIn(self.direct_offer_id, tenant_ids)
        self.assertIn(f"pg-other-vertical-{self.direct_offer_id}", tenant_ids)
        self.assertNotIn(f"pg-other-tenant-{self.direct_offer_id}", tenant_ids)
        self.assertNotIn(f"pg-inactive-{self.direct_offer_id}", tenant_ids)
        self.assertEqual(
            [offer["offer_id"] for offer in self.platform.store.list_offers(
                vertical="mortgage", tenant_id=self.routing_tenant_id
            )],
            [self.direct_offer_id],
        )

        quota = self.platform.quota_status(self.direct_offer_id)
        self.assertEqual(quota["offer_id"], self.direct_offer_id)
        self.assertEqual(quota["summary"]["total"], 0)
        payload = self._lead()["lcp"]["payload"]
        candidates = self.platform.store.list_offer_candidates(
            payload, tenant_id=self.routing_tenant_id
        )
        self.assertIn(self.direct_offer_id, {offer["offer_id"] for offer in candidates})

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


# The event_subscriptions table as v1.2 created it, before LEP-0012 added
# `delivery`. A v1.3 store must migrate it in place on startup.
_V12_EVENT_SUBSCRIPTIONS = """
    CREATE TABLE event_subscriptions (
        subscription_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        subscriber_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        event_types_json TEXT NOT NULL,
        callback_url TEXT NOT NULL,
        filter_json TEXT NOT NULL,
        secret_cipher TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        replay_from TEXT,
        cursor_sequence INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(subscriber_id, idempotency_key)
    )
"""


@unittest.skipUnless(
    POSTGRES_URL,
    "Set LCP_TEST_POSTGRES_URL to run the real Postgres migration tests",
)
class PostgresSaleLifecycleTests(unittest.TestCase):
    """LEP-0012 storage on real Postgres, each test in its own schema."""

    def setUp(self) -> None:
        import psycopg
        from psycopg.conninfo import make_conninfo

        from lcp_platform.postgres import PostgresStore

        self.psycopg = psycopg
        self.store_class = PostgresStore
        self.schema = f"lcp_sale_{uuid4().hex[:12]}"
        with psycopg.connect(POSTGRES_URL, autocommit=True) as admin:
            admin.execute(f'CREATE SCHEMA "{self.schema}"')
        self.addCleanup(self._drop_schema)
        self.dsn = make_conninfo(POSTGRES_URL, options=f"-c search_path={self.schema}")

    def _drop_schema(self) -> None:
        with self.psycopg.connect(POSTGRES_URL, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')

    def _store(self):
        store = self.store_class(self.dsn, pii_encryption_key=TEST_ENCRYPTION_KEY)
        self.addCleanup(store.close)
        return store

    def test_v12_subscriptions_gain_delivery_and_payables_gain_a_lead_index(self) -> None:
        stamp = now_iso()
        with self.psycopg.connect(self.dsn, autocommit=True) as old:
            old.execute(_V12_EVENT_SUBSCRIPTIONS)
            old.execute(
                "INSERT INTO event_subscriptions VALUES "
                "('sub_v12aaaaaaaa', 't', 'pub', 'k', '[]', 'https://example.com/h', "
                "'{}', 'x', 1, NULL, 0, %s, %s)",
                (stamp, stamp),
            )
        self._store()
        with self.psycopg.connect(self.dsn) as check:
            delivery = check.execute(
                "SELECT delivery FROM event_subscriptions WHERE subscription_id = 'sub_v12aaaaaaaa'"
            ).fetchone()[0]
            indexes = {
                row[0]
                for row in check.execute(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = %s",
                    (self.schema,),
                )
            }
        self.assertEqual(delivery, "json")
        self.assertIn("idx_payable_lead", indexes)
        self.assertIn("idx_sale_result_jobs_due", indexes)

    def test_concurrent_insert_of_one_event_id_is_judged_not_raised(self) -> None:
        store = self._store()
        message_id = str(uuid4())

        def event(payout: int) -> dict:
            return build_event(
                "lead_pg_race",
                "ACCEPTED",
                platform_id="platform_pg",
                receiver_id="pub_pg",
                details={"payout_cents": payout, "currency": "USD", "revision": 1},
                test=True,
                message_id=message_id,
            )

        for racer_event, expected in ((event(4450), False), (event(9999), EventIdempotencyConflict)):
            with self.subTest(expected=expected):
                store_event = event(4450)
                with self.psycopg.connect(self.dsn) as racer:
                    # An uncommitted insert of the same id: the store's check
                    # misses it, so its own insert waits on the unique index.
                    racer.execute(
                        "DELETE FROM lifecycle_events WHERE event_id = %s", (message_id,)
                    )
                    racer.commit()
                    racer.execute(
                        "INSERT INTO lifecycle_events "
                        "(event_id, lead_id, event_name, timestamp, envelope_json) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (
                            message_id,
                            "lead_pg_race",
                            "ACCEPTED",
                            racer_event["lcp"]["payload"]["timestamp"],
                            store.encode_envelope(racer_event),
                        ),
                    )
                    outcome: dict = {}

                    def insert() -> None:
                        try:
                            outcome["value"] = store.insert_event(
                                "lead_pg_race", "ACCEPTED", store_event
                            )
                        except Exception as exc:  # noqa: BLE001 - asserted below
                            outcome["value"] = exc

                    thread = threading.Thread(target=insert)
                    thread.start()
                    time.sleep(0.5)
                    # Still blocked on the racer's row: it reaches the unique
                    # violation, not the pre-check.
                    self.assertTrue(thread.is_alive())
                    racer.commit()
                    thread.join(timeout=10)
                if expected is False:
                    self.assertIs(outcome["value"], False)
                else:
                    self.assertIsInstance(outcome["value"], expected)


if __name__ == "__main__":
    unittest.main()
