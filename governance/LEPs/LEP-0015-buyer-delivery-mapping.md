# LEP-0015 — Buyer delivery mapping: declarative outbound adapters and buyer-reported dispositions

- **Title:** Buyer delivery mapping: a declarative document that turns an LCP post into a buyer API request and the buyer's response into an LEP-0012 disposition
- **Author(s):** LCP maintainers (draft from SpearPointX adopter evidence)
- **Date:** 2026-11-19
- **Status:** **Implemented** — shipped in v1.4.0 (PR
  [#251](https://github.com/SpearSystems/LCP/pull/251), released
  2026-11-19); Accepted 2026-11-19 (maintainer;
  [v1.4 decision record](../../docs/DECISIONS-v1.4.md) D1–D4: review
  window and independent review waived, maintainer security/privacy
  sign-off under D3).
- **Target version:** v1.4.0 (additive: a new platform document format,
  conformance vectors and an optional buyer-side ack read)
- **Discussion:** [#257](https://github.com/SpearSystems/LCP/issues/257)
- **Depends on:** [LEP-0012](LEP-0012-sale-lifecycle.md) for the
  disposition vocabulary and [LEP-0013](LEP-0013-regulated-identifiers.md)
  for regulated-field forwarding. Both were Accepted on 2026-11-19 and
  ship in v1.3.0.

## Decision (maintainer, 2026-11-19)

**Accepted** under [D1–D4](../../docs/DECISIONS-v1.4.md) of the v1.4 decision
record. The review window closed early with no comments, and no reviewer
independent of the author took part (D1, D2). The maintainer signed off on
the buyer egress surface and restricted forwarding (D3).

Conditions:

- The reference executor ships **off** (`LCP_FEATURE_BUYER_MAPPING`). A
  deployment that turns it on first records a sign-off from a named human
  security/privacy reviewer (D3 condition 4).
- The reference platform enrols no buyer for regulated identifiers, so it
  refuses every regulated path in a mapping.
- Normative execution rules: [BUYER-MAPPING](../../docs/BUYER-MAPPING.md).
- Any comment with new evidence on
  [#257](https://github.com/SpearSystems/LCP/issues/257) reopens this record.
  Re-evaluation: 2027-05-19.

## Problem statement and evidence

**Most buyers do not speak LCP.** A platform that routes leads delivers to
buyer APIs that each have their own JSON or form body, field names, units,
authentication header and response format. LCP defines the inbound side
declaratively ([PUBLISHER-MAPPING](../../docs/PUBLISHER-MAPPING.md)), but
has nothing for the outbound side. The reference platform can deliver only
an LCP envelope to `webhook_url`, and it discards the buyer's response body.

**The buyer's verdict is lost.** LEP-0012 gives the sender a disposition:
sold, unsold, pending or price_rejected. For live traffic, the platform can
only learn that result from the buyer. A buyer that answers "rejected:
duplicate" with HTTP 200 is currently recorded as delivered and payable.

**Adopter evidence (SpearPointX).** The first non-LCP buyer the adopter
integrated needed:

- request field renames, nesting and a static source constant;
- unit conversion: the buyer takes whole dollars, while the platform holds
  integer cents;
- an API key in a header, resolved from a secret store;
- a test flag, plus a rule that test leads never reach the live endpoint;
- response decoding into accepted, rejected, invalid and price-rejected,
  with a counter price, a buyer lead reference and a consumer redirect URL.

The adopter's existing mechanism renames keys and nothing else. Everything
else would be hand-written code for that one buyer. Each later buyer would
add another one-off adapter, and the adapter could not be tested against
anything shared.

**LEP-0012's re-post trigger is met on the buyer leg.** LEP-0012 deferred
re-posting at the counter price until a buyer API with price-reject
semantics appeared. The adopter's buyer has those semantics. This record
covers only the platform-to-buyer re-post. A sender-side floor stays
deferred.

## Scope

1. **Buyer mapping document.** A versioned JSON document format
   (`format_version` `1.0`, schema `schemas/buyer-mapping.json`) and
   normative execution rules. A mapping is data, not code. It has no
   expressions, a closed transform set and a closed rule language.
2. **Native buyer disposition.** A platform MAY post `lcp.version` `1.3.0`
   to a buyer that declares it, and read `ack.disposition` from the
   response. Native LCP buyers need no mapping.
3. **Buyer-leg counter re-post.** One bounded re-post to the same buyer at
   its counter price, decided by platform policy.
4. **Conformance vectors.** Language-neutral golden vectors: given a post
   and a mapping, the exact request; given a response and a mapping, the
   exact decoded result. Any platform's executor, in any language, can run
   them.
5. **Reference platform**, after Acceptance: an executor behind
   `LCP_FEATURE_BUYER_MAPPING`, off by default, plus
   `lcp-platform-admin buyer-mapping upsert|list|deactivate`.

**Not in scope:**

- code adapters for protocols a mapping cannot express (SOAP or XML,
  multi-step sessions, OAuth token exchange);
- the ping leg of buyer ping-post APIs;
- asynchronous buyer callbacks that report a result later;
- a sender-side price floor on `lead`;
- a public catalog of buyer mappings.

Each of these is a deferred question below.

## Proposed wire shape

### 1. Document

```json
{
  "format_version": "1.0",
  "mapping_id": "example-loans-direct-v2",
  "buyer_id": "buyer_example_loans",
  "version": "2.0.0",
  "active": true,
  "vertical": "personal_loans",
  "schema_versions": ["2.0.0"],
  "request": {
    "method": "POST",
    "url": "https://api.buyer.example/v2/leads",
    "test_url": "https://sandbox.buyer.example/v2/leads",
    "encoding": "json",
    "timeout_ms": 10000,
    "headers": {
      "X-Api-Key": { "secret": "example_loans_api_key" },
      "X-Source": { "const": "lcp" }
    },
    "idempotency": { "header": "Idempotency-Key", "from": "message.idempotency_key" }
  },
  "body": {
    "source.reference": { "from": "message.id" },
    "source.test": { "from": "message.test", "default": false },
    "applicant.first_name": { "from": "payload.consumer.first_name", "transform": ["trim"], "required": true },
    "applicant.last_name": { "from": "payload.consumer.last_name", "transform": ["trim"], "required": true },
    "applicant.phone": { "from": "payload.consumer.phone", "transform": ["phone_national"], "required": true },
    "applicant.email": { "from": "payload.consumer.email", "transform": ["lower"] },
    "applicant.zip": { "from": "payload.location.postal_code", "transform": ["trim"] },
    "loan.amount": { "from": "payload.attributes.loan_amount", "required": true },
    "loan.purpose": {
      "from": "payload.attributes.loan_purpose",
      "map": { "debt_consolidation": "DEBT", "home_improvement": "HOME" },
      "map_default": "OTHER"
    },
    "pricing.min_price": { "from": "payload.price_cents", "transform": ["cents_to_units"] },
    "tracking.sub_id": { "from": "payload.extensions[\"lcp.affiliate.tracking\"].sub_id" }
  },
  "response": {
    "encoding": "json",
    "max_bytes": 65536,
    "rules": [
      { "when": { "status": [200, 201], "path": "result", "in": ["accepted"] }, "outcome": "sold" },
      { "when": { "path": "result", "equals": "price_rejected" }, "outcome": "price_rejected" },
      { "when": { "path": "result", "in": ["rejected", "invalid", "duplicate"] }, "outcome": "unsold" },
      { "when": { "status": [409] }, "outcome": "unsold", "reason": "duplicate" }
    ],
    "default": "pending",
    "fields": {
      "price": { "path": "price", "unit": "units" },
      "counter_price": { "path": "min_price", "unit": "units" },
      "redirect_url": { "path": "redirect_url" },
      "buyer_reference": { "path": "lead_id" },
      "reason": {
        "path": "reason",
        "map": { "dup": "duplicate", "low_credit": "credit_too_low", "bad_phone": "invalid_phone" },
        "map_default": "other"
      }
    },
    "retry_on_status": [429, 502, 503, 504]
  },
  "counter": { "repost": true, "max_reposts": 1, "price_from": "counter_price" }
}
```

All objects are closed (`additionalProperties: false`). A platform MUST
refuse at upsert any unknown key, unknown transform, unknown rule operator
or path outside the grammar below. It MUST NOT activate a document that
fails validation.

### 2. Paths

- Source paths are rooted at the `lcp` object of the post:
  `message.<field>` or `payload.<field>`. No other roots exist.
- A segment is a property name, or `["..."]` for a key that contains a dot
  (extension names). `[n]`, a non-negative integer, indexes an array.
- There are no wildcards, filters or functions.
- Every `payload.attributes.*` path MUST exist in the declared vertical's
  schema at every listed `schema_versions` entry. This is checked at upsert.

Target keys in `body` are dotted paths in the buyer's body. With
`encoding: json`, dots build nested objects. With `encoding: form`
(`application/x-www-form-urlencoded`), the key is sent as written, dots
included, and values MUST be scalar.

### 3. Values

Each target takes exactly one of three value sources:

- `{"from": path}`. Optional `transform` (an ordered list), `map` (exact
  string match after transforms), `map_default`, `default` and `required`.
  - A missing source with no `default` omits the target.
  - A missing `required` target stops the delivery before any bytes are
    sent (see §6).
- `{"const": value}`. A JSON scalar.
- `{"secret": name}`, with an optional `prefix` (for example `"Bearer "`).
  - The value is resolved from the platform secret store at send time.
  - Secret values never appear in a mapping, a vector, a log or an audit
    record.

**Transforms**, a closed set applied in order:

- string: `trim`, `lower`, `upper`, `string`;
- type: `integer`, `boolean`;
- phone: `e164`, and `phone_national` (E.164 `+1` numbers only, giving 10
  digits);
- time: `date` (`YYYY-MM-DD` from an RFC 3339 date-time), `date_time_utc`;
- money:
  - `cents_to_units` divides by the currency's ISO 4217 minor-unit factor.
    It fails, and never rounds, if the result would not be a whole number.
  - `units_to_cents` multiplies by that factor.

A transform that cannot apply, such as `integer` on `"abc"`, is a mapping
failure (§6). It is never coerced and never silently dropped.

### 4. Request

- **Method:** `POST` only. The URL is static. It has no macros and no query
  values taken from the lead, so PII is never in a URL.
- **`url` and `test_url`** MUST pass the platform's egress rules: `https`,
  the same SSRF checks as `webhook_url`, and no redirects followed.
- **Test traffic:** a post with `message.test: true` goes only to
  `test_url`. If `test_url` is absent, the post is not delivered, and the
  platform applies the LEP-0012 test outcome. A test post MUST never reach
  `url`.
- **Idempotency:** with `idempotency` declared, the platform sends the
  stable key on every attempt, and a retry after the request was sent is
  allowed. Without it, the platform retries only failures where no request
  bytes were sent (DNS, connect or TLS errors). An ambiguous failure after
  sending becomes `pending` (§5). This avoids double-selling to a buyer that
  cannot deduplicate.

### 5. Response

The body is read up to `max_bytes`, capped by the platform's own limit. It
is decoded per `encoding` (`json` or `form`) and then discarded. Only the
decoded fields persist.

**Rules** are evaluated in order, and the first match wins.

- A `when` clause holds when all of its tests hold:
  - `status` is a list of HTTP codes;
  - `path` plus one of `equals`, `in` or `exists` tests a value in the body.
- `outcome` is an LEP-0012 outcome. An optional `reason` sets a known
  `rejection_reason` constant.
- `default` applies when no rule matches. It MUST be `pending` or `unsold`,
  never `sold`.

A mapping cannot make an unrecognised response into a sale.

**Decoded fields:**

- **`price`** and **`counter_price`.** `unit` is `units` or `cents`; the
  platform converts both to integer minor units of the post's currency.
  - A value outside `1`–`100000000` minor units is a decode failure.
  - A float with a fractional minor unit is a decode failure.
- **`redirect_url`.** It MUST match `^https://`, with at most 2048
  characters. Otherwise it is dropped. It is handled as LEP-0012 §2
  requires: relayed to the sender, never fetched and never logged.
- **`buyer_reference`.** At most 128 characters. It is stored for
  reconciliation.
- **`reason`.** It is mapped onto the known `rejection_reason` values. An
  unmapped value becomes `map_default`, or `other` if none is set. Free text
  from the buyer is never retained.

**Money is never invented.** When the outcome is `sold`, the payable price
is the decoded `price`. If `fields.price` is not declared, the payable
price is the posted `payload.price_cents`. If `price` is declared but fails
to decode on a `sold` response, the result is `pending` and enters
operator review. It is not `sold` at the posted price.

**Ambiguity is `pending`.** All of these resolve to `pending`:

- a timeout after sending;
- a 2xx response whose body fails to decode;
- a match on `default: pending`.

The platform resolves a `pending` result by reconciliation: an operator,
using `buyer_reference` and the buyer's report, before the LEP-0012
`final_by`. If it is still unresolved at `final_by`, the result is
`REJECTED` with `rejection_reason` `buyer_unconfirmed`, a new known value.

`retry_on_status` lists the statuses that re-enter the delivery retry
queue, subject to §4. Any other non-matching status falls through to the
rules and then to `default`.

### 6. Failures before sending

A missing `required` target, a failed transform or a regulated path the
buyer is not enrolled for (§Privacy) stops the delivery before any bytes
are sent. The platform then:

- records the delivery as `FAILED` with reason `mapping_failed`;
- records the target key in the audit, but never the value;
- dead-letters the delivery;
- gives the lead the same outcome as an undelivered post under SPEC §9.

### 7. Native buyers (no mapping)

- A buyer whose profile declares `lcp_versions` including `1.3.0` receives
  posts at `lcp.version` `1.3.0`.
- The platform reads the response body, bounded by
  `max_webhook_response_bytes`, as an LCP `ack`. It validates the body
  against `schemas/ack.json` and retains only `status`, `rejection_reason`
  and `disposition`.
- A 2xx response without a valid ack keeps today's behaviour: delivered,
  with payable rules as configured.
- A buyer disposition is the buyer's statement to the platform. The
  sender-facing disposition is computed by the platform's own payout
  policy, and the buyer's price and the sender's payout are never
  conflated.

### 8. Counter re-post

On a decoded `price_rejected` with a valid `counter_price`, the platform
MAY re-post once (`max_reposts` cap `1` in format `1.0`) to the same buyer
at the counter price. All of these conditions must hold:

- the counter is at or above the platform's floor for this lead and offer.
  The floor is platform policy and is never sent to the buyer.
- the post-response deadline (SPEC §9) has not passed;
- the re-post uses a new `message.id` and the same
  `message.idempotency_key` suffixed `:r1`.

If no re-post is made, or the re-post is refused, the buyer result is
`unsold`. The sender sees one final disposition for the lead. A buyer-leg
`price_rejected` is never forwarded to the sender as-is.

#### Valid 1 — rendered request (vector `BMAP-R-001`)

Post `payload.attributes.loan_amount` `1000`, `payload.price_cents` `5000`
in USD, `message.test` `false`, with the mapping above. The request goes to
`url`:

```json
{
  "source": { "reference": "6f1c2c1e-2b1d-4c35-9c55-3f2a1d9b8e10", "test": false },
  "applicant": { "first_name": "Test", "last_name": "Applicant", "phone": "2025550143", "email": "test@example.com", "zip": "20001" },
  "loan": { "amount": 1000, "purpose": "DEBT" },
  "pricing": { "min_price": 50 },
  "tracking": { "sub_id": "s-42" }
}
```

It carries the header `Idempotency-Key: <message.idempotency_key>`. In
vectors, `X-Api-Key` is the placeholder `<secret:example_loans_api_key>`.

#### Valid 2 — decoded responses (vectors `BMAP-D-001`…`005`)

| HTTP | Body | Decoded |
|---|---|---|
| 200 | `{"result":"accepted","price":42,"lead_id":"B-9","redirect_url":"https://buyer.example/offer/abc"}` | `sold`, price 4200, reference `B-9`, redirect relayed |
| 200 | `{"result":"price_rejected","min_price":30}` | `price_rejected`, counter 3000, re-post allowed if 3000 is at or above the floor |
| 200 | `{"result":"rejected","reason":"dup"}` | `unsold`, `duplicate` |
| 409 | empty | `unsold`, `duplicate` |
| 200 | `{"result":"queued"}` | `pending` (default) |

#### Invalid A — `default: sold`

The mapping is refused at upsert. A default can never make a sale.

#### Invalid B — URL macro

`"url": "https://api.buyer.example/leads?phone={phone}"` is refused at
upsert, because URLs are static.

#### Invalid C — regulated path without enrolment

`payload.attributes.us_details.ssn` in `body`, for a buyer not enrolled
under LEP-0013, is refused at upsert. At send time it would be dropped and
the delivery failed (§6).

#### Invalid D — inexact unit conversion

`payload.price_cents` `5050` through `cents_to_units` is a mapping failure.
It is not rounded to `50` or `51`.

## Privacy, security, and universal-core audit

- **Not executable.** There are no expressions, templates or scripts, and
  the transform and rule sets are closed. A mapping cannot reach anything
  outside the post it is given.
- **Egress:** static `https` URLs under the existing SSRF and no-redirect
  rules. Lead data is never in a URL.
- **Secrets by reference only.** Values are resolved at send time and
  redacted from logs, audits, errors and vectors.
- **Restricted and regulated fields:**
  - A mapping may read a `restricted` path only if the buyer's requirement
    profile allows that field.
  - It may read an `x-lcp-regulated` path only if the buyer is enrolled
    and names that field (LEP-0013).
  - Both rules are checked at upsert and again at send, fail closed.
- **Response bodies are never stored.** Buyer free text is never retained.
  `redirect_url` follows LEP-0012 §2.
- **Test isolation:** test posts reach only `test_url` (§4).
- **Universal core:** no envelope change. §7 uses the existing `ack`
  schema. The mapping format is a platform document, not a message type.

A named human security/privacy reviewer is needed at Review, because the
record adds egress to arbitrary buyer hosts and forwards restricted data.

## Compatibility, versioning, and deprecation impact

- **Additive.** Senders see no change. Buyers without a mapping or a
  declared `lcp_versions` keep today's delivery.
- **`format_version`** versions the document. A platform MUST refuse a
  `format_version` it does not implement.
- **`buyer_unconfirmed`** is a new known value on the open
  `rejection_reason` enumeration.

## SDK, OpenAPI, MCP, conformance, and operational impact

- **SDKs:** no client API change. Mappings are executed by platforms, not
  senders. An optional Python validator for mapping documents may ship
  with the reference platform.
- **Conformance:** a new `BMAP` tier with:
  - render vectors (post plus mapping gives exact headers, with
    placeholders, and body);
  - decode vectors (status plus body plus mapping gives the exact decoded
    result);
  - refusal vectors for every Invalid case.

  The vectors are plain JSON, so a PHP or TypeScript platform runs them
  without the Python runner.
- **OpenAPI and MCP:** none. Admin is platform-local.
- **Operations:** per-mapping counters for each outcome, the rate of
  decodes that fell to `default` (a drift alarm when a buyer changes its
  response), `mapping_failed` counts by target key, and re-post counts and
  acceptance.

## Alternatives considered

- **A code adapter per buyer.** This is the status quo. Each one is a
  one-off, cannot be tested against shared vectors and grows with the buyer
  count. Rejected as the default. Code adapters stay the escape hatch for
  protocols a mapping cannot express.
- **An expression language** (JSONata, JMESPath, Jinja). Expressive, but
  executable, hard to bound, and implementations disagree on edge cases.
  The closed transform set covers the adopter evidence. Rejected.
- **Require buyers to speak LCP.** Unrealistic for existing buyer APIs.
  §7 supports native buyers, and the mapping covers the rest.
- **Extend the inbound publisher mapping.** The transforms overlap, and the
  two should share one transform registry. Outbound also needs secrets,
  test routing, idempotency and response decoding, so it gets its own
  document. Partially adopted: shared transforms.

## Rollout, migration, observability, and rollback

1. **LCP:** schema, vectors and documentation, then the reference executor
   behind `LCP_FEATURE_BUYER_MAPPING`, off by default.
2. **Adopter (SpearPointX):**
   - grows its key-rename mapping into this format;
   - runs the `BMAP` vectors against its PHP executor;
   - writes its first buyer mapping as a private document. The buyer
     stays unnamed in this repository.
   - Routing, money and deadlines stay in the adopter's router core.
3. **Activation:** upsert a new version, send synthetic test posts to
   `test_url`, compare the decoded results, then mark the old version
   inactive. This matches PUBLISHER-MAPPING.
4. **Rollback:** deactivate the version, and the previous version resumes.
   Turning the flag off reverts to LCP-envelope webhook delivery.

## Deferred questions

- **Ping leg of buyer ping-post APIs.** Trigger: a buyer that requires a
  ping before the post.
- **Asynchronous buyer callbacks** (the buyer reports funded, returned or
  rejected later). The decode rules would apply to an inbound callback.
  Trigger: a buyer whose final result arrives after the response.
- **OAuth2 client-credentials tokens.** Trigger: a buyer that will not
  accept a static key.
- **XML and SOAP bodies.** Trigger: a buyer that offers no JSON or form
  endpoint.
- **Sender-side floor on `lead`** (LEP-0012's deferral). Trigger: a sender
  asking to automate re-posts.
- **A shared catalog of buyer mappings.** Trigger: two platforms
  delivering to the same buyer API.
