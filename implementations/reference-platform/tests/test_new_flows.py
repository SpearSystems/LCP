from __future__ import annotations

from hashlib import sha256
import base64
import json
from pathlib import Path
import tempfile
import time
import unittest

from lcp_platform.attachments import AttachmentError, ClamAVMalwareScanner, S3ObjectStorageAttachmentStore
from lcp_platform.config import PlatformConfig
from lcp_platform.messages import _envelope
from lcp_platform.router import Platform
from lcp_platform.service import PlatformService


ROOT = Path(__file__).resolve().parents[3]


class NewFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="spx_platform",
                require_auth=False,
                allow_insecure_webhooks=True,
                test_mode=True,
                pii_encryption_key=base64.urlsafe_b64encode(b"k" * 32).decode(),
                attachment_directory=Path(self.tempdir.name) / "attachments",
            )
        )
        self.platform.upsert_credential("buyer_mva", hmac_secret="buyer-mva-secret")

    def tearDown(self) -> None:
        self.platform.close()
        self.tempdir.cleanup()

    def test_versioned_publisher_mapping_normalizes_otp_and_audits_without_source_pii(self) -> None:
        self.platform.upsert_mapping(
            {
                "mapping_id": "brand-01-gutters-v3",
                "publisher_id": "publisher_brand_01",
                "brand_id": "brand_01",
                "form_key": "gutters-form-v3",
                "version": "3.0.0",
                "active": True,
                "vertical": "home_services",
                "schema_version": "1.0.0",
                "channel": "form",
                "country_code": "AU",
                "field_map": {
                    "lead_id": "submission.id",
                    "consumer.full_name": "contact.name",
                    "consumer.phone": "contact.phone",
                    "consumer.email": "contact.email",
                    "location.country_code": "address.country",
                    "location.state_region": "address.state",
                    "location.postal_code": "address.postcode",
                    "attributes.service_type": "answers.service",
                    "attributes.service_subtype": "answers.job_type",
                    "compliance.consent_evidence": "consent.evidence",
                },
                "transforms": {
                    "consumer.phone": "e164",
                    "consumer.email": "lower",
                    "location.country_code": "upper",
                },
                "value_maps": {
                    "attributes.service_type": {"roof_guttering": "gutters"},
                    "attributes.service_subtype": {"replace": "replacement"},
                },
                "otp": {
                    "verified_path": "verification.otp_verified",
                    "channel_path": "verification.channel",
                    "verified_at_path": "verification.verified_at",
                    "method": "otp",
                },
            }
        )
        self.platform.upsert_offer(
            {
                "offer_id": "buyer-gutters",
                "buyer_id": "buyer_mva",
                "active": True,
                "routing_mode": "auction",
                "vertical": "home_services",
                "countries": ["AU"],
                "allowed_publisher_ids": ["publisher_brand_01"],
                "attribute_equals": {"service_type": "gutters"},
                "floor_price_cents": 1000,
                "currency": "AUD",
            }
        )
        source = {
            "submission": {"id": "source-001"},
            "contact": {"name": "Synthetic Consumer", "phone": "0412 345 678", "email": "SYNTHETIC@EXAMPLE.COM"},
            "address": {"country": "au", "state": "NSW", "postcode": "2000"},
            "answers": {"service": "roof_guttering", "job_type": "replace"},
            "consent": {"evidence": [{"type": "verified_consent", "provider": "internal", "token_or_url": "synthetic-evidence"}]},
            "verification": {"otp_verified": True, "channel": "sms", "verified_at": "2026-08-15T10:00:00Z"},
        }
        envelope = self.platform.normalize_publisher_record(
            source,
            publisher_id="publisher_brand_01",
            form_key="gutters-form-v3",
            test=True,
        )
        payload = envelope["lcp"]["payload"]
        self.assertEqual(payload["consumer"]["phone"], "+61412345678")
        self.assertEqual(payload["consumer"]["email"], "synthetic@example.com")
        self.assertEqual(payload["attributes"]["service_type"], "gutters")
        self.assertTrue(payload["compliance"]["otp_verified"])
        self.assertTrue(payload["lead_quality"]["verified_phone"])
        self.assertEqual(payload["provenance"]["brand_id"], "brand_01")
        self.assertEqual(payload["provenance"]["form_id"], "gutters-form-v3")
        self.platform.ingest(envelope, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        self.assertEqual(len(self.platform.store.list_pings("source-001")), 1)
        audit = self.platform.store.list_mapping_applications("source-001")
        self.assertEqual(audit[0]["mapping_id"], "brand-01-gutters-v3")
        self.assertNotIn("synthetic@example.com", json.dumps(audit))

    def test_http_attachment_endpoints_preserve_raw_bytes(self) -> None:
        content = b"synthetic HTTP attachment"
        digest = sha256(content).hexdigest()
        headers = {
            "X-LCP-Test": "true",
            "X-LCP-Sender-Id": "publisher_http",
            "X-LCP-Lead-Id": "http-lead-001",
            "X-LCP-Attachment-Id": "att_http_001",
            "X-LCP-Attachment-Purpose": "police_report",
            "X-LCP-Filename": "report.pdf",
            "Content-Type": "application/pdf",
            "X-LCP-Content-SHA256": digest,
            "X-LCP-Idempotency-Key": "publisher-http-attachment-001",
        }
        service = PlatformService(self.platform)
        status, _, response = service.dispatch("POST", "/v1/lcp/attachments", headers=headers, body=content)
        self.assertEqual(status, 201)
        self.assertEqual(response["attachment_id"], "att_http_001")
        status, response_headers, downloaded = service.dispatch(
            "GET", "/v1/lcp/attachments/att_http_001", headers={"X-LCP-Sender-Id": "publisher_http"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(response_headers["Content-Type"], "application/pdf")
        self.assertEqual(downloaded, content)

    def test_mva_attachment_upload_is_encrypted_access_controlled_and_posted(self) -> None:
        content = b"synthetic signed contract"
        digest = sha256(content).hexdigest()
        attachment = self.platform.upload_attachment(
            headers={
                "X-LCP-Test": "true",
                "X-LCP-Sender-Id": "publisher_mva",
                "X-LCP-Lead-Id": "mva-lead-001",
                "X-LCP-Attachment-Id": "att_contract_001",
                "X-LCP-Attachment-Purpose": "signed_contract",
                "X-LCP-Filename": "synthetic-contract.pdf",
                "Content-Type": "application/pdf",
                "X-LCP-Content-SHA256": digest,
                "X-LCP-Idempotency-Key": "publisher-mva-attachment-001",
            },
            body=content,
        )
        self.assertEqual(attachment["storage_ref"], "lcp://attachments/att_contract_001")
        self.assertEqual(attachment["sha256"], digest)
        stored_path = Path(self.tempdir.name) / "attachments" / "att_contract_001.bin"
        self.assertTrue(stored_path.exists())
        self.assertNotIn(content, stored_path.read_bytes())
        downloaded, _ = self.platform.download_attachment(
            "att_contract_001", headers={"X-LCP-Sender-Id": "publisher_mva"}
        )
        self.assertEqual(downloaded, content)

        call = _mva_call("mva-lead-001")
        call["lcp"]["payload"]["attachments"] = [attachment]
        self.platform.upsert_offer(
            {
                "offer_id": "mva-call-offer",
                "buyer_id": "buyer_mva",
                "active": True,
                "routing_mode": "direct",
                "vertical": "mva",
                "countries": ["AU"],
                "channels": ["call"],
                "floor_price_cents": 2500,
                "currency": "AUD",
                "webhook_url": "http://127.0.0.1:9/buyer",
                "payable_rules": {"mode": "call_outcome", "require_call_answered": True, "minimum_call_seconds": 30},
                "monthly_minimum_payable": 300,
                "monthly_quota_policy": "pace",
            }
        )
        ack = self.platform.ingest(call, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        deliveries = [row for row in self.platform.store.deliveries_for_lead("mva-lead-001") if row["kind"] == "post"]
        self.assertEqual(len(deliveries), 1)
        post = self.platform.store.decode_envelope(deliveries[0]["envelope_json"])
        self.assertEqual(post["lcp"]["payload"]["call"]["call_id"], "call-mva-001")
        self.assertEqual(post["lcp"]["payload"]["attachments"][0]["attachment_id"], "att_contract_001")

    def test_call_outcome_marks_payable_and_reports_monthly_pacing(self) -> None:
        offer = {
            "offer_id": "mva-auction-offer",
            "buyer_id": "buyer_mva",
            "active": True,
            "routing_mode": "auction",
            "vertical": "mva",
            "countries": ["AU"],
            "channels": ["call"],
            "floor_price_cents": 2500,
            "currency": "AUD",
            "webhook_url": "http://127.0.0.1:9/buyer",
            "ping_timeout_seconds": 1,
            "payable_rules": {"mode": "call_outcome", "require_call_answered": True, "minimum_call_seconds": 30},
            "monthly_minimum_payable": 300,
            "monthly_quota_policy": "pace",
        }
        self.platform.upsert_offer(offer)
        call = _mva_call("mva-lead-002")
        self.platform.ingest(call, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        ping = self.platform.store.list_pings("mva-lead-002")[0]
        ping_envelope = self.platform.store.decode_envelope(ping["envelope_json"])
        bid = _envelope(
            "bid",
            "buyer_mva",
            "spx_platform",
            {
                "ping_id": ping["ping_id"],
                "decision": "accept",
                "bid_price_cents": 3200,
                "currency": "AUD",
                "estimated_contact_seconds": 3,
            },
            correlation_id=ping_envelope["lcp"]["message"]["id"],
            test=True,
        )
        self.platform.submit_bid(bid, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        time.sleep(1.1)
        self.platform.process_once()
        post_delivery = [row for row in self.platform.store.deliveries_for_lead("mva-lead-002") if row["kind"] == "post"]
        self.assertEqual(len(post_delivery), 1)
        event = _envelope(
            "event",
            "buyer_mva",
            "spx_platform",
            {
                "lead_id": "mva-lead-002",
                "event": "CALL_OUTCOME",
                "timestamp": "2026-08-15T10:05:00Z",
                "details": {
                    "offer_id": "mva-auction-offer",
                    "call_status": "answered",
                    "total_seconds": 65,
                    "disposition": "qualified_lead",
                },
            },
            test=True,
        )
        ack = self.platform.submit_event(event, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        quota = self.platform.quota_status("mva-auction-offer")
        self.assertEqual(quota["summary"]["payable"], 1)
        self.assertEqual(quota["minimum_payable"], 300)
        self.assertEqual(quota["payable_remaining_to_minimum"], 299)

    def test_s3_object_storage_uses_kms_and_enforces_residency(self) -> None:
        class FakeObjectBody:
            def __init__(self, value: bytes):
                self.value = value

            def read(self) -> bytes:
                return self.value

        class FakeObjectClient:
            def __init__(self) -> None:
                self.objects: dict[tuple[str, str], dict[str, object]] = {}
                self.put_kwargs: dict[str, object] = {}

            def put_object(self, **kwargs: object) -> None:
                self.put_kwargs = kwargs
                self.objects[(str(kwargs["Bucket"]), str(kwargs["Key"]))] = {
                    "Body": kwargs["Body"], "Metadata": kwargs["Metadata"]
                }

            def head_object(self, **kwargs: object) -> dict[str, object]:
                return self.objects[(str(kwargs["Bucket"]), str(kwargs["Key"]))]

            def get_object(self, **kwargs: object) -> dict[str, object]:
                return {"Body": FakeObjectBody(self.objects[(str(kwargs["Bucket"]), str(kwargs["Key"]))]["Body"])}

            def delete_object(self, **kwargs: object) -> None:
                self.objects.pop((str(kwargs["Bucket"]), str(kwargs["Key"])), None)

        content = b"synthetic object-store contract"
        digest = sha256(content).hexdigest()
        client = FakeObjectClient()
        store = S3ObjectStorageAttachmentStore(
            bucket="lcp-test-bucket",
            prefix="regulated",
            residency="AU",
            kms_key_id="arn:aws:kms:au:123:key/test",
            client=client,
        )
        reference = store.put(
            "att_object_001",
            content,
            sha256_hex=digest,
            content_type="application/pdf",
            filename="contract.pdf",
            residency="AU",
        )
        self.assertEqual(reference, "lcp-object://attachments/att_object_001")
        self.assertEqual(client.put_kwargs["ServerSideEncryption"], "aws:kms")
        self.assertEqual(client.put_kwargs["SSEKMSKeyId"], "arn:aws:kms:au:123:key/test")
        self.assertEqual(client.put_kwargs["Metadata"]["lcp-residency"], "AU")
        self.assertEqual(store.read(reference, expected_sha256=digest), content)
        with self.assertRaises(AttachmentError):
            store.put(
                "att_object_002", content, sha256_hex=digest, content_type="application/pdf",
                filename="contract.pdf", residency="US"
            )
        store.delete(reference)
        with self.assertRaises(AttachmentError):
            store.read(reference, expected_sha256=digest)

    def test_clamav_scanner_fails_closed_for_infected_content(self) -> None:
        class FakeClamAV:
            def __init__(self, result: tuple[str, str | None]) -> None:
                self.result = result

            def instream(self, content: bytes) -> dict[str, tuple[str, str | None]]:
                self.seen = content
                return {"stream": self.result}

        clean = ClamAVMalwareScanner(client=FakeClamAV(("OK", None)))
        self.assertEqual(clean.scan(b"clean", filename="x.pdf", content_type="application/pdf").status, "clean")
        infected = ClamAVMalwareScanner(client=FakeClamAV(("FOUND", "Eicar-Test-Signature")))
        with self.assertRaises(AttachmentError):
            infected.scan(b"malware", filename="x.pdf", content_type="application/pdf")


def _mva_call(lead_id: str) -> dict:
    phone = "+61412345678"
    return _envelope(
        "call",
        "publisher_mva",
        "spx_platform",
        {
            "lead_id": lead_id,
            "status": "NEW",
            "channel": "call",
            "consumer": {"full_name": "Synthetic MVA Consumer", "phone": phone},
            "location": {"country_code": "AU", "state_region": "NSW", "postal_code": "2000"},
            "attributes": {
                "vertical": "mva",
                "schema_version": "1.0.0",
                "accident_type": "car_collision",
                "accident_date_band": "last_7_days",
                "injury_present": True,
                "injury_severity_band": "moderate",
                "medical_treatment": "emergency_room",
                "fault_position": "not_at_fault",
                "consumer_insured": "yes",
                "represented_by_attorney": False,
                "evidence_available": ["signed_contract"],
            },
            "call": {
                "call_id": "call-mva-001",
                "status": "answered",
                "direction": "inbound",
                "caller_phone_hash": sha256(phone.encode()).hexdigest(),
                "started_at": "2026-08-15T10:00:00Z",
                "durations": {"total_seconds": 65, "agent_seconds": 55},
                "disposition": "qualified_lead",
            },
        },
        idempotency_key=f"{lead_id}-idempotency",
        test=True,
    )


if __name__ == "__main__":
    unittest.main()
