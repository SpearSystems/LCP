# LCP error guide

Every structured error LCP receivers return, in plain English, with the three
most common causes and the exact field-level fix. The same data powers
`lcp-doctor --explain CODE`.

Run the end-to-end check against a sandbox:

```bash
export LCP_ENDPOINT=https://sandbox.example
export LCP_SENDER_ID=your-sender-id
export LCP_API_KEY=your-api-key            # or LCP_HMAC_SECRET
export LCP_RECEIVER_ID=platform-001        # optional; platform-issued ID
lcp-doctor
```

`LCP_RECEIVER_ID` fills `lcp.message.receiver_id`. If it is unset, the doctor
uses `LCP_PLATFORM_ID`, then a `receiver_id`/`platform_id` advertised by the
capabilities document, then `LCP_PUBLISHER_ID`, then the endpoint hostname.
Set it explicitly when the receiver does not advertise its platform ID.

The doctor exits `0` only when `GET /v1/lcp/capabilities` and a synthetic
`test: true` `POST /v1/lcp/leads` both pass. It exits `1` otherwise and prints
the failing code plus the fix from this table. `lcp-doctor --json` emits the
same report as machine-readable JSON.

| Code | HTTP | Plain-English meaning | Three most common causes | Exact field-level fix |
|---|---|---|---|---|
| `LCP-001` | 400 | The request is not a well-formed LCP HTTP request. | Body is not valid JSON.<br>Content-Type is not `application/json`.<br>The URL path or HTTP method is not the route the endpoint serves. | `POST <endpoint>/v1/lcp/leads` with `Content-Type: application/json` and a complete envelope `{lcp:{version,message,payload}}`. Remove duplicate or missing path segments from `LCP_ENDPOINT`. |
| `LCP-002` | 401 | The receiver does not recognize or authorize this sender. | `sender_id` is not provisioned at the receiver.<br>The credential does not belong to this sender.<br>`X-LCP-Sender-Id`, envelope `sender_id`, `receiver_id`, or credential scope disagrees with the receiver. | Use the exact `sender_id` and credential issued by the receiver. Send `Authorization: Bearer <api-key>` (or HMAC headers) plus `X-LCP-Sender-Id` equal to `lcp.message.sender_id`. Set `lcp.message.receiver_id` to the receiver's platform ID and confirm the credential has `lead:submit`. |
| `LCP-003` | 400 | A required field is missing from the message. | `consumer` lacks `email`, `phone`, `first_name`+`last_name`, or `full_name`.<br>`location` lacks `country_code`, or both `state_region` and `postal_code`.<br>`attributes` lacks `vertical` or `schema_version`. | Add the field named in `errors[].field`. Minimum lead: `consumer.email` or `consumer.phone` or `consumer.full_name`, `location.country_code` plus `state_region` or `postal_code`, and `attributes.vertical` plus `attributes.schema_version`. |
| `LCP-004` | 422 | The requested lifecycle change is not allowed from the lead's current status. | A terminal status was moved back.<br>The transition skipped a required intermediate status.<br>The update targeted the wrong `lead_id`. | Read the current status with `GET /v1/lcp/leads/{lead_id}`, then send only a transition the payload specification allows for that status. Do not change a terminal lead. |
| `LCP-005` | 409 | This lead or idempotency key was already submitted. | The same `idempotency_key` was reused for a new message.<br>The same `lead_id` was submitted twice.<br>A retry reused the original key but the receiver treats it as a conflict. | For a genuinely new lead, generate a new UUID for `lcp.message.id` and a new `idempotency_key` plus `X-LCP-Idempotency-Key`. For a retry, reuse the original key and expect the original result. |
| `LCP-006` | 400 | `message.type` is not a message type this receiver accepts. | A typo or custom value such as `Lead` instead of `lead`.<br>The receiver does not implement that type.<br>A v1.2 type was sent without the matching capability advertisement. | Set `lcp.message.type` to `lead`, `call`, `ping`, `post`, `ack`, `event`, or `bid`. Send `batch`, `quote`, `counter`, or `subscription` only when `GET /v1/lcp/capabilities` advertises it. |
| `LCP-007` | 422 | The protocol or schema version in the message is not supported. | `lcp.version` is not full `major.minor.patch` semver.<br>The major version is not 1.<br>`attributes.schema_version` is not the version advertised for that vertical. | Set `lcp.version` to a value from `capabilities.lcp_versions` (e.g. `1.2.0`), and set `attributes.schema_version` to the `schema_version` advertised for `attributes.vertical`. |
| `LCP-008` | 400 | A ping contains a field the non-PII ping allowlist forbids. | Raw consumer data was put in a ping.<br>A vertical attribute is not marked `ping_safe`.<br>An unknown field was added to the closed ping object. | Remove the field named in `errors[].field`. Pings may contain only fields listed in the ping payload schema; send contact data as `phone_hash`/`email_hash`, never raw values. |
| `LCP-009` | 400 | A phone number is not in E.164 format. | The number has spaces, dashes, or parentheses.<br>The number starts with a local trunk `0` instead of a country code.<br>`secondary_phone` has the same formatting problem. | Set `consumer.phone` (and `secondary_phone`) to E.164: `+`, country code, then 2-15 digits with no separators, e.g. `+61412345678`. |
| `LCP-010` | 422 | The lead's location or territory is not valid for this receiver. | `country_code` is lowercase or not ISO 3166-1 alpha-2.<br>`state_region` is not valid for that country.<br>`postal_code` uses the wrong national format, or both `state_region` and `postal_code` are missing. | Set `location.country_code` to an uppercase ISO 3166-1 alpha-2 code (e.g. `AU`). Add a valid `location.state_region` or `location.postal_code` for that country, using `capabilities.countries`. |
| `LCP-011` | 429 | The receiver is rate-limiting this sender. | Too many requests per second or minute.<br>The receiver's daily or hourly capacity is exhausted.<br>Parallel retries amplified the request rate. | Stop and retry after `Retry-After`. Reduce send rate, add jittered backoff, and check `capabilities.capacity` for remaining daily/hourly allowance. |
| `LCP-012` | 401 | HMAC authentication failed: signature, timestamp, or idempotency header does not verify. | The signature was computed over different bytes than the body sent.<br>`X-LCP-Timestamp` is stale, not UTC, or not whole-second `YYYY-MM-DDTHH:MM:SSZ`.<br>`X-LCP-Idempotency-Key` is missing or differs from `lcp.message.idempotency_key`. | Sign the exact canonical input `timestamp + "\n" + idempotency-key + "\n" + raw request body` with HMAC-SHA256 hex. Send the same timestamp in `X-LCP-Timestamp`, keep it within 300 seconds of the receiver clock, and send `X-LCP-Idempotency-Key` equal to `lcp.message.idempotency_key`. |
| `LCP-013` | 400 | Test traffic and the receiver's environment disagree. | A production endpoint received `test: true` or `X-LCP-Test: true`.<br>A sandbox received a message without both test markers.<br>The envelope `test` flag and the `X-LCP-Test` header disagree. | For a sandbox, send `lcp.message.test=true` and `X-LCP-Test: true` together (`lcp-doctor` does). For production, send `test: false` and omit `X-LCP-Test`. Point `LCP_ENDPOINT` at the matching environment. |
| `LCP-100` | 400 | The message failed schema or field validation. | A field has the wrong type, format, enum value, or pattern.<br>A closed object contains an unknown field.<br>The payload does not match the vertical's schema. | Fix the field named in `errors[].field` (and the path in `errors[].details`). Validate the envelope against `schemas/envelope.json`, the payload against `schemas/lead.json`, and `attributes` against the vertical schema before resending. |
| `LCP-500` | 500 | The receiver hit an internal error while processing the request. | A receiver bug or unhandled exception.<br>A downstream dependency (database, queue) is unavailable.<br>Receiver processing timed out. | Retry once with the same `idempotency_key` and body. If it repeats, send the receiver the UTC time, request ID, endpoint, and full response body; do not change a valid payload to work around a server fault. |

## Reading a response

Errors use one shared shape in HTTP error responses and `ack.errors[]`:

```json
{
  "errors": [
    {
      "code": "LCP-003",
      "name": "MISSING_REQUIRED_FIELD",
      "message": "consumer.phone is required when no email or name is provided",
      "field": "consumer.phone",
      "details": {}
    }
  ]
}
```

Read `errors[0].code` first, then `errors[0].field` for the location, then
`errors[0].message` for the receiver's own wording. `LCP-002` is the identity
and authorization axis; `LCP-012` is the replay-protection axis for a sender
the receiver already recognizes. An implementation must not recode one family
as the other.
