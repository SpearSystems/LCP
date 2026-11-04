# Buyer webhook receiver — verify a signed LCP delivery in one command

> Example — not for production. Durable idempotency, TLS/WAF, secret storage,
> and CRM handoff belong to your deployment. In-memory de-duplication here is
> a demo affordance, not a durability guarantee.

A buyer's endpoint must prove it accepted a `post` (or a subscribed `event`)
before the platform will treat the delivery as received: verify the HMAC over
the exact bytes, answer `200` with an `ack` envelope, inside the 30-second
post window. This example does exactly that, in Python (`http.server`) or Node
(`node:http`), standard library only.

## Quickstart

```bash
export LCP_WEBHOOK_SECRET='the-pair-secret-the-platform-issued-you'
python3 examples/integrations/buyer-webhook/python/receiver.py --port 8090
# or, same behaviour:
node examples/integrations/buyer-webhook/node/receiver.mjs --port 8090
```

The receiver prints `listening on http://0.0.0.0:8090` and serves:

| Route | Purpose |
|---|---|
| `POST /` (any path) | verify a delivery, answer with an `ack` envelope |
| `GET /healthz` | liveness for containers and probes |

Flags (both receivers): `--port` (default `8090`, `0` = ephemeral), `--host`
(default `0.0.0.0`), `--secret` (or `LCP_WEBHOOK_SECRET`), `--print-body`
(prints each **verified** body; rejections never print one).

## What the platform sends

`post` delivery (spec/01-transport.md §9):

```text
POST / HTTP/1.1
Content-Type: application/json
Accept: application/json
X-LCP-Sender-Id: platform_001
X-LCP-Timestamp: 2026-08-15T10:20:10Z
X-LCP-Idempotency-Key: platform_001-post-<message id>
X-LCP-Signature: <lowercase-hex-hmac-sha256>
X-LCP-Test: true            # only on test traffic
```

Signature input — `<timestamp>\n<idempotency-key>\n<raw body bytes>`:

```text
X-LCP-Timestamp + "\n" + X-LCP-Idempotency-Key + "\n" + exact request body
```

Event subscription delivery (docs/OPERATIONS.md, "Event subscriptions"):

```text
POST / HTTP/1.1
Content-Type: application/json
X-LCP-Sender-Id: platform_001
X-LCP-Timestamp: 2026-08-15T14:30:00Z
X-LCP-Idempotency-Key: 0f2c...        # equal to X-LCP-Event-Id on this surface
X-LCP-Event-Id: 0f2c...
X-LCP-Subscription-Id: 7b41...
X-LCP-Signature: <lowercase-hex-hmac-sha256>
```

Signature input — `<timestamp>\n<event-id>\n<raw body bytes>`. The event id,
**not** the envelope's `idempotency_key`, is the middle term; that is the only
difference between the two surfaces.

The digest is HMAC-SHA256, lowercase hex, over the received header strings and
the received bytes — never a re-serialized body and never a reformatted
timestamp. The replay window is 300 seconds by default.

Two scope notes: this receiver answers `post` and `event` deliveries, so a
`ping` (auction offers) is refused with `400`/`LCP-006` — use
[`buyer_webhook.py`](../buyer_webhook.py) if your offer bids. And it accepts
`X-LCP-Test: true` traffic; a production route SHOULD refuse it
(`LCP-013 TEST_TRAFFIC_POLICY`, docs/BUYER-ONBOARDING.md §4) once test and
production credentials are split.

## The ack the receiver returns

`200` with a full `ack` envelope — the delivery is acknowledged the moment this
is written, well inside the default 30-second window:

```json
{
  "lcp": {
    "version": "1.0.0",
    "message": {
      "id": "<new uuid>",
      "type": "ack",
      "timestamp": "2026-08-15T10:20:10Z",
      "sender_id": "buyer_001",
      "receiver_id": "platform_001",
      "correlation_id": "<post message id>",
      "idempotency_key": "buyer_001-ack-<ack id>",
      "test": false
    },
    "payload": {
      "original_message_id": "<post message id>",
      "status": "RECEIVED",
      "lead_id": "lead_abc123",
      "request_id": "req_1f2e3d4c5b6a"
    }
  }
}
```

`sender_id` is the address the delivery was sent to, `receiver_id` the platform
that sent it, and `correlation_id` the acknowledged message. A replayed
delivery still answers `200`, with `payload.status = "DUPLICATE"`; the platform
discards the body and only reads the status code, but a truthful ack keeps
your logs and its retry accounting aligned.

Refusals are `401` with the LCP error shape and nothing else:

```json
{"errors": [{"code": "LCP-012", "name": "SIGNATURE_INVALID", "message": "signature verification failed"}]}
```

The reason (missing header, stale timestamp, digest mismatch, key disagreement
with the envelope) is logged to stderr as a phrase only — never the body, the
secret, or consumer data.

## Point a buyer offer at it

Set the offer's `webhook_url` to this endpoint — that field is the delivery
target for `ping`/`post` traffic on the offer (`schemas/offer.json` §delivery):

```json
{"delivery": {"webhook_url": "https://buyer.example.com/lcp/webhook"}}
```

Use HTTPS in production. The reference platform refuses private, loopback, and
link-local destinations (`validate_webhook_url` + `validate_egress_host`), so
`http://127.0.0.1:8090` is rejected unless the deployment sets
`LCP_ALLOW_INSECURE_WEBHOOKS` — a sandbox-only switch. Event subscriptions use
the same rule for `callback_url` on `POST /v1/lcp/events/subscribe`.

## Exposing a laptop endpoint

The platform must reach your receiver over the public internet while you
develop, so put a tunnel in front of it and give the tunnel's HTTPS URL as the
`webhook_url`:

```bash
cloudflared tunnel --url http://localhost:8090     # prints a trycloudflare.com URL
ngrok http 8090                                    # prints an ngrok-free.app URL
```

Both have free tiers; use whichever you already have. Treat the tunnel URL as
a credential (it is a bearer capability for leads) and close the tunnel when
you are done. Point the offer at the tunnel host, not at your laptop.

## Checking it works

```bash
python3 examples/integrations/buyer-webhook/selftest.py
node --check examples/integrations/buyer-webhook/node/receiver.mjs
```

The self-test starts the Python receiver on an ephemeral port and asserts, per
surface: `200` + `ack` shape for a correctly signed delivery, `401`/`LCP-012`
for a body changed after signing, and a `DUPLICATE` ack for a replay; the
rejection check also asserts the response never echoes the request body. It is
a documented manual check, not a CI job.

## Container

```bash
docker build -t lcp-buyer-webhook examples/integrations/buyer-webhook
docker run --rm -p 8090:8090 --read-only \
  -e LCP_WEBHOOK_SECRET='the-pair-secret' lcp-buyer-webhook
```

The image runs as uid `10001`, writes nothing, and healthchecks `GET /healthz`.
Do not bake the secret into an image — inject it at run time.
