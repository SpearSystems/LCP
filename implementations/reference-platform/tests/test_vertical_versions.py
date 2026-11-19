"""LEP-0016: mortgage 2.0.0 served side by side with the frozen 1.0.0.

Contract pinned here:

* capabilities list one entry per served (id, schema_version), read from each
  file's const, so mortgage appears as 1.0.0 and 2.0.0;
* a lead is validated against the version its ``attributes.schema_version``
  names, so a 1.0.0 lead (cents) and a 2.0.0 lead (whole units) are both
  accepted;
* a version this receiver does not serve is LCP-100 with
  ``field`` = ``attributes.schema_version``;
* a ping projected from a 1.0.0 lead uses that version's ping_safe tags.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any
from uuid import uuid4

from lcp_platform.config import PlatformConfig
from lcp_platform.messages import build_ping
from lcp_platform.router import Platform
from lcp_platform.service import PlatformService
from lcp_platform.storage import now_iso

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_LEAD = json.loads((ROOT / "examples" / "lead.json").read_text(encoding="utf-8"))
V2 = {
    "vertical": "mortgage",
    "schema_version": "2.0.0",
    "amount_currency": "AUD",
    "loan_type": "refinance",
    "loan_amount": 650000,
    "loan_amount_band": "500k_750k",
    "property_value": 880000,
}
V1 = {
    "vertical": "mortgage",
    "schema_version": "1.0.0",
    "loan_type": "refinance",
    "loan_amount_cents": 65000000,
    "loan_amount_band": "500k_750k",
}


class VerticalVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=False,
                allow_insecure_webhooks=True,
                test_mode=True,
            )
        )
        self.addCleanup(self.platform.close)

    @staticmethod
    def _lead(attributes: dict[str, Any]) -> dict[str, Any]:
        lead = copy.deepcopy(EXAMPLE_LEAD)
        lead["lcp"]["message"].update(
            {
                "id": str(uuid4()),
                "timestamp": now_iso(),
                "idempotency_key": f"idem-{uuid4().hex}",
                "test": True,
            }
        )
        payload = lead["lcp"]["payload"]
        payload["lead_id"] = f"lead_{uuid4().hex[:12]}"
        payload.pop("expiry", None)
        payload["attributes"] = attributes
        return lead

    def _post(self, envelope: dict[str, Any]):
        message = envelope["lcp"]["message"]
        headers = {
            "Content-Type": "application/json",
            "X-LCP-Idempotency-Key": message["idempotency_key"],
            "X-LCP-Sender-Id": message["sender_id"],
            "X-LCP-Test": "true",
        }
        body = json.dumps(envelope, separators=(",", ":")).encode()
        return PlatformService(self.platform).dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )

    def test_capabilities_list_every_served_version(self) -> None:
        verticals = self.platform.capabilities()["verticals"]
        self.assertIn({"id": "mortgage", "schema_version": "1.0.0"}, verticals)
        self.assertIn({"id": "mortgage", "schema_version": "2.0.0"}, verticals)
        self.assertIn({"id": "personal_loans", "schema_version": "2.0.0"}, verticals)
        self.assertEqual(len(verticals), len({(v["id"], v["schema_version"]) for v in verticals}))

    def test_both_versions_are_accepted(self) -> None:
        for attributes in (V1, V2):
            with self.subTest(version=attributes["schema_version"]):
                status, _, body = self._post(self._lead(attributes))
                self.assertEqual(status, 200, body)

    def test_each_version_is_checked_against_its_own_schema(self) -> None:
        crossed = {**V2, "loan_amount_cents": 65000000}
        status, _, body = self._post(self._lead(crossed))
        self.assertEqual(status, 400)
        self.assertEqual(body["errors"][0]["code"], "LCP-100")

    def test_unserved_version_names_the_field(self) -> None:
        status, _, body = self._post(self._lead({**V2, "schema_version": "9.9.9"}))
        self.assertEqual(status, 400)
        error = body["errors"][0]
        self.assertEqual(error["code"], "LCP-100")
        self.assertEqual(error["field"], "attributes.schema_version")
        self.assertIn("1.0.0, 2.0.0", error["details"][0])

    def test_ping_from_a_1_0_lead_uses_1_0_tags(self) -> None:
        lead = self._lead(V1)
        offer = {"offer_id": "o1", "buyer_id": "b1", "vertical": "mortgage", "floor_price_cents": 100, "currency": "USD"}
        ping, _ = build_ping(lead, offer, platform_id="platform_001", buyer_secret="s")
        attributes = ping["lcp"]["payload"]["attributes"]
        self.assertEqual(attributes, {"loan_type": "refinance", "loan_amount_band": "500k_750k"})
        self.platform.validator.require_valid_envelope(ping)


if __name__ == "__main__":
    unittest.main()
