from __future__ import annotations

import json
from pathlib import Path
import unittest

from lcp_platform.api_keys import API_KEY_PBKDF2_ITERATIONS, hash_api_key, verify_api_key
from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform
from lcp_platform.service import PlatformService
from lcp_platform.storage import Store


ROOT = Path(__file__).resolve().parents[3]


class ApiKeyHashingTests(unittest.TestCase):
    def test_hash_and_verify_round_trip(self) -> None:
        salt, digest = hash_api_key("secret-api-key")
        self.assertTrue(salt)
        self.assertTrue(digest)
        self.assertTrue(verify_api_key("secret-api-key", salt, digest))
        self.assertFalse(verify_api_key("wrong-api-key", salt, digest))

    def test_each_credential_gets_its_own_salt(self) -> None:
        first_salt, first_digest = hash_api_key("same-key")
        second_salt, second_digest = hash_api_key("same-key")
        self.assertNotEqual(first_salt, second_salt)
        self.assertNotEqual(first_digest, second_digest)

    def test_missing_salt_or_digest_never_verifies(self) -> None:
        self.assertFalse(verify_api_key("key", None, None))
        self.assertFalse(verify_api_key("key", "salt", None))
        self.assertFalse(verify_api_key("key", None, "digest"))

    def test_digest_is_not_plain_sha256(self) -> None:
        import hashlib

        salt, digest = hash_api_key("secret-api-key")
        self.assertNotEqual(digest, hashlib.sha256(b"secret-api-key").hexdigest())
        self.assertEqual(len(digest), 64)
        self.assertNotEqual(digest, salt)


class ApiKeyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(Path(":memory:"))

    def tearDown(self) -> None:
        self.store.close()

    def test_find_sender_for_api_key_round_trip(self) -> None:
        self.store.upsert_credential("pub_001", api_key="super-secret-key")
        self.assertEqual(self.store.find_sender_for_api_key("super-secret-key"), "pub_001")
        self.assertIsNone(self.store.find_sender_for_api_key("wrong-key"))

    def test_stored_digest_is_salted(self) -> None:
        import hashlib

        self.store.upsert_credential("pub_001", api_key="super-secret-key")
        row = self.store.get_credential("pub_001")
        self.assertTrue(row["api_key_salt"])
        self.assertNotEqual(
            row["api_key_hash"], hashlib.sha256(b"super-secret-key").hexdigest()
        )

    def test_upsert_rehashes_and_old_key_stops_working(self) -> None:
        self.store.upsert_credential("pub_001", api_key="first-key")
        self.assertEqual(self.store.find_sender_for_api_key("first-key"), "pub_001")
        self.store.upsert_credential("pub_001", api_key="rotated-key")
        self.assertIsNone(self.store.find_sender_for_api_key("first-key"))
        self.assertEqual(self.store.find_sender_for_api_key("rotated-key"), "pub_001")


class ApiKeyAuthFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=True,
                test_mode=True,
            )
        )

    def tearDown(self) -> None:
        self.platform.close()

    def load_lead(self) -> dict:
        with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
            lead = json.load(handle)
        lead["lcp"]["message"]["test"] = True
        lead["lcp"]["message"]["id"] = "550e8400-e29b-41d4-a716-446655440099"
        lead["lcp"]["message"]["idempotency_key"] = "bearer-test-001"
        lead["lcp"]["payload"]["lead_id"] = "lead_bearer_001"
        return lead

    def test_bearer_api_key_authenticates_and_resolves_sender(self) -> None:
        # The credential is keyed by the envelope sender (pub_123 in the
        # canonical fixture); no X-LCP-Sender-Id header is sent on either
        # request.
        self.platform.upsert_credential("pub_123", api_key="api-key-abc-123")
        body = json.dumps(self.load_lead(), separators=(",", ":")).encode()
        post_headers = {
            "Authorization": "Bearer api-key-abc-123",
            "Content-Type": "application/json",
            "X-LCP-Idempotency-Key": "bearer-test-001",
            "X-LCP-Test": "true",
        }
        status, _, response = PlatformService(self.platform).dispatch(
            "POST", "/v1/lcp/leads", headers=post_headers, body=body
        )
        self.assertEqual(status, 200, response)
        self.assertEqual(response["lcp"]["payload"]["status"], "RECEIVED")
        # The read path has no envelope sender, so the sender must be resolved
        # from the API key alone via find_sender_for_api_key.
        status, _, response = PlatformService(self.platform).dispatch(
            "GET",
            "/v1/lcp/leads/lead_bearer_001",
            headers={"Authorization": "Bearer api-key-abc-123"},
        )
        self.assertEqual(status, 200, response)
        self.assertEqual(response["lead_id"], "lead_bearer_001")

    def test_bearer_with_wrong_api_key_is_rejected(self) -> None:
        self.platform.upsert_credential("pub_123", api_key="api-key-abc-123")
        body = json.dumps(self.load_lead(), separators=(",", ":")).encode()
        headers = {
            "Authorization": "Bearer api-key-wrong",
            "Content-Type": "application/json",
            "X-LCP-Test": "true",
        }
        status, _, response = PlatformService(self.platform).dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )
        self.assertEqual(status, 401)
        self.assertEqual(response["errors"][0]["code"], "LCP-012")


if __name__ == "__main__":
    unittest.main()
