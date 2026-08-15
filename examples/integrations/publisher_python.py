#!/usr/bin/env python3
"""Send one synthetic/example lead to an LCP endpoint.

Required environment variables:
  LCP_ENDPOINT, LCP_SENDER_ID, LCP_RECEIVER_ID, LCP_HMAC_SECRET
Optional: LCP_TEST_MODE=true for a sandbox endpoint.
Never run this example with real consumer data until its persistence, consent,
privacy, and retry handling have been reviewed for the deployment.
"""

from __future__ import annotations

import json
import os
from uuid import uuid4

from lcp_sdk import LCPClient, SchemaValidator, build_envelope


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def main() -> None:
    sender_id = os.environ["LCP_SENDER_ID"]
    receiver_id = os.environ["LCP_RECEIVER_ID"]
    test_mode = env_flag("LCP_TEST_MODE")
    lead_id = f"example-lead-{uuid4().hex}"
    envelope = build_envelope(
        "lead",
        sender_id=sender_id,
        receiver_id=receiver_id,
        test=test_mode,
        payload={
            "lead_id": lead_id,
            "external_id": f"source-{uuid4().hex[:12]}",
            "status": "NEW",
            "channel": "form",
            "consumer": {
                "full_name": "Synthetic Example",
                "phone": "+61412345678",
                "email": "synthetic@example.invalid",
            },
            "location": {
                "country_code": "AU",
                "state_region": "NSW",
                "postal_code": "2000",
            },
            "compliance": {
                "consent_timestamp": envelope_timestamp(),
                "consent_purposes": ["calls", "email", "share_with_partners"],
                "consent_evidence": [
                    {
                        "type": "example_consent",
                        "provider": "synthetic_fixture",
                        "token_or_url": "fixture-consent-001",
                    }
                ],
            },
            "provenance": {
                "source_type": "publisher",
                "acquisition_method": "paid_ad",
                "platform_source": "synthetic_fixture",
            },
            "attributes": {
                "vertical": "mortgage",
                "schema_version": "1.0.0",
                "loan_type": "refinance",
                "loan_amount_band": "500k_750k",
            },
        },
    )

    client = LCPClient(
        os.environ["LCP_ENDPOINT"],
        sender_id=sender_id,
        hmac_secret=os.environ["LCP_HMAC_SECRET"],
        max_retries=2,
        validator=SchemaValidator(os.environ.get("LCP_SCHEMA_DIR")),
    )
    response = client.submit_lead(envelope, test=test_mode)
    print(json.dumps(response, indent=2, sort_keys=True))


def envelope_timestamp() -> str:
    from lcp_sdk import utc_timestamp

    return utc_timestamp()


if __name__ == "__main__":
    main()
