"""LEP-0001 / execution plan W2: bounded batch submission.

Wire shape pinned to the LEP's "Batch submission" section: `batch` is a
message type whose payload carries `request_id` and 1..100 items of
`{item_id, idempotency_key, payload}`, where each `payload` is a lead payload
(the `lead.json` root object, not an envelope). The platform synthesizes the
per-item lead envelope from the batch envelope and runs the single-lead path,
so per-item validation, idempotency, throttling and audit are the single-lead
path's own; these tests pin the batch-specific contract instead:

* request-level refusals only for wrapper-level problems (shape, auth, caps),
* one receipt per item in input order, under the closed receipt vocabulary,
* no cross-item transaction — a rejected item leaves its siblings persisted,
* per-item idempotency replay returning the item's original receipt,
* tenant authorization (one authenticated principal per batch), and
* the ``LCP_FEATURE_BATCH`` advertisement gate (off by default).
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from lcp_platform.auth import signature_for
from lcp_platform.config import PlatformConfig
from lcp_platform.router import BATCH_MAX_BODY_BYTES, BATCH_MAX_ITEMS, Platform
from lcp_platform.service import BATCH_ROUTE, PlatformService
from lcp_platform.storage import now_iso

ROOT = Path(__file__).resolve().parents[3]
PUBLISHER_SECRET = "batch-publisher-secret"
BUYER_SECRET = "batch-buyer-secret"
_NAMESPACE = uuid5(NAMESPACE_URL, "lcp-lep0001-batch")
_EXAMPLE_LEAD = json.loads(
    (ROOT / "examples" / "lead.json").read_text(encoding="utf-8")
)


def _compact(envelope: dict[str, Any]) -> bytes:
    return json.dumps(envelope, separators=(",", ":")).encode()


def _payload(lead_id: str) -> dict[str, Any]:
    """An ordinary lead payload — the `lead.json` root object."""
    payload = copy.deepcopy(_EXAMPLE_LEAD["lcp"]["payload"])
    payload["lead_id"] = lead_id
    # The checked-in example demonstrates a historical expiry; these tests are
    # about intake, so every item is live.
    payload.pop("expiry", None)
    return payload


def _item(
    lead_id: str, *, item_id: str | None = None, key: str | None = None
) -> dict[str, Any]:
    return {
        "item_id": item_id or f"item-{lead_id}",
        "idempotency_key": key or f"batch-{lead_id}",
        "payload": _payload(lead_id),
    }


def _batch(
    items: list[Any],
    *,
    sender: str = "pub_123",
    key: str = "batch-key",
    request_id: str | None = "req-batch",
) -> dict[str, Any]:
    payload: dict[str, Any] = {"items": items}
    if request_id is not None:
        payload["request_id"] = request_id
    return {
        "lcp": {
            "version": "1.2.0",
            "message": {
                "id": str(uuid5(_NAMESPACE, f"batch/{sender}/{key}")),
                "type": "batch",
                "timestamp": now_iso(),
                "sender_id": sender,
                "receiver_id": "platform_001",
                "correlation_id": None,
                "idempotency_key": key,
                "test": True,
            },
            "payload": payload,
        }
    }


def _headers(envelope: dict[str, Any], *, secret: str | None = None) -> dict[str, str]:
    message = envelope["lcp"]["message"]
    headers = {
        "Content-Type": "application/json",
        "X-LCP-Test": "true",
        "X-LCP-Sender-Id": message["sender_id"],
        "X-LCP-Idempotency-Key": message["idempotency_key"],
    }
    if secret is not None:
        timestamp = now_iso()
        headers["X-LCP-Timestamp"] = timestamp
        headers["X-LCP-Signature"] = signature_for(
            secret, timestamp, message["idempotency_key"], _compact(envelope)
        )
    return headers


class BatchSubmissionTests(unittest.TestCase):
    def _platform(
        self, *, require_auth: bool = False, feature_batch: bool = True
    ) -> Platform:
        platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=require_auth,
                allow_insecure_webhooks=True,
                test_mode=True,
                feature_batch=feature_batch,
            )
        )
        self.addCleanup(platform.close)
        if require_auth:
            platform.upsert_credential(
                "pub_123",
                tenant_id="tenant_a",
                scopes=["lead:submit"],
                hmac_secret=PUBLISHER_SECRET,
            )
            platform.upsert_credential(
                "buyer_7",
                tenant_id="tenant_b",
                scopes=["lead:submit"],
                hmac_secret=BUYER_SECRET,
            )
        return platform

    def _post(
        self, platform: Platform, envelope: dict[str, Any], *, secret: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        status, _headers_out, payload = PlatformService(platform).dispatch(
            "POST",
            BATCH_ROUTE,
            headers=_headers(envelope, secret=secret),
            body=_compact(envelope),
        )
        return status, payload

    # ─── Happy path ────────────────────────────────────────────────────────

    def test_all_valid_batch_returns_receipts_in_input_order(self) -> None:
        platform = self._platform()
        items = [_item("lead_a"), _item("lead_b"), _item("lead_c")]

        status, payload = self._post(
            platform, _batch(items, key="batch-all-valid", request_id="req-batch-1")
        )

        self.assertEqual(status, 207)
        self.assertEqual(payload["request_id"], "req-batch-1")
        self.assertEqual(
            [receipt["item_id"] for receipt in payload["results"]],
            ["item-lead_a", "item-lead_b", "item-lead_c"],
        )
        self.assertEqual(
            [receipt["status"] for receipt in payload["results"]],
            ["accepted", "accepted", "accepted"],
        )
        self.assertEqual(
            [receipt["lead_id"] for receipt in payload["results"]],
            ["lead_a", "lead_b", "lead_c"],
        )
        self.assertEqual(
            payload["summary"],
            {"total": 3, "accepted": 3, "duplicate": 0, "rejected": 0, "error": 0},
        )
        # The receipt is not an ack: no envelope, no ack.status vocabulary, and
        # the item's idempotency key is not echoed (the LEP receipt carries
        # item_id, status, lead_id, and error only).
        self.assertNotIn("lcp", payload["results"][0])
        self.assertNotIn("idempotency_key", payload["results"][0])
        for lead_id in ("lead_a", "lead_b", "lead_c"):
            self.assertIsNotNone(platform.store.get_lead(lead_id), lead_id)

    def test_synthesized_item_envelope_keeps_batch_identity(self) -> None:
        platform = self._platform()
        batch = _batch([_item("lead_identity", key="item-key-identity")])
        status, payload = self._post(platform, batch)
        self.assertEqual(status, 207)
        self.assertEqual(payload["results"][0]["status"], "accepted")

        stored = platform.store.get_lead("lead_identity")
        assert stored is not None
        envelope = json.loads(stored["envelope_json"])
        message = envelope["lcp"]["message"]
        # Same sender/receiver/timestamp/test as the batch, the item's own key,
        # and a deterministic id derived from the batch id + item id.
        self.assertEqual(message["sender_id"], batch["lcp"]["message"]["sender_id"])
        self.assertEqual(message["receiver_id"], "platform_001")
        self.assertEqual(message["timestamp"], batch["lcp"]["message"]["timestamp"])
        self.assertTrue(message["test"])
        self.assertEqual(message["type"], "lead")
        self.assertEqual(message["idempotency_key"], "item-key-identity")
        self.assertEqual(
            message["id"],
            str(
                uuid5(
                    NAMESPACE_URL,
                    f"{batch['lcp']['message']['id']}/item-lead_identity",
                )
            ),
        )
        self.assertEqual(envelope["lcp"]["payload"]["lead_id"], "lead_identity")

    def test_invalid_item_does_not_fail_siblings(self) -> None:
        platform = self._platform()
        invalid = _item("lead_bad")
        invalid["payload"].pop("channel")  # lead schema: required

        status, payload = self._post(
            platform, _batch([invalid, _item("lead_ok")], key="batch-partial")
        )

        self.assertEqual(status, 207)
        rejected, accepted = payload["results"]
        self.assertEqual(rejected["item_id"], "item-lead_bad")
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["lead_id"], "lead_bad")
        self.assertEqual(rejected["error"]["code"], "LCP-100")
        self.assertTrue(rejected["error"]["details"])
        self.assertEqual(accepted["status"], "accepted")
        self.assertNotIn("error", accepted)
        self.assertEqual(
            payload["summary"],
            {"total": 2, "accepted": 1, "duplicate": 0, "rejected": 1, "error": 0},
        )
        # No cross-item transaction: the rejected item stores nothing and the
        # sibling it rode in with is persisted (W2-02).
        self.assertIsNone(platform.store.get_lead("lead_bad"))
        self.assertIsNotNone(platform.store.get_lead("lead_ok"))

    def test_unexpected_item_fault_is_isolated(self) -> None:
        platform = self._platform()
        healthy = platform._ingest_lead

        def flaky(envelope, *, message, sender_id, **kwargs):
            if message["idempotency_key"] == "batch-lead_boom":
                raise RuntimeError("induced per-item fault")
            return healthy(envelope, message=message, sender_id=sender_id, **kwargs)

        platform._ingest_lead = flaky  # type: ignore[method-assign]

        status, payload = self._post(
            platform,
            _batch([_item("lead_boom"), _item("lead_survivor")], key="batch-fault"),
        )

        self.assertEqual(status, 207)
        broken, survivor = payload["results"]
        self.assertEqual(broken["status"], "error")
        self.assertEqual(broken["error"]["code"], "LCP-500")
        self.assertEqual(survivor["status"], "accepted")
        # The sibling was still persisted after the induced failure (W2-02).
        self.assertIsNotNone(platform.store.get_lead("lead_survivor"))
        self.assertEqual(
            [
                row["action"]
                for row in platform.store.list_audit_events("lead", "lead_boom")
            ],
            ["lead.error"],
        )

    def test_non_object_item_payload_rejects_whole_request(self) -> None:
        # An item without the LEP's per-item metadata is not a batch this
        # platform agreed to process: schemas/batch.json refuses the wrapper
        # (400), so no item runs. Per-item negatives are lead-payload
        # negatives only (see test_invalid_item_does_not_fail_siblings).
        platform = self._platform()
        envelope = _batch([_item("lead_shape"), _item("lead_after_junk")])
        envelope["lcp"]["payload"]["items"][0]["payload"] = "not-a-lead-payload"

        status, payload = self._post(platform, envelope)

        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["code"], "LCP-100")
        self.assertIsNone(platform.store.get_lead("lead_shape"))
        self.assertIsNone(platform.store.get_lead("lead_after_junk"))

    # ─── Per-item idempotency ──────────────────────────────────────────────

    def test_replayed_item_returns_its_original_receipt(self) -> None:
        platform = self._platform()
        item = _item("lead_dup", key="batch-dup-key")

        first_status, first = self._post(
            platform, _batch([item], key="batch-dup-request-1")
        )
        second_status, second = self._post(
            platform, _batch([item], key="batch-dup-request-2")
        )

        self.assertEqual((first_status, second_status), (207, 207))
        self.assertEqual(first["results"][0]["status"], "accepted")
        self.assertEqual(second["results"][0]["status"], "duplicate")
        self.assertEqual(
            second["results"][0]["lead_id"], first["results"][0]["lead_id"]
        )
        self.assertEqual(second["results"][0]["item_id"], "item-lead_dup")
        self.assertEqual(
            second["summary"],
            {"total": 1, "accepted": 0, "duplicate": 1, "rejected": 0, "error": 0},
        )
        # The replay is the existing LCP-005 semantics at item granularity:
        # the row is not re-inserted, and the duplicate is audited. (Audit
        # timestamps are second-precision, so compare as a set.)
        self.assertEqual(
            sorted(
                row["action"]
                for row in platform.store.list_audit_events("lead", "lead_dup")
            ),
            ["lead.duplicate", "lead.routed"],
        )

    def test_reused_item_key_with_different_content_is_rejected(self) -> None:
        platform = self._platform()
        _first_status, _first = self._post(
            platform,
            _batch([_item("lead_conflict", key="batch-conflict")], key="batch-c-1"),
        )

        changed = _item("lead_conflict", key="batch-conflict")
        changed["payload"]["consumer"]["first_name"] = "Grace"
        status, payload = self._post(platform, _batch([changed], key="batch-c-2"))

        self.assertEqual(status, 207)
        self.assertEqual(payload["results"][0]["status"], "rejected")
        self.assertEqual(payload["results"][0]["error"]["code"], "LCP-005")
        stored = platform.store.get_lead("lead_conflict")
        assert stored is not None
        self.assertEqual(
            json.loads(stored["envelope_json"])["lcp"]["payload"]["consumer"][
                "first_name"
            ],
            "Jane",
        )

    # ─── Request-level refusals ────────────────────────────────────────────

    def test_over_cap_items_rejects_whole_request(self) -> None:
        platform = self._platform()
        items = [_item(f"lead_cap_{index}") for index in range(BATCH_MAX_ITEMS + 1)]

        status, payload = self._post(platform, _batch(items, key="batch-over-cap"))

        self.assertEqual(status, 413)
        self.assertEqual(payload["errors"][0]["code"], "LCP-001")
        self.assertIsNone(platform.store.get_lead("lead_cap_0"))

    def test_over_cap_body_rejects_whole_request(self) -> None:
        platform = self._platform()
        envelope = _batch([_item("lead_body")], key="batch-over-body")
        envelope["padding"] = "x" * BATCH_MAX_BODY_BYTES

        status, payload = self._post(platform, envelope)

        self.assertEqual(status, 413)
        self.assertEqual(payload["errors"][0]["code"], "LCP-001")
        self.assertIsNone(platform.store.get_lead("lead_body"))

    def test_malformed_wrapper_rejects_whole_request(self) -> None:
        platform = self._platform()
        envelope = _batch([_item("lead_shape")], key="batch-shape")
        envelope["lcp"]["payload"]["items"] = {"lead": "not-an-array"}

        status, payload = self._post(platform, envelope)

        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["code"], "LCP-100")
        self.assertIsNone(platform.store.get_lead("lead_shape"))

    def test_missing_request_id_rejects_whole_request(self) -> None:
        platform = self._platform()
        envelope = _batch([_item("lead_no_request_id")], request_id=None)

        status, payload = self._post(platform, envelope)

        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["code"], "LCP-100")
        self.assertIsNone(platform.store.get_lead("lead_no_request_id"))

    # ─── Tenant authorization ──────────────────────────────────────────────

    def test_batch_sender_must_match_authenticated_principal(self) -> None:
        platform = self._platform(require_auth=True)

        # A publisher's secret may not submit a batch claiming a buyer id: the
        # batch has exactly one authenticated principal (there is no per-item
        # sender from v1.2), so the request itself is refused before any item.
        status, payload = self._post(
            platform,
            _batch([_item("lead_forged")], sender="buyer_7", key="batch-tenant-1"),
            secret=PUBLISHER_SECRET,
        )

        self.assertEqual(status, 401)
        # The header identity cannot be authenticated for that sender: the auth
        # layer's HMAC code (LCP-012) or the identity mismatch code (LCP-002).
        self.assertIn(payload["errors"][0]["code"], {"LCP-002", "LCP-012"})
        self.assertIsNone(platform.store.get_lead("lead_forged"))

        # And a buyer cannot submit a lead batch as a publisher it does not own.
        status, payload = self._post(
            platform,
            _batch([_item("lead_forged_2")], sender="pub_123", key="batch-tenant-2"),
            secret=BUYER_SECRET,
        )

        self.assertEqual(status, 401)
        self.assertIn(payload["errors"][0]["code"], {"LCP-002", "LCP-012"})
        self.assertIsNone(platform.store.get_lead("lead_forged_2"))

    # ─── Audit ─────────────────────────────────────────────────────────────

    def test_audit_records_one_entry_per_processed_item(self) -> None:
        platform = self._platform()
        invalid = _item("lead_audit_bad")
        invalid["payload"].pop("channel")
        items = [_item("lead_audit_ok"), invalid, _item("lead_audit_ok_2")]

        status, _payload = self._post(platform, _batch(items, key="batch-audit"))
        self.assertEqual(status, 207)

        expected = {
            "lead_audit_ok": "lead.routed",
            "lead_audit_bad": "lead.rejected",
            "lead_audit_ok_2": "lead.routed",
        }
        for lead_id, action in expected.items():
            rows = platform.store.list_audit_events("lead", lead_id)
            self.assertEqual([row["action"] for row in rows], [action], lead_id)

    # ─── Advertisement gate ────────────────────────────────────────────────

    def test_batch_is_off_by_default_and_unadvertised(self) -> None:
        platform = self._platform(feature_batch=False)

        capabilities = platform.capabilities()
        self.assertNotIn("batch", capabilities["features"])
        self.assertNotIn("batch", capabilities["message_types"])
        status, payload = self._post(
            platform, _batch([_item("lead_flag")], key="batch-flag")
        )

        self.assertEqual(status, 404)
        self.assertEqual(payload["errors"][0]["code"], "LCP-001")
        self.assertIsNone(platform.store.get_lead("lead_flag"))

    def test_enabled_batch_is_advertised(self) -> None:
        platform = self._platform(feature_batch=True)

        capabilities = platform.capabilities()
        self.assertEqual(capabilities["features"]["batch"], "1.2.0")
        self.assertIn("batch", capabilities["message_types"])


if __name__ == "__main__":  # pragma: no cover - unittest entrypoint
    unittest.main()
