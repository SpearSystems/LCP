# Buyer onboarding
> **Buyer page · Page 2 of 6**

This guide is for an advertiser, network, CRM, call centre, or downstream
platform that wants to receive LCP leads. A buyer may operate a receiving
endpoint, publish offers through a platform operator, or do both.

## 1. Choose a buyer model

### Buyer-operated receiver

A platform sends direct `post` messages or winning auction posts to your HTTPS
webhook. Your endpoint validates, deduplicates, stores or forwards the post,
and returns a fast acknowledgement.

```text
LCP platform → signed post webhook → buyer intake → CRM/dialler/compliance
```

### Buyer in an auction

The platform sends a PII-free `ping`. Your system decides whether the lead
fits current capacity and commercial criteria, then submits a signed `bid` to
the platform before the ping expires. Full PII is sent only if you win.

```text
platform → ping (no PII) → buyer decision → bid → winning post (full PII)
```

### Buyer-operated platform

A buyer or technology provider can run the reference platform and accept
publishers directly. Configure credentials, offers, routing tenants, a
Postgres deployment, delivery workers, and the controls in the
[deployment](DEPLOYMENT.md) and [security architecture](SECURITY-ARCHITECTURE.md)
documents.

## 2. Provision a bilateral credential

Request or create a dedicated credential for each buyer, platform, and
environment. At minimum, an auction buyer commonly needs:

- `bid:submit` to send bids;
- `event:submit` to send call outcomes and lifecycle updates;
- `lead:read` to query the status of leads delivered to that buyer;
- authenticated attachment access for documents included in a winning post; and
- `offer:read` if the operator exposes offer discovery.

Use a separate HMAC secret for each bilateral integration. The platform signs
webhook messages with the buyer's shared secret, and the buyer signs bids with
the same agreed secret. Do not reuse a publisher credential, a human admin
credential, or a secret across tenants.

The reference platform's local administration example is:

```bash
lcp-platform-admin credential upsert \
  --sender-id buyer_001 \
  --tenant-id buyer_tenant \
  --scope bid:submit --scope lead:read --scope offer:read \
  --hmac-secret '<secret-from-your-secret-manager>'
```

In production, prefer `LCP_SECRETS_FILE` backed by a secret manager and keep
credentials out of shell history. The command above is for controlled local
administration only.

## 3. Publish an offer

An offer is an acceptance profile, not a promise that every matching lead
will be delivered. Agree its commercial and compliance meaning with the
platform operator. Criteria can include:

- vertical, schema version, country, state, postal code, and channel;
- acquisition/source restrictions and incentive policy;
- verified phone/email requirements;
- spam-risk, completeness, DNC, litigator, and blacklist rules;
- delivery windows and time zone;
- daily/hourly capacity;
- floor price, currency, and payable definition;
- structured call payable rules and real-time/post-call mode;
- monthly minimum/maximum payable targets and pacing policy; and
- direct delivery or auction routing.

A reusable offer template is
[`examples/templates/offer-auction.json`](../examples/templates/offer-auction.json).
A reference-platform operator can validate and store it with:

```bash
lcp-platform-admin offer upsert --file offer-auction.json
```

Keep webhook URLs on HTTPS in production. The reference platform blocks
private, loopback, link-local, and other unsafe egress destinations unless an
explicit test-only policy is enabled.

## 4. Receive and verify webhooks

The buyer webhook must:

1. enforce HTTPS at the edge and a strict body/header limit;
2. read the raw request bytes without reserializing JSON;
3. verify `X-LCP-Sender-Id`, timestamp freshness, idempotency key, and
   HMAC-SHA256 signature;
4. reject test messages on production routes;
5. validate the complete envelope and message-specific schema;
6. atomically claim the idempotency key before side effects; and
7. return a small `2xx` or `409` response only after the durable handoff.

The canonical signature input is:

```text
<timestamp>\n<idempotency-key>\n<raw-request-body>
```

Python verification outline:

```python
import json
import os
from lcp_sdk import SchemaValidator, verify_hmac

raw_body = request.get_data(cache=False)
verify_hmac(
    os.environ["LCP_PLATFORM_HMAC_SECRET"],
    request.headers["X-LCP-Signature"],
    request.headers["X-LCP-Timestamp"],
    request.headers["X-LCP-Idempotency-Key"],
    raw_body,
)
envelope = json.loads(raw_body)
SchemaValidator().require_valid_envelope(envelope)
```

