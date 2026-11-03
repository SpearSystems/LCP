"""SPEC §5/§8/§9 coverage-gap closure tests (W5-prep grind).

Rows claimed by this file (docs/SPEC-COVERAGE.md): ERR-003, ERR-011,
SEC-10, ERR-500, CAP-02, CAP-05, SND-01. Every assertion drives the
platform's own HTTP dispatch surface (``PlatformService.dispatch``) — the
protocol wire behaviour, not just the schema layer.

SPEC clause quotes (verbatim, with the section that carries them):

* §5 (error table): ``LCP-003 | MISSING_REQUIRED_FIELD | 400``,
  ``LCP-011 | RATE_LIMITED | 429``, ``LCP-500 | INTERNAL_ERROR | 500``,
  ``LCP-013 | TEST_TRAFFIC_POLICY | 400``.
* §5: "`LCP-011 RATE_LIMITED` (429) pairs with the per-sender rate limits
  defined in §9."
* §5: "`LCP-013 TEST_TRAFFIC_POLICY` is used by the HTTP reference profile
  when the `test` envelope field and `X-LCP-Test` header disagree, when a
  sandbox receives non-test traffic, or when a production endpoint
  receives test traffic."
* §9: "Per-sender rate limits are enforced. Exceeding the limit returns
  `LCP-011 RATE_LIMITED` (429) with a `Retry-After` header (HTTP binding)."
* §8: "`countries` is an array of ISO 3166-1 alpha-2 country codes the
  endpoint supports (e.g. `[\"AU\", \"NZ\", \"US\"]`). This replaces the legacy
  `markets` field — no more `au_nz` / `us` grouping."
* §8 (capacity): "Caps are advisory — the platform may still route leads
  above cap, but the endpoint may reject them (LCP-011 RATE_LIMITED or a
  200 with `ack.status = REJECTED`)."
"""

from __future__ import annotations

from dataclasses import replace
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform
from lcp_platform.service import PlatformService


ROOT = Path(__file__).resolve().parents[3]

_ALPHA2 = re.compile(r"\A[A-Z]{2}\Z")


