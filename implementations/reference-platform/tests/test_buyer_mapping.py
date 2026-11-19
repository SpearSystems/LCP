"""LEP-0015: the reference executor against the golden BMAP vectors, and the
router integration behind LCP_FEATURE_BUYER_MAPPING.

Vectors (test-vectors/buyer-mapping.json) pin the pure executor: render,
decode and upsert refusal. The integration tests pin what the router does
with a decoded result: money never invented, ambiguity pending, failures
before sending dead-lettered with the target key only, test posts kept off
the live URL, one bounded counter re-post, and the native 1.3.0 ack read.
"""

from __future__ import annotations

import copy
from dataclasses import replace
import json
import os
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock
from urllib.parse import parse_qsl
from uuid import uuid4

import httpx

from lcp_platform import buyer_mapping
from lcp_platform import router as router_module
from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform, RequestError
from lcp_platform.storage import now_iso
from lcp_platform.validation import SchemaValidator

ROOT = Path(__file__).resolve().parents[3]
VECTORS = json.loads((ROOT / "test-vectors" / "buyer-mapping.json").read_text(encoding="utf-8"))
SECRET_ENV = "LCP_BUYER_SECRET_EXAMPLE_LOANS_API_KEY"


def apply_patch(document: Any, operations: list[dict[str, Any]] | None) -> Any:
    """RFC 6902 add/replace/remove on object members (all the vectors use)."""
    document = copy.deepcopy(document)
    for operation in operations or []:
        parts = [
            part.replace("~1", "/").replace("~0", "~")
            for part in operation["path"].split("/")[1:]
        ]
        parent = document
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        if operation["op"] == "remove":
            del parent[parts[-1]]
        else:
            parent[parts[-1]] = copy.deepcopy(operation["value"])
    return document


def placeholder(name: str) -> str:
    return VECTORS["secret_placeholder"].replace("{name}", name)


class BuyerMappingVectorTests(unittest.TestCase):
    validator = SchemaValidator(ROOT / "schemas")

    def _check(self, mapping: dict[str, Any]) -> list[str]:
        return self.validator.buyer_mapping_errors(mapping) or buyer_mapping.check(
            mapping,
            vertical_schema=self.validator.vertical_schema,
            url_error=lambda url: None,
        )

    def test_base_mapping_and_post_are_valid(self) -> None:
        self.assertEqual(self._check(VECTORS["mapping"]), [])
        self.assertEqual(self.validator.validate_envelope(VECTORS["post"]), [])

    def test_render_vectors(self) -> None:
        for vector in VECTORS["render"]:
            with self.subTest(vector["id"]):
                mapping = apply_patch(VECTORS["mapping"], vector.get("mapping_patch"))
                post = apply_patch(VECTORS["post"], vector.get("post_patch"))
                self.assertEqual(self._check(mapping), [])
                if "expect_failure" in vector:
                    with self.assertRaises(buyer_mapping.MappingFailure) as caught:
                        buyer_mapping.render(mapping, post, secret_for=placeholder)
                    self.assertEqual(caught.exception.target, vector["expect_failure"]["target"])
                    continue
                request = buyer_mapping.render(mapping, post, secret_for=placeholder)
                expect = vector["expect"]
                if expect is None:
                    self.assertIsNone(request)
                    continue
                assert request is not None
                self.assertEqual(request.url, expect["url"])
                self.assertEqual(request.headers, expect["headers"])
                if "body_form" in expect:
                    pairs = parse_qsl(request.body.decode(), keep_blank_values=True)
                    self.assertEqual([list(pair) for pair in pairs], expect["body_form"])
                else:
                    self.assertEqual(json.loads(request.body), expect["body"])

    def test_decode_vectors(self) -> None:
        currency = VECTORS["post"]["lcp"]["payload"]["currency"]
        for vector in VECTORS["decode"]:
            with self.subTest(vector["id"]):
                result = buyer_mapping.decode(
                    VECTORS["mapping"],
                    vector["status"],
                    vector["body"].encode("utf-8"),
                    currency=currency,
                )
                self.assertEqual(result, vector["expect"])

    def test_refusal_vectors(self) -> None:
        for vector in VECTORS["refuse"]:
            with self.subTest(vector["id"]):
                mapping = apply_patch(VECTORS["mapping"], vector["mapping_patch"])
                schema_errors = self.validator.buyer_mapping_errors(mapping)
                if vector["by"] == "schema":
                    self.assertTrue(schema_errors)
                    errors = schema_errors
                else:
                    self.assertEqual(schema_errors, [])
                    errors = self._check(mapping)
                self.assertIn(vector["error_contains"], " ".join(errors))

    def test_failures_never_carry_the_value(self) -> None:
        post = apply_patch(
            VECTORS["post"],
            [{"op": "replace", "path": "/lcp/payload/consumer/phone", "value": "+61412345678"}],
        )
        with self.assertRaises(buyer_mapping.MappingFailure) as caught:
            buyer_mapping.render(VECTORS["mapping"], post, secret_for=placeholder)
        self.assertNotIn("61412345678", str(caught.exception))

    def test_cents_to_units_uses_the_currency_exponent(self) -> None:
        for currency, cents, units in (("JPY", 5050, 5050), ("KWD", 5000, 5), ("USD", 5000, 50)):
            with self.subTest(currency):
                post = apply_patch(
                    VECTORS["post"],
                    [
                        {"op": "replace", "path": "/lcp/payload/currency", "value": currency},
                        {"op": "replace", "path": "/lcp/payload/price_cents", "value": cents},
                    ],
                )
                request = buyer_mapping.render(VECTORS["mapping"], post, secret_for=placeholder)
                assert request is not None
                self.assertEqual(json.loads(request.body)["pricing"]["min_price"], units)


