"""LEP-0003 W6-07 (SPEC §9-gated): email/form-relay entry emission.

A publisher whose entry surface is an email/form relay hands the consumer over
with no JavaScript capture, so nothing on the wire proves how the entry was
observed. SPEC §9 forbids inventing what a deployment did not observe: the
platform therefore records the entry itself, under the declared open-enum
``provenance.source_type: "email_form"``, with an ``acquisition_method`` that
agrees with the consent recorded on the lead, and with **no** client-observed
field (``provenance.ip_address`` / ``ip_hash`` / ``user_agent_hash`` and their
``user_agent`` relatives stay absent).

Covered here:

* the entry is recorded when the gate is on (flag + a declared relay sender),
* the lead is refused — not mislabelled as ``publisher`` — when it is off,
* no client-capture field is ever invented, and a publisher's own
  ``source_type`` declaration is never rewritten.

Plain ``unittest`` (the suite's style): no fixtures, no network.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform, RequestError

ROOT = Path(__file__).resolve().parents[3]
RELAY_PUBLISHER = "pub_email_relay"
CLIENT_CAPTURE_KEYS = ("ip_address", "ip_hash", "user_agent_hash")
_NAMESPACE = uuid5(NAMESPACE_URL, "lcp-entry-emission")


def _platform(
    *,
    feature: bool,
    relay_publishers: tuple[str, ...] = (RELAY_PUBLISHER,),
) -> Platform:
    return Platform(
        PlatformConfig(
            database_path=Path(":memory:"),
            schema_root=ROOT / "schemas",
            platform_id="platform_001",
            require_auth=False,
            test_mode=True,
            feature_email_form_entry=feature,
            email_form_publisher_ids=relay_publishers,
        )
    )


def _lead(
    lead_id: str,
    *,
    idempotency_key: str,
    consent: bool = True,
    capture: bool = False,
    source_type: str | None = "publisher",
) -> dict[str, Any]:
    """A relay lead: no client capture unless ``capture`` is asked for."""
    with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
        lead = json.load(handle)
    message = lead["lcp"]["message"]
    message["id"] = str(uuid5(_NAMESPACE, lead_id))
    message["test"] = True
    message["idempotency_key"] = idempotency_key
    message["sender_id"] = RELAY_PUBLISHER
    payload = lead["lcp"]["payload"]
    payload["lead_id"] = lead_id
    payload.pop("expiry", None)
    provenance = payload["provenance"]
    for key in CLIENT_CAPTURE_KEYS:
        provenance.pop(key, None)
    if source_type is None:
        provenance.pop("source_type", None)
    else:
        provenance["source_type"] = source_type
    if not consent:
        payload["compliance"].pop("consent_timestamp", None)
        payload["compliance"].pop("consent_evidence", None)
        payload["compliance"].pop("consent_purposes", None)
    if capture:
        provenance["ip_hash"] = "a" * 64
        provenance["user_agent_hash"] = "b" * 64
    return lead


def _submit(platform: Platform, lead: dict[str, Any]) -> dict[str, Any]:
    """Ingest one test-flagged lead exactly as the wire path would."""
    body = json.dumps(lead, separators=(",", ":")).encode()
    return platform.ingest(lead, headers={"X-LCP-Test": "true"}, raw_body=body)


def _stored_payload(platform: Platform, lead_id: str) -> dict[str, Any]:
    row = platform.store.get_lead(lead_id)
    assert row is not None, "lead was not recorded"
    envelope = platform.store.decode_envelope(row["envelope_json"])
    return envelope["lcp"]["payload"]


class EmailFormEntryEmissionTests(unittest.TestCase):
    def test_entry_emitted_when_gate_is_on(self) -> None:
        platform = _platform(feature=True)
        self.addCleanup(platform.close)
        lead = _lead("entry-gate-on", idempotency_key="entry-gate-on-key")

        ack = _submit(platform, lead)

        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        payload = _stored_payload(platform, "entry-gate-on")
        self.assertEqual(payload["provenance"]["source_type"], "email_form")
        # The lead records a consented handover, so the entry says so.
        self.assertEqual(payload["provenance"]["acquisition_method"], "warm_transfer")
        self.assertIn("email_form_entry", platform.capabilities()["features"])

        # The derivation is replay-stable: the stored bytes are what a replay
        # of the same lead is compared against, so it returns DUPLICATE.
        replay = _lead("entry-gate-on", idempotency_key="entry-gate-on-key")
        duplicate = _submit(platform, replay)
        self.assertEqual(duplicate["lcp"]["payload"]["status"], "DUPLICATE")

        # No consent on record: the platform refuses to claim an acquisition
        # path it has no evidence for, and still records the entry.
        bare = _lead("entry-no-consent", idempotency_key="entry-no-consent-key")
        bare["lcp"]["payload"]["compliance"].pop("consent_timestamp", None)
        bare["lcp"]["payload"]["compliance"].pop("consent_evidence", None)
        bare["lcp"]["payload"]["compliance"].pop("consent_purposes", None)
        _submit(platform, bare)
        self.assertEqual(
            _stored_payload(platform, "entry-no-consent")["provenance"]["source_type"],
            "email_form",
        )
        self.assertEqual(
            _stored_payload(platform, "entry-no-consent")["provenance"][
                "acquisition_method"
            ],
            "unknown",
        )

    def test_entry_refused_when_gate_is_off(self) -> None:
        platform = _platform(feature=False)
        self.addCleanup(platform.close)
        self.assertNotIn("email_form_entry", platform.capabilities()["features"])

        lead = _lead("entry-gate-off", idempotency_key="entry-gate-off-key")
        with self.assertRaises(RequestError) as raised:
            _submit(platform, lead)

        self.assertEqual(raised.exception.code, "LCP-100")
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("email_form_entry", json.dumps(raised.exception.details))
        # Refused ahead of any write: nothing was stored, nothing mislabelled.
        self.assertIsNone(platform.store.get_lead("entry-gate-off"))

    def test_gate_on_still_requires_a_declared_relay_publisher(self) -> None:
        platform = _platform(feature=True, relay_publishers=())
        self.addCleanup(platform.close)
        # No declared entry surface: the flag alone emits nothing and
        # advertises nothing.
        self.assertNotIn("email_form_entry", platform.capabilities()["features"])
        lead = _lead("entry-undeclared", idempotency_key="entry-undeclared-key")
        _submit(platform, lead)
        self.assertEqual(
            _stored_payload(platform, "entry-undeclared")["provenance"]["source_type"],
            "publisher",
        )

    def test_entry_never_invents_client_capture(self) -> None:
        platform = _platform(feature=True)
        self.addCleanup(platform.close)

        _submit(
            platform,
            _lead("entry-no-capture", idempotency_key="entry-no-capture-key"),
        )
        payload = _stored_payload(platform, "entry-no-capture")
        self.assertEqual(payload["provenance"]["source_type"], "email_form")
        for key in CLIENT_CAPTURE_KEYS + ("user_agent", "client"):
            self.assertNotIn(key, payload["provenance"], f"{key} must not be invented")
        self.assertNotIn("user_agent", json.dumps(payload))

        # A lead that *does* carry a client capture is not a captureless relay
        # entry: the platform leaves its provenance alone.
        captured = _lead(
            "entry-with-capture", idempotency_key="entry-with-capture-key", capture=True
        )
        _submit(platform, captured)
        payload = _stored_payload(platform, "entry-with-capture")
        self.assertEqual(payload["provenance"]["source_type"], "publisher")
        self.assertNotIn("acquisition_method", payload["provenance"])

        # A publisher's own declaration is never rewritten either.
        declared = _lead(
            "entry-declared",
            idempotency_key="entry-declared-key",
            source_type="referral",
        )
        _submit(platform, declared)
        self.assertEqual(
            _stored_payload(platform, "entry-declared")["provenance"]["source_type"],
            "referral",
        )


if __name__ == "__main__":
    unittest.main()