class _FrozenDateTime(datetime):
    """Fixed clock so two back-to-back rate-limit requests land in the same
    wall-clock minute window (storage.py reads this module attribute)."""

    FROZEN = datetime(2026, 11, 3, 4, 30, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.FROZEN if tz is None else cls.FROZEN.astimezone(tz)


class GapErrorsCapsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.platform = self._make_platform()
        self.platform.upsert_credential("buyer_001", hmac_secret="buyer-secret")

    def tearDown(self) -> None:
        self.platform.close()

    def _make_platform(self, **overrides: object) -> Platform:
        config = PlatformConfig(
            database_path=Path(":memory:"),
            schema_root=ROOT / "schemas",
            platform_id="platform_001",
            require_auth=False,
            allow_insecure_webhooks=True,
            test_mode=True,
        )
        if overrides:
            config = replace(config, **overrides)
        return Platform(config)

    def load_lead(self, *, sender: str = "pub_123", test: bool = True) -> dict:
        with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
            lead = json.load(handle)
        lead["lcp"]["message"]["test"] = test
        lead["lcp"]["message"]["id"] = str(uuid4())
        lead["lcp"]["message"]["sender_id"] = sender
        lead["lcp"]["message"]["idempotency_key"] = f"idem-{uuid4().hex}"
        lead["lcp"]["payload"]["lead_id"] = f"lead_{uuid4().hex[:12]}"
        # Routing cases use the non-expiring intake (see test_platform.py).
        lead["lcp"]["payload"].pop("expiry", None)
        return lead

    def post_lead(self, envelope: dict) -> tuple[int, dict, dict]:
        body = json.dumps(envelope, separators=(",", ":")).encode()
        return PlatformService(self.platform).dispatch(
            "POST",
            "/v1/lcp/leads",
            headers={
                "Content-Type": "application/json",
                "X-LCP-Idempotency-Key": envelope["lcp"]["message"]["idempotency_key"],
                "X-LCP-Test": "true",
            },
            body=body,
        )

    # ── ERR-003 ─────────────────────────────────────────────────────────────

    def test_platform_stamps_lcp003_on_real_requests_with_missing_required_fields(
        self,
    ) -> None:
        # SPEC §5: "LCP-003 | MISSING_REQUIRED_FIELD | 400". Envelope-schema
        # violations are the deliberate LCP-100 catch-all (issue #131); the
        # platform's own request surface stamps LCP-003 when required fields
        # are missing, and this asserts the code lands on the wire — not
        # only in the schema corpus.
        service = PlatformService(self.platform)
        base = {
            "X-LCP-Sender-Id": "pub_123",
            "X-LCP-Test": "true",
            "X-LCP-Idempotency-Key": f"att-{uuid4().hex}",
            "X-LCP-Content-SHA256": "a" * 64,
        }
        # Missing X-LCP-Lead-Id ...
        status, _, payload = service.dispatch(
            "POST", "/v1/lcp/attachments", headers=dict(base), body=b"payload"
        )
        self.assertEqual(status, 400)
        error = payload["errors"][0]
        self.assertEqual(error["code"], "LCP-003")
        self.assertIn("required", error["message"].lower())
        # ... and missing X-LCP-Content-SHA256 stamp the same code.
        no_digest = {
            key: value for key, value in base.items() if key != "X-LCP-Content-SHA256"
        }
        no_digest["X-LCP-Lead-Id"] = f"lead_{uuid4().hex[:12]}"
        status, _, payload = service.dispatch(
            "POST", "/v1/lcp/attachments", headers=no_digest, body=b"payload"
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["code"], "LCP-003")

    # ── ERR-011 / SEC-10 ────────────────────────────────────────────────────

    def test_rate_limited_intake_returns_lcp011_with_retry_after(self) -> None:
        # SPEC §5: "LCP-011 RATE_LIMITED (429) pairs with the per-sender
        # rate limits defined in §9."  SPEC §9: "Per-sender rate limits are
        # enforced. Exceeding the limit returns LCP-011 RATE_LIMITED (429)
        # with a Retry-After header (HTTP binding)."
        self.platform.close()
        self.platform = self._make_platform(rate_limit_per_minute=1)
        with patch("lcp_platform.storage.datetime", _FrozenDateTime):
            first, _, _ = self.post_lead(self.load_lead())
            self.assertEqual(first, 200)
            status, headers, payload = self.post_lead(self.load_lead())
        self.assertEqual(status, 429)
        self.assertEqual(payload["errors"][0]["code"], "LCP-011")
        retry_after = headers.get("Retry-After")
        self.assertIsNotNone(retry_after, "429 must carry Retry-After (§9)")
        self.assertGreater(int(retry_after), 0)

    def test_rate_limit_is_per_sender_not_global(self) -> None:
        # SPEC §9: "Per-sender rate limits are enforced." A second sender
        # must keep flowing while the blocked sender stays throttled.
        self.platform.close()
        self.platform = self._make_platform(rate_limit_per_minute=1)
        with patch("lcp_platform.storage.datetime", _FrozenDateTime):
            blocked_a, _, _ = self.post_lead(self.load_lead(sender="pub_123"))
            self.assertEqual(blocked_a, 200)
            status_a2, headers_a2, payload_a2 = self.post_lead(
                self.load_lead(sender="pub_123")
            )
            self.assertEqual(status_a2, 429)
            self.assertEqual(payload_a2["errors"][0]["code"], "LCP-011")
            self.assertIsNotNone(headers_a2.get("Retry-After"))
            status_b1, _, _ = self.post_lead(self.load_lead(sender="pub_999"))
            self.assertEqual(status_b1, 200)
            status_a3, _, _ = self.post_lead(self.load_lead(sender="pub_123"))
            self.assertEqual(status_a3, 429)

    # ── ERR-500 ─────────────────────────────────────────────────────────────

    def test_fault_injected_store_crash_returns_lcp500_envelope(self) -> None:
        # SPEC §5: "LCP-500 | INTERNAL_ERROR | 500" carried in the one
        # §5 error shape ("errors[] with code/name/message/field/details
        # everywhere"). Fault-inject a crash in the store callable the
        # intake path depends on and pin the 500 envelope + correlation.
        lead = self.load_lead()
        lead_id = lead["lcp"]["payload"]["lead_id"]

        def crash(*args: object, **kwargs: object) -> None:
            raise RuntimeError("injected store failure")

        self.platform.store.insert_lead = crash  # type: ignore[method-assign]
        status, headers, payload = self.post_lead(lead)
        self.assertEqual(status, 500)
        self.assertEqual(
            payload["errors"], [{"code": "LCP-500", "message": "Internal server error"}]
        )
        self.assertEqual(payload["request_id"], headers["X-LCP-Request-Id"])
        # The crashed request must not persist the lead.
        self.assertIsNone(self.platform.store.get_lead(lead_id))

    # ── CAP-02 ──────────────────────────────────────────────────────────────

    def test_over_capacity_lead_is_withheld_with_capacity_exceeded_reason(
        self,
    ) -> None:
        # SPEC §8: "Caps are advisory — the platform may still route leads
        # above cap, but the endpoint may reject them (LCP-011 RATE_LIMITED
        # or a 200 with ack.status = REJECTED)." The reference platform
        # implements the permissive MAY as a routing withhold: a
        # capacity-exhausted offer drops out of the candidate set with an
        # explainable ``capacity_exceeded`` match decision, and the intake
        # itself still acks RECEIVED. This asserts the implemented shape.
        self.platform.upsert_offer(
            {
                "offer_id": "cap-offer",
                "buyer_id": "buyer_001",
                "active": True,
                "vertical": "mortgage",
                "countries": ["AU"],
                "floor_price_cents": 1500,
                "currency": "AUD",
                "webhook_url": "http://127.0.0.1:9999/buyer",
                "routing_mode": "direct",
                "daily_cap": 1,
            }
        )
        first = self.load_lead()
        first_id = first["lcp"]["payload"]["lead_id"]
        status, _, payload = self.post_lead(first)
        self.assertEqual(status, 200)
        self.assertEqual(payload["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(self.platform.store.get_lead(first_id)["status"], "POSTED")
        decisions = self.platform.store.list_match_decisions(first_id)
        self.assertTrue(decisions[0]["matched"])

        second = self.load_lead()
        second_id = second["lcp"]["payload"]["lead_id"]
        status, _, payload = self.post_lead(second)
        self.assertEqual(status, 200)
        self.assertEqual(payload["lcp"]["payload"]["status"], "RECEIVED")
        withheld = self.platform.store.list_match_decisions(second_id)
        self.assertEqual(len(withheld), 1)
        self.assertFalse(withheld[0]["matched"])
        self.assertIn("capacity_exceeded", withheld[0]["reasons"])
        # Nothing was awarded to the over-cap offer.
        self.assertEqual(self.platform.store.deliveries_for_lead(second_id), [])
        self.assertEqual(self.platform.store.get_lead(second_id)["status"], "NEW")

    # ── CAP-05 ──────────────────────────────────────────────────────────────

    def test_capabilities_countries_is_alpha2_and_replaces_legacy_markets(
        self,
    ) -> None:
        # SPEC §8: "countries is an array of ISO 3166-1 alpha-2 country
        # codes ... This replaces the legacy markets field — no more
        # au_nz / us grouping." The reference deployment's capabilities
        # passthrough records an empty list (unrestricted); assert the
        # replacement shape on the wire: countries is the ISO-array slot
        # and the legacy `markets` grouping is gone.
        status, _, payload = PlatformService(self.platform).dispatch(
            "GET", "/v1/lcp/capabilities"
        )
        self.assertEqual(status, 200)
        countries = payload["countries"]
        self.assertIsInstance(countries, list)
        for code in countries:
            self.assertRegex(code, _ALPHA2)
        self.assertNotIn("markets", payload)

    # ── SND-01 ──────────────────────────────────────────────────────────────

    def test_sandbox_endpoint_accepts_only_test_traffic_both_shapes(self) -> None:
        # SPEC §5: "LCP-013 TEST_TRAFFIC_POLICY is used by the HTTP
        # reference profile when the `test` envelope field and X-LCP-Test
        # header disagree, when a sandbox receives non-test traffic, or
        # when a production endpoint receives test traffic." The
        # production direction is covered by
        # plat:test_production_endpoint_rejects_test_traffic; this drives
        # the sandbox side (config test_mode=True): the accept control
        # plus every reject shape — envelope non-test (no header),
        # envelope test without the mirroring header, header/envelope
        # disagreement — on both the intake route and the attachment
        # route's header gate.
        service = PlatformService(self.platform)

        # Accept control: test=true envelope + X-LCP-Test: true → 200.
        status, _, payload = self.post_lead(self.load_lead())
        self.assertEqual(status, 200)
        self.assertNotIn("errors", payload)

        # Reject 1: non-test envelope into the sandbox with no X-LCP-Test
        # header at all → "Sandbox accepts only test messages".
        lead = self.load_lead(test=False)
        body = json.dumps(lead, separators=(",", ":")).encode()
        status, _, payload = service.dispatch(
            "POST",
            "/v1/lcp/leads",
            headers={
                "Content-Type": "application/json",
                "X-LCP-Idempotency-Key": lead["lcp"]["message"]["idempotency_key"],
            },
            body=body,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["code"], "LCP-013")
        self.assertIn("only test messages", payload["errors"][0]["message"])

        # Reject 2: test envelope missing the X-LCP-Test mirror.
        lead = self.load_lead()
        body = json.dumps(lead, separators=(",", ":")).encode()
        status, _, payload = service.dispatch(
            "POST",
            "/v1/lcp/leads",
            headers={
                "Content-Type": "application/json",
                "X-LCP-Idempotency-Key": lead["lcp"]["message"]["idempotency_key"],
            },
            body=body,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["code"], "LCP-013")
        self.assertIn("X-LCP-Test", payload["errors"][0]["message"])

        # Reject 3: envelope test=false while the header claims test.
        lead = self.load_lead(test=False)
        body = json.dumps(lead, separators=(",", ":")).encode()
        status, _, payload = service.dispatch(
            "POST",
            "/v1/lcp/leads",
            headers={
                "Content-Type": "application/json",
                "X-LCP-Idempotency-Key": lead["lcp"]["message"]["idempotency_key"],
                "X-LCP-Test": "true",
            },
            body=body,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["code"], "LCP-013")
        self.assertIn("must match", payload["errors"][0]["message"])

        # Attachment upload route: sandbox requires the header too.
        status, _, payload = service.dispatch(
            "POST",
            "/v1/lcp/attachments",
            headers={
                "X-LCP-Sender-Id": "pub_123",
                "X-LCP-Idempotency-Key": f"att-{uuid4().hex}",
                "X-LCP-Lead-Id": f"lead_{uuid4().hex[:12]}",
                "X-LCP-Content-SHA256": "a" * 64,
            },
            body=b"payload",
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["code"], "LCP-013")


if __name__ == "__main__":
    unittest.main()