class BuyerMappingRouterTests(unittest.TestCase):
    """The router side: upsert/list/deactivate and `_deliver` outcomes."""

    def setUp(self) -> None:
        self.platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=False,
                allow_insecure_webhooks=True,
                test_mode=True,
                feature_buyer_mapping=True,
                feature_sale_disposition=True,
            )
        )
        self.addCleanup(self.platform.close)
        patcher = mock.patch.dict(os.environ, {SECRET_ENV: "s3cr3t-value"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.platform.upsert_credential(
            "buyer_example_loans", tenant_id="default", scopes=["*"], hmac_secret="buyer-hmac"
        )
        self.offer = {
            "offer_id": "offer_example_loans",
            "buyer_id": "buyer_example_loans",
            "vertical": "personal_loans",
            "countries": ["US"],
            "floor_price_cents": 3000,
            "currency": "USD",
            "webhook_url": "https://hooks.buyer.example/lcp",
        }
        self.platform.upsert_offer(self.offer)
        self.platform.upsert_buyer_mapping(VECTORS["mapping"])
        self.requests: list[httpx.Request] = []
        self.responses: list[httpx.Response | Exception] = []

    def _transport(self) -> Callable[..., httpx.Client]:
        real_client = httpx.Client

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        def factory(**kwargs: Any) -> httpx.Client:
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        return factory

    def _award(self, *, test: bool = False, price_cents: int = 5000) -> dict[str, Any]:
        post = copy.deepcopy(VECTORS["post"])
        message = post["lcp"]["message"]
        message.update(
            {"id": str(uuid4()), "timestamp": now_iso(), "test": test,
             "idempotency_key": f"platform_001-post-{uuid4().hex}"}
        )
        payload = post["lcp"]["payload"]
        payload["lead_id"] = f"lead_{uuid4().hex[:12]}"
        payload["price_cents"] = price_cents
        payload["pricing"]["final_price_cents"] = price_cents
        lead = {
            "lcp": {
                "version": "1.3.0",
                "message": {**message, "type": "lead", "sender_id": "pub_example",
                            "receiver_id": "platform_001", "id": str(uuid4())},
                "payload": {
                    "lead_id": payload["lead_id"],
                    "submitted_at": payload["submitted_at"],
                    "status": "NEW",
                    "channel": "form",
                    "consumer": payload["consumer"],
                    "location": payload["location"],
                    "attributes": payload["attributes"],
                },
            }
        }
        self.platform.store.insert_lead(lead, status="POSTED", tenant_id="default", routing_job=False)
        self.platform.store.insert_delivery(
            lead_id=payload["lead_id"], ping_id=None, offer_id=self.offer["offer_id"],
            buyer_id=self.offer["buyer_id"], kind="post", envelope=post,
            webhook_url=self.offer["webhook_url"],
        )
        self.platform._record_pending_payable(lead, self.offer, price_cents)
        return post

    def _run(self, *responses: httpx.Response | Exception) -> None:
        self.responses.extend(responses)
        with mock.patch.object(router_module.httpx, "Client", side_effect=self._transport()):
            self.platform._process_deliveries()

    def _payable(self, lead_id: str) -> dict[str, Any]:
        (record,) = self.platform.store.payable_for_lead(lead_id)
        return record

    def _sale_events(self, lead_id: str) -> list[tuple[str, dict[str, Any]]]:
        return [
            (event["lcp"]["payload"]["event"], event["lcp"]["payload"]["details"])
            for event in self.platform.store.list_events(lead_id)
            if event["lcp"]["message"]["sender_id"] == "platform_001"
        ]

    def _deliveries(self, lead_id: str) -> list[Any]:
        return [d for d in self.platform.store.deliveries_for_lead(lead_id) if d["kind"] == "post"]

    def test_upsert_refuses_and_lists_and_deactivates(self) -> None:
        with self.assertRaises(RequestError) as caught:
            self.platform.upsert_buyer_mapping(
                apply_patch(VECTORS["mapping"], VECTORS["refuse"][0]["mapping_patch"])
            )
        self.assertEqual(caught.exception.code, "LCP-100")
        listed = self.platform.list_buyer_mappings("buyer_example_loans")
        self.assertEqual([(m["mapping_id"], m["version"], m["active"]) for m in listed],
                         [("example-loans-direct-v2", "2.0.0", True)])
        self.platform.deactivate_buyer_mapping("example-loans-direct-v2")
        self.assertFalse(self.platform.list_buyer_mappings("buyer_example_loans")[0]["active"])

    def test_sold_is_payable_at_the_decoded_price_and_secret_is_sent(self) -> None:
        post = self._award()
        self._run(httpx.Response(200, json={"result": "accepted", "price": 42, "lead_id": "B-9"}))
        (request,) = self.requests
        self.assertEqual(str(request.url), "https://api.buyer.example/v2/leads")
        self.assertEqual(request.headers["X-Api-Key"], "s3cr3t-value")
        record = self._payable(post["lcp"]["payload"]["lead_id"])
        self.assertEqual((record["status"], record["price_cents"]), ("payable", 4200))
        (result,) = self.platform.store.buyer_results_for_lead(post["lcp"]["payload"]["lead_id"])
        self.assertEqual((result["outcome"], result["buyer_reference"]), ("sold", "B-9"))

    def test_unsold_is_not_payable_and_reported_rejected(self) -> None:
        post = self._award()
        lead_id = post["lcp"]["payload"]["lead_id"]
        self._run(httpx.Response(200, json={"result": "rejected", "reason": "dup"}))
        record = self._payable(lead_id)
        self.assertEqual(record["status"], "not_payable")
        (event,) = self._sale_events(lead_id)
        self.assertEqual(event[0], "REJECTED")

    def test_ambiguous_timeout_without_idempotency_is_pending(self) -> None:
        mapping = apply_patch(
            VECTORS["mapping"],
            [{"op": "remove", "path": "/request/idempotency"},
             {"op": "replace", "path": "/version", "value": "2.0.1"}],
        )
        self.platform.upsert_buyer_mapping(mapping)
        post = self._award()
        self._run(httpx.ReadTimeout("slow"))
        record = self._payable(post["lcp"]["payload"]["lead_id"])
        self.assertEqual((record["status"], record["reason"]), ("pending", "buyer_pending"))
        self.assertEqual(self._deliveries(post["lcp"]["payload"]["lead_id"])[0]["status"], "DELIVERED")

    def test_connect_error_retries(self) -> None:
        post = self._award()
        self._run(httpx.ConnectError("refused"))
        (delivery,) = self._deliveries(post["lcp"]["payload"]["lead_id"])
        self.assertEqual(delivery["status"], "RETRY")

    def test_mapping_failure_dead_letters_with_target_key_only(self) -> None:
        post = self._award(price_cents=5050)
        self._run()
        self.assertEqual(self.requests, [])
        (delivery,) = self._deliveries(post["lcp"]["payload"]["lead_id"])
        self.assertEqual(delivery["status"], "FAILED")
        self.assertEqual(delivery["last_error"], "mapping_failed: pricing.min_price")
        self.assertTrue(self.platform.store.list_dead_letters("OPEN"))

    def test_test_post_never_reaches_live_url(self) -> None:
        self._award(test=True)
        self._run(httpx.Response(200, json={"result": "accepted", "price": 42}))
        (request,) = self.requests
        self.assertEqual(request.url.host, "sandbox.buyer.example")

    def test_counter_reposts_once_at_the_counter_price(self) -> None:
        post = self._award()
        lead_id = post["lcp"]["payload"]["lead_id"]
        self._run(httpx.Response(200, json={"result": "price_rejected", "min_price": 40}))
        (repost,) = self._deliveries(lead_id)  # the awarded slot is re-armed, not copied
        self.assertEqual(repost["status"], "PENDING")
        self.assertEqual(repost["idempotency_key"], post["lcp"]["message"]["idempotency_key"] + ":r1")
        self.assertNotEqual(repost["message_id"], post["lcp"]["message"]["id"])
        self.assertEqual(self._payable(lead_id)["price_cents"], 4000)
        self._run(httpx.Response(200, json={"result": "price_rejected", "min_price": 35}))
        self.assertEqual(len(self.requests), 2)  # never a second re-post
        self.assertEqual(json.loads(self.requests[1].content)["pricing"]["min_price"], 40)
        self.assertEqual(self._payable(lead_id)["status"], "not_payable")
        self.assertEqual(self._deliveries(lead_id)[0]["status"], "DELIVERED")

    def test_counter_below_floor_is_not_reposted(self) -> None:
        post = self._award()
        lead_id = post["lcp"]["payload"]["lead_id"]
        self._run(httpx.Response(200, json={"result": "price_rejected", "min_price": 20}))
        self.assertEqual(self._deliveries(lead_id)[0]["status"], "DELIVERED")
        self.assertEqual(self._payable(lead_id)["status"], "not_payable")

    def test_unconfirmed_pending_becomes_rejected(self) -> None:
        post = self._award()
        lead_id = post["lcp"]["payload"]["lead_id"]
        self._run(httpx.Response(200, json={"result": "queued"}))
        with mock.patch.object(router_module, "BUYER_PENDING_FINAL_SECONDS", -1):
            self.platform._resolve_unconfirmed_buyer_results()
        record = self._payable(lead_id)
        self.assertEqual((record["status"], record["reason"]), ("not_payable", "buyer_unconfirmed"))
        (event,) = self._sale_events(lead_id)
        self.assertEqual((event[0], event[1]["rejection_reason"]), ("REJECTED", "buyer_unconfirmed"))

    def test_sold_reports_accepted_at_the_buyer_price(self) -> None:
        post = self._award()
        lead_id = post["lcp"]["payload"]["lead_id"]
        self._run(httpx.Response(200, json={"result": "accepted", "price": 42}))
        (event,) = self._sale_events(lead_id)
        self.assertEqual((event[0], event[1]["payout_cents"]), ("ACCEPTED", 4200))

    def test_posts_go_out_at_1_3_only_to_buyers_declaring_it(self) -> None:
        self.assertIsNone(self.platform._native_post_version("buyer_example_loans"))
        self.platform.upsert_credential(
            "buyer_example_loans", tenant_id="default", scopes=["*"],
            hmac_secret="buyer-hmac", lcp_versions=["1.2.0", "1.3.0"],
        )
        self.assertEqual(self.platform._native_post_version("buyer_example_loans"), "1.3.0")

    def test_native_buyer_ack_disposition_is_read(self) -> None:
        self.platform.deactivate_buyer_mapping("example-loans-direct-v2")
        post = self._award(price_cents=5000)
        ack = {
            "lcp": {
                "version": "1.3.0",
                "message": {
                    "id": str(uuid4()), "type": "ack", "timestamp": now_iso(),
                    "sender_id": "buyer_example_loans", "receiver_id": "platform_001",
                    "correlation_id": post["lcp"]["message"]["id"],
                    "idempotency_key": f"ack-{uuid4().hex}", "test": False,
                },
                "payload": {
                    "original_message_id": post["lcp"]["message"]["id"],
                    "status": "ACCEPTED",
                    "disposition": {"outcome": "unsold"},
                    "rejection_reason": "duplicate",
                },
            }
        }
        self._run(httpx.Response(200, json=ack))
        (request,) = self.requests
        self.assertEqual(str(request.url), self.offer["webhook_url"])
        self.assertEqual(self._payable(post["lcp"]["payload"]["lead_id"])["status"], "not_payable")

    def test_native_buyer_without_valid_ack_keeps_todays_behaviour(self) -> None:
        self.platform.deactivate_buyer_mapping("example-loans-direct-v2")
        post = self._award()
        self._run(httpx.Response(200, text="ok"))
        self.assertEqual(self._payable(post["lcp"]["payload"]["lead_id"])["status"], "payable")

    def test_flag_off_delivers_the_lcp_envelope(self) -> None:
        self.platform.config = replace(self.platform.config, feature_buyer_mapping=False)
        post = self._award()
        self._run(httpx.Response(200, json={"result": "rejected"}))
        (request,) = self.requests
        self.assertEqual(str(request.url), self.offer["webhook_url"])
        self.assertEqual(self._payable(post["lcp"]["payload"]["lead_id"])["status"], "payable")


if __name__ == "__main__":
    unittest.main()