The [`buyer_webhook.py`](../examples/integrations/buyer_webhook.py) example
shows the same flow with Python's standard library. It uses an in-memory
idempotency set for demonstration only; replace that set with a database
unique constraint or durable queue before production.

Never log the raw body, consumer fields, HMAC secret, or authorization
headers. Store only the minimum PII needed for the agreed purpose and retain
an audit reference for the delivery.

## 5. Respond to pings

A ping contains no full consumer PII. Evaluate it against the offer and
current capacity:

- `accept`: send a bid with a price at or above the offer floor;
- `reject`: explicitly decline with a reason such as `capacity_full` or
  `vertical_mismatch`; or
- `pass`: decline without a commercial bid.

The bid must be an LCP envelope with:

- `message.type = "bid"`;
- `message.sender_id` equal to the buyer credential;
- `message.receiver_id` equal to the platform ID;
- `message.correlation_id` equal to the ping message ID;
- the ping's `ping_id`; and
- a stable idempotency key unique to that bid attempt.

Submit the bid before the `expires_at`/ping timeout. A late bid may be
rejected even when the HTTP request itself succeeds.

## 6. Handle posts, attachments, and lifecycle events

Treat `post` delivery as at-least-once. Use the LCP `message.id` or
`idempotency_key` as a durable unique key, and make CRM/dialler writes
idempotent. A `409` for a previously accepted message is a successful
idempotent outcome for the webhook.

Before contacting a consumer, apply buyer-side controls again:

- consent and purpose match;
- DNC and jurisdictional rules;
- dispute/return window;
- contact window and channel preference;
- lead freshness and duplicate checks; and
- internal suppression, fraud, and capacity policy.

The platform may send a `DELIVERED` event after a post webhook succeeds. Keep
an auditable mapping between `lead_id`, `offer_id`, buyer reference, CRM ID,
and any billing or dispute record. Do not treat delivery alone as consumer
consent or conversion.

If a post contains `attachments[]`, fetch the bytes through the authenticated
attachment endpoint only after verifying the metadata hash and applying your
own malware, residency, retention, and authorization controls. Never treat
`storage_ref` as a public URL.

For call offers, send a signed `CALL_OUTCOME` event with the offer ID, answer
state, duration, disposition, and transfer status. The platform evaluates the
offer's `payable_rules` and updates the monthly quota. See [calls and
telephony](CALLS-AND-TELEPHONY.md) and [monthly quotas](MONTHLY-QUOTAS.md).

## 7. Test before production

Use a separate buyer credential, webhook URL, database, and CRM sandbox. Test:

1. valid ping verification and bid submission;
2. malformed and stale signatures;
3. duplicate webhook delivery;
4. a webhook timeout followed by a retry;
5. a `post` with a lost response;
6. a post that fails local compliance checks;
7. an expired ping and a late bid;
8. a buyer at capacity; and
9. test-marker rejection on the production endpoint;
10. call outcome rules, attachment retrieval, and monthly quota reporting.

The repository sandbox demonstrates the complete synthetic publisher → ping →
bid → post → delivery-event path:

```bash
docker compose -f implementations/reference-platform/docker-compose.yml up --build
python3 examples/sandbox/publisher.py
```

## 8. Go-live checklist

- [ ] Buyer endpoint is public only through an approved TLS/WAF boundary.
- [ ] Webhook signature verification uses raw bytes and a replay window.
- [ ] Idempotency is durable and survives process restarts.
- [ ] CRM/dialler side effects are deduplicated.
- [ ] Buyer and platform HMAC secrets are stored and rotated safely.
- [ ] Offer criteria, floor, currency, payable definition, and dispute window
      are documented and agreed.
- [ ] Capacity, delivery-window, timeout, retry, and dead-letter alerts exist.
- [ ] PII retention, deletion, residency, and downstream processor controls
      are documented for each jurisdiction.
- [ ] Production and sandbox destinations cannot cross-connect.
- [ ] The buyer can prove which offer, price, consent evidence, and delivery
      event applied to each lead.

For the exact HTTP contract, see the [OpenAPI definition](../api/lcp-openapi.yaml).
For routing behavior, see [implementation decisions](IMPLEMENTATION-DECISIONS.md).

---

**Previous:** [Monthly payable quotas](MONTHLY-QUOTAS.md) · **Next:** [Platform integration](PLATFORM-INTEGRATION.md)
