# LCP Python SDK

Standalone Python helpers for LCP validation, envelope construction, HTTP
transport, HMAC signing, and idempotency. The SDK does not depend on MCP and
does not contain platform routing or storage logic.

## Install

From this repository:

```bash
python3 -m pip install -e implementations/sdk/python
```

The package targets Python 3.10+.

## Build a lead envelope

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

## Send over HTTP

```python
from lcp_sdk import LCPClient, SchemaValidator

client = LCPClient(
    "https://platform.example",
    sender_id="publisher_001",
    hmac_secret="secret-from-your-secret-manager",
    validator=SchemaValidator(),
)
ack = client.submit_lead(lead)
```

HMAC requests use the approved canonical input:

```text
<timestamp>\n<idempotency-key>\n<raw-request-body>
```

The client sends `X-LCP-Timestamp`, `X-LCP-Idempotency-Key`, and
`X-LCP-Signature`. The request body is signed exactly as transmitted.

## Schema location

Released wheels bundle the canonical `schemas/` and `verticals/` directories.
When running from a repository checkout, the SDK discovers the repository
copies automatically. Installed deployments can override them with:

```bash
export LCP_SCHEMA_DIR=/path/to/lcp/schemas
```

## Publishing

The repository includes a GitHub Actions Trusted Publishing workflow for the
SDK, MCP adapter, and reference platform. Before tagging a release, configure
one PyPI Trusted Publisher for each package and keep the package versions
aligned with the release process. The workflow publishes on `v*` tags; it does
not require a long-lived PyPI token in GitHub secrets.

## Scope

The SDK intentionally does not implement buyer offer matching, auctions,
webhook workers, or persistence. Those belong to the reference platform or
the operator's deployment.
