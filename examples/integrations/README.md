# Integration examples

These examples are intentionally small starting points for partner adapters.
They use synthetic data and do not replace durable outbox/idempotency storage,
secret management, privacy controls, TLS/WAF, or a production web framework.

## Publisher examples

Install the SDK from the repository or your released package:

```bash
python3 -m pip install -e implementations/sdk/python
export LCP_ENDPOINT=https://platform.example
export LCP_SENDER_ID=publisher_001
export LCP_RECEIVER_ID=platform_001
export LCP_HMAC_SECRET='read-from-a-secret-manager'
```

Run one of:

```bash
python3 examples/integrations/publisher_python.py
node examples/integrations/publisher_node.mjs
# For the repository fixture, use its matching sender_id (pub_123), or provide
# your own body whose lcp.message.sender_id matches LCP_SENDER_ID.
LCP_SENDER_ID=pub_123 LCP_BODY=examples/lead.json bash examples/integrations/publisher_curl.sh
```

For a sandbox, point `LCP_ENDPOINT` at the sandbox, use its test credential,
and set `LCP_TEST_MODE=true`. The JSON body and headers must agree on the test
marker. Do not point these examples at production while using synthetic
credentials or fixture PII.

## Buyer example

The buyer template listens for platform webhooks, verifies the canonical HMAC
signature, validates the envelope, submits an auction bid, and demonstrates
where an idempotent CRM handoff belongs:

```bash
export LCP_BUYER_ID=buyer_001
export LCP_PLATFORM_ID=platform_001
export LCP_PLATFORM_ENDPOINT=https://platform.example
export LCP_BUYER_HMAC_SECRET='read-from-a-secret-manager'
python3 examples/integrations/buyer_webhook.py
```

The in-memory `SEEN_KEYS` set is deliberately not production-safe. Replace it
with a durable claim keyed by the incoming idempotency key/message ID before
accepting real posts. Use a framework with explicit request-size, timeout,
TLS, authentication, and observability controls for a live endpoint.

## Related documentation

- [Publisher onboarding](../../docs/PUBLISHER-ONBOARDING.md)
- [Buyer onboarding](../../docs/BUYER-ONBOARDING.md)
- [HTTP/OpenAPI contract](../../api/lcp-openapi.yaml)
- [Python SDK](../../implementations/sdk/python/README.md)
- [Synthetic sandbox](../sandbox/README.md)
