# Publisher onboarding

This guide is for a publisher that captures leads from forms, calls, chat,
marketplaces, ads, or an internal application and sends them to an LCP
platform or buyer. LCP is the wire contract; the publisher still owns the
source-system adapter, consent records, lawful basis, and local privacy
obligations.

## 1. Choose the receiving model

Ask the platform operator for:

- the HTTPS LCP base URL;
- the platform receiver ID;
- a dedicated sender ID for this publisher and environment;
- the accepted verticals, countries, channels, and schema versions;
- the authentication method (HMAC is recommended for server-to-server
  publishing; Bearer can be used when the operator provisions API keys);
- the idempotency and duplicate-retention window;
- the test/sandbox endpoint and test credential; and
- the commercial definition of an accepted, payable, disputed, or returned
  lead.

The normal publisher flow is:

```text
capture → normalize → validate → persist an outbox record → sign → submit
       → record the acknowledgement → retry only with the same idempotency key
```

Do not send a lead directly from a browser. Submit from a server-side adapter
so credentials, consent evidence, and retry state are not exposed to a
consumer or an ad platform.

## 2. Install an implementation

LCP is JSON over HTTPS and can be implemented without an SDK. For Python, the
repository SDK provides envelope construction, validation, HMAC signing,
HTTP retries, and idempotency helpers:

```bash
python3 -m pip install lcp-sdk  # use the released package when available
# Or, from this repository:
python3 -m pip install -e implementations/sdk/python
```

The copy-paste examples are in [`examples/integrations/`](../examples/integrations/):

- `publisher_python.py` — Python SDK example;
- `publisher_node.mjs` — dependency-free Node.js 18+ example;
- `publisher_curl.sh` — cURL/OpenSSL signing template; and
- `buyer_webhook.py` — buyer receiver template for the other side of the
  connection.

`examples/templates/` contains sanitized offer and lead templates. Synthetic
fixtures must not be used with real consumer data.

## 3. Map source data to LCP

Build a deterministic mapping from the source platform into the canonical
`lead` payload:

| Source concern | LCP location | Publisher responsibility |
|---|---|---|
| Stable source ID | `payload.external_id` | Preserve the source record ID; do not use it as the LCP lead ID unless the ID is globally unique. |
| New LCP ID | `payload.lead_id` | Generate a stable ID before enqueueing. Keep it stable across retries. |
| Name/contact | `payload.consumer` | Normalize phone to E.164. Do not invent or silently overwrite values. |
| Country and service area | `payload.location` | Send ISO 3166-1 alpha-2 `country_code` plus state/postal data where lawful. |
| Product or vertical | `payload.attributes` | Include `vertical` and `schema_version`; use the matching vertical schema. |
| Acquisition source | `payload.provenance` | Record source type, acquisition method, campaign, and creative IDs without raw tracking secrets. |
| Consent | `payload.compliance` | Preserve timestamp, source URL, text version, purposes, and evidence reference. Never send an OTP code. |
| Contact preference | `payload.contact_window` and consumer fields | Carry a consumer's stated availability and preferred contact method. |
| Duplicate policy | `message.idempotency_key` | Generate once per logical submission and persist it in an outbox. |

A publisher-provided quality signal is a declaration, not an LCP-certified
fact. Buyers should be able to distinguish `lead_quality` and provenance
signals from independently verified platform checks.

## 4. Authenticate and submit

For HMAC, sign the exact bytes sent on the wire:

```text
<timestamp>\n<idempotency-key>\n<raw-request-body>
```

Send these headers on a mutating request:

```text
Content-Type: application/json
X-LCP-Sender-Id: publisher_001
X-LCP-Timestamp: 2026-08-15T10:20:00Z
X-LCP-Idempotency-Key: publisher_001-lead-8f1d
X-LCP-Signature: <lowercase-hex-HMAC-SHA256>
```

The header idempotency key must exactly match
`lcp.message.idempotency_key`. Keep the HMAC secret in a secret manager and
rotate it through the operator's documented previous-key window. Never log
request bodies, signatures, credentials, or consumer PII.

Minimal Python shape:

```python
from lcp_sdk import LCPClient, SchemaValidator, build_envelope

lead = build_envelope(
    "lead",
    sender_id="publisher_001",
    receiver_id="platform_001",
    payload={
        "lead_id": "lead_8f1d",
        "status": "NEW",
        "channel": "form",
        "consumer": {"full_name": "Synthetic Example", "phone": "+61412345678"},
        "location": {"country_code": "AU", "postal_code": "2000"},
        "attributes": {"vertical": "mortgage", "schema_version": "1.0.0"},
    },
)

client = LCPClient(
    "https://platform.example",
    sender_id="publisher_001",
    hmac_secret="read-from-a-secret-manager",
    validator=SchemaValidator(),
)
ack = client.submit_lead(lead)
```

The full examples read credentials from environment variables and are safe to
adapt. Configure a separate `test: true` credential and endpoint for the
sandbox. Test requests must carry both envelope `test: true` and
`X-LCP-Test: true`; never mix test and production credentials.

## 5. Handle acknowledgements and failures

Treat the HTTP status and the LCP acknowledgement together:

- `2xx` with `RECEIVED` or `VALIDATED`: persist the acknowledgement and mark
  the outbox item accepted for processing;
- `DUPLICATE`: mark the outbox item complete; do not create a new lead;
- `4xx` validation/authentication errors: fix the message, credential, or
  configuration before retrying; blind retries amplify incidents;
- `409` idempotency conflict: stop and investigate key reuse with different
  content; and
- `429`, `500`, `502`, `503`, or `504`: retry with bounded exponential backoff
  and jitter, respecting `Retry-After` where present.

For network timeouts, the outcome is unknown. Retry the exact same body and
idempotency key after backoff; never generate a new lead ID merely because the
first response was lost. Keep a durable outbox and a dead-letter path for
messages that exceed the retry policy.

## 6. Test before production

1. Validate all envelopes locally against the published schemas.
2. Submit a synthetic lead to the operator's sandbox.
3. Verify the receiver acknowledges it, applies the expected offer, and emits
   the expected lifecycle events.
4. Test duplicate submission, malformed payloads, stale signatures, wrong
   receiver IDs, consent failure, and rate limiting.
5. Confirm no sandbox message reaches a production buyer or CRM.
6. Capture the test evidence and the exact SDK/schema versions used.

The repository sandbox exercises the same platform code path:

```bash
docker compose -f implementations/reference-platform/docker-compose.yml up --build
python3 examples/sandbox/publisher.py
```

## 7. Go-live checklist

- [ ] A written data-processing and delivery agreement exists with the
      platform/buyer.
- [ ] Country/state residency, retention, deletion, and cross-border transfer
      rules have been reviewed for every affected jurisdiction.
- [ ] Consent evidence and lawful-purpose records are retained according to
      policy, with no raw OTP or unnecessary secrets.
- [ ] Production HTTPS certificate validation is enabled.
- [ ] Production HMAC secret is stored and rotated through the agreed process.
- [ ] The publisher has a durable outbox, retry/dead-letter process, and alert
      for delivery lag.
- [ ] Idempotency behavior has been tested with a lost response.
- [ ] Logs and traces redact consumer PII and authorization headers.
- [ ] Production and sandbox endpoints, credentials, databases, and CRM
      destinations are separate.
- [ ] Schema and vertical versions are pinned and upgraded deliberately.

For platform-specific source mappings, see the
[platform integration guide](PLATFORM-INTEGRATION.md). For the transport
contract, use the [OpenAPI definition](../api/lcp-openapi.yaml) and
[canonical specification](../SPEC.md).
