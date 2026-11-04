# LCP — Lead Context Protocol Specification

> Part of the normative LCP v1.0 specification: **Errors and status codes**.
> The structured error taxonomy, HTTP status mapping, the shared error shape, and ack status codes.
> Index: [SPEC.md](../SPEC.md) · File map: [spec/README.md](README.md).

## 5. Error taxonomy

| Code | Name | HTTP |
|---|---|---|
| LCP-001 | INVALID_FORMAT | 400 |
| LCP-002 | UNKNOWN_SENDER | 401 |
| LCP-003 | MISSING_REQUIRED_FIELD | 400 |
| LCP-004 | INVALID_STATUS_TRANSITION | 422 |
| LCP-005 | DUPLICATE_LEAD | 409 |
| LCP-006 | UNKNOWN_MESSAGE_TYPE | 400 |
| LCP-007 | SCHEMA_VERSION_UNSUPPORTED | 422 |
| LCP-008 | PII_IN_PING | 400 |
| LCP-009 | INVALID_PHONE | 400 |
| LCP-010 | INVALID_TERRITORY | 422 |
| LCP-011 | RATE_LIMITED | 429 |
| LCP-012 | SIGNATURE_INVALID | 401 |
| LCP-013 | TEST_TRAFFIC_POLICY | 400 |
| LCP-100 | VALIDATION_ERROR | 400 |
| LCP-500 | INTERNAL_ERROR | 500 |

**Error shape** (used everywhere — both HTTP error responses and
`ack.errors[]`):

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

`LCP-002 UNKNOWN_SENDER` covers the identity and authorization axis: an
unrecognized `sender_id`, a `sender_id` that disagrees with
`X-LCP-Sender-Id`, an API key that does not authenticate its sender, and a
recognized sender that lacks the scope a route requires.

`LCP-012 SIGNATURE_INVALID` covers the replay-protection axis for a
recognized sender (401): a missing `X-LCP-Timestamp` or
`X-LCP-Signature`; a timestamp that is malformed, not timezone-aware, or
outside the replay window; a missing `X-LCP-Idempotency-Key` on a mutating
route; and a header idempotency key that disagrees with
`lcp.message.idempotency_key`. These rejections happen before any PII is
stored or evaluated.

The replay window is inclusive and symmetric: a request is accepted when
`|now - timestamp| <= window` (integer-second granularity, default 300 s
per §9) and rejected when the offset exceeds it. Senders MUST NOT rely on
sub-second tolerance.

These are different failure modes (identity/authorization vs. replay
protection) and have distinct codes; an implementation MUST NOT re-code one
family as the other.

`LCP-011 RATE_LIMITED` (429) pairs with the per-sender rate limits
defined in §9. `LCP-013 TEST_TRAFFIC_POLICY` is used by the HTTP
reference profile when the `test` envelope field and `X-LCP-Test` header
disagree, when a sandbox receives non-test traffic, or when a production
endpoint receives test traffic.
