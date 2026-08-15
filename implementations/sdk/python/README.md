# LCP Python SDK

Tier 1 maintained reference SDK for LCP publishers, buyers, platforms, and
webhook receivers. It is independent of MCP and does not contain platform
routing or storage logic.

## Install

From the repository while the package is unreleased:

```bash
python3 -m pip install -e implementations/sdk/python
```

When a release is published:

```bash
python3 -m pip install lcp-sdk
```

The package targets Python 3.10+ and bundles the canonical schemas in released
wheels. See the [SDK support policy](../../../docs/SDK-ROADMAP.md) for
compatibility and release gates.

## Build and validate messages

```python
from lcp_sdk import SchemaValidator, build_envelope

lead = build_envelope(
    "lead",
    sender_id="publisher_001",
    receiver_id="platform_001",
    payload={
        "lead_id": "lead_123",
        "status": "NEW",
        "channel": "form",
        "consumer": {"phone": "+61412345678"},
        "location": {"country_code": "AU", "postal_code": "2000"},
        "attributes": {"vertical": "mortgage", "schema_version": "1.0.0"},
    },
)

SchemaValidator().require_valid_envelope(lead)
```

`SchemaValidator` validates the envelope, message payload, offer documents,
and the ping-safe tags in vertical schemas. The released wheel bundles the
canonical Draft 2020-12 schemas and generated `TypedDict` models in
`lcp_sdk.generated.models`; set `LCP_SCHEMA_DIR` when using an external schema
release instead.

## HTTP client

```python
from lcp_sdk import LCPClient, SchemaValidator

client = LCPClient(
    "https://platform.example",
    sender_id="publisher_001",
    hmac_secret="secret-from-your-secret-manager",
    validator=SchemaValidator(),
    max_retries=2,
)
ack = client.submit_lead(lead)
status = client.query_lead_status("lead_123")
offers = client.list_offers(vertical="mortgage")
capabilities = client.get_capabilities()
```

The client provides:

| Method | Operation |
|---|---|
| `submit_lead` | Submit a lead envelope |
| `submit_call` | Submit a call envelope |
| `submit_bid` | Submit a bid envelope |
| `query_lead_status` | Query lead lifecycle/status |
| `get_schema` | Retrieve a core or vertical schema |
| `get_capabilities` | Discover endpoint capabilities |
| `list_offers` | Discover active offers |
| `request` | Make an authenticated LCP HTTP request |

`429`, `500`, `502`, `503`, `504`, and transport errors are retried with
exponential backoff. Mutating requests must use a stable envelope idempotency
key when retrying. Non-success responses raise `LCPHTTPError` with the status
code and parsed error body.

## Authentication and webhook verification

HMAC requests use the approved canonical input:

```text
<timestamp>\n<idempotency-key>\n<raw-request-body>
```

The body must be the exact bytes sent on the wire. The SDK sends
`X-LCP-Timestamp`, `X-LCP-Idempotency-Key`, `X-LCP-Signature`, and
`X-LCP-Sender-Id`. Bearer authentication is also supported.

For a webhook or framework receiver, verify the raw body before JSON parsing:

```python
from lcp_sdk import verify_http_request

headers = {
    "X-LCP-Signature": request.headers["X-LCP-Signature"],
    "X-LCP-Timestamp": request.headers["X-LCP-Timestamp"],
    "X-LCP-Idempotency-Key": request.headers["X-LCP-Idempotency-Key"],
}
verify_http_request("partner-secret", headers, request.get_data())
```

`verify_http_request` enforces the signature and replay window. Applications
must still perform durable idempotency claims, authorization, schema
validation, and PII-safe logging.

Low-level helpers are also available: `canonical_signing_bytes`, `sign_hmac`,
and `verify_hmac`.

## Test mode

Pass `test=True` to mutating client methods when sending synthetic traffic.
The SDK emits `X-LCP-Test: true`; the envelope should also have `test=True`.
Production receivers should reject mismatched or unexpected test traffic.

## Tests

```bash
PYTHONPATH=implementations/sdk/python \
  python3 -m unittest discover -s implementations/sdk/python/tests -v
```

The SDK tests include the shared cross-language HMAC fixture in
`test-vectors/sdk/hmac.json`.

## Publishing

The repository includes a GitHub Actions Trusted Publishing workflow for the
SDK, MCP adapter, and reference platform. Before tagging a release, configure
one PyPI Trusted Publisher for each package and keep package versions aligned
with the release process. The workflow publishes on `v*` tags; it does not
require a long-lived PyPI token in GitHub secrets.

## Scope

The SDK intentionally does not implement buyer offer matching, auctions,
webhook queues, CRM handoff, or persistence. Those belong to the reference
platform or the operator's deployment.
