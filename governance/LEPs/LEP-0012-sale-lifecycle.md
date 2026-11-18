# LEP-0012 — Sale lifecycle: lead disposition, money events, URL postbacks, and test outcomes

- **Title:** Sale lifecycle: synchronous lead disposition, money-bearing lifecycle events, URL-template postbacks, tracking passthrough, and defined test outcomes
- **Author(s):** LCP maintainers (draft from SpearPointX adopter evidence)
- **Date:** 2026-11-19
- **Status:** **Draft**. Not accepted. A candidate implementation is on
  branch `lep/0012-0013-sale-lifecycle`; it does not merge until this
  record is Accepted (LEP.md stage 5).
- **Target version:** v1.3.0 (additive MINOR: one optional object on the
  closed `ack` payload, one optional property on `subscription`, documented
  `details` shapes on three existing event names, two registered extensions;
  no new message type, no new error code)
- **Discussion:** none yet. A `[LEP]` issue opens when this record moves to
  Review.
- **Reviewers needed at Review:** at least one publisher, one buyer, and one
  lead network (domain); a named human security/privacy reviewer, because
  §4 adds an outbound HTTP surface and §1 carries a session-bearing URL.
- **Companion records:** [LEP-0013](LEP-0013-regulated-identifiers.md)
  (regulated identifiers) and
  [LEP-0014](LEP-0014-personal-loans-vertical.md) (`personal_loans`
  vertical). Each is decidable on its own.

## Problem statement and evidence

### What senders need back from a lead post

A publisher or network that posts a lead needs four facts, at post time:

1. **Did it sell?**
2. **For how much,** so ad spend can be optimised on revenue rather than on
   form fills.
3. **Where to send the consumer next,** when the buyer requires a browser
   handoff.
4. **What changed later:** a pending lead resolving, or a sold lead
   returned.

Real buyer APIs already answer the first three synchronously. A US
personal-loans buyer's v2 lead API, used by the reference deployment,
answers every post with:

- a status: accepted, rejected, or rejected on price with an alternative
  price;
- the price, as a decimal string;
- a consumer `redirect_url` that the buyer measures as a redirect rate.

Ping-tree buyers and lead networks commonly do the same.

### What LCP has today

- **`ack.status` is message-level only.** It reports `ACCEPTED`,
  `REJECTED` and so on for the message. It has no sale outcome, no price,
  and no redirect. `ack.json` is closed (`additionalProperties: false`), so
  a receiver cannot add them without breaking strict validators.
- **Events carry no money for a sale or a return.** `CONVERTED` and
  `FINAL_PRICING` have money shapes. `ACCEPTED`, `REJECTED` and `REFUNDED`
  have none, so a sender cannot reconcile a return against an earlier sale.
- **Subscriptions are JSON webhooks only** (LEP-0001). Publishers optimise
  ads through affiliate trackers. Those trackers accept server-to-server
  postbacks as an HTTPS GET with values substituted into a URL template.
  They do not run an HMAC-verifying JSON receiver, and nor do no-code form
  stacks.
- **The test flag has a traffic policy (`LCP-013`) but no defined
  outcome.** An integrator cannot make a test post come back sold, unsold or
  pending, so the branches that matter most go untested until live traffic.

### What it costs, measured in the reference deployment

- SpearPointX had to plan a non-LCP response body (outcome, payout,
  redirect) for publishers. That is a protocol fork in the first adopter.
- A publisher integration guide converted dollars to cents twice. Nothing in
  the response told the publisher what the lead earned, so the error could
  only surface at statement time.
- Publisher test posts can only prove that the request validates, not that
  the integration handles a sale.

### Evidence status

There is one adopter deployment, one documented buyer API, and the
industry-standard S2S postback convention. The two-adopter rule in
`LEP.md` applies to new message types. This record adds none: every change
is an optional field, a documented `details` shape, or a registered
extension.

## Scope

- **Core protocol (additive MINOR):**
  - `ack.disposition`: an optional object on the ack of a `lead` or `post`
    (§1);
  - `subscription.delivery`: an optional property selecting JSON or
    URL-template delivery (§4).
- **Documented event shapes:** `details` for `ACCEPTED`, `REJECTED` and
  `REFUNDED` when they report a sale result to the sender of the lead (§3).
- **Registered extensions:**
  - `lcp.affiliate.tracking`: click and sub-ID passthrough (§5);
  - `lcp.test.outcome`: a forced test outcome (§6).
- **Capability advertisement:** `features.sale_disposition`,
  `features.url_postbacks` and `features.test_outcomes`, each naming
  `1.3.0`.
- **Normative text:** consumer redirect handling (§2) and test-outcome
  behaviour (§6).
- **Not in scope:**
  - settlement, invoicing, or the publisher statement of account;
  - pricing strategy;
  - a price floor on a direct lead post (deferred, see Deferred questions);
  - consumer-facing redirect pages;
  - shared-lead (LEP-0003) multi-buyer dispositions.

## Proposed wire shape

### 1. `ack.disposition`

`disposition` is an optional object on the payload of an `ack` whose
`original_message_id` names a `lead` or `post`. It reports the commercial
result. `ack.status` keeps reporting message processing.

| Field | Type | Rule |
|---|---|---|
| `outcome` | string, closed enum | `sold`, `unsold`, `pending`, `price_rejected`. Required. |
| `payout_cents` | integer, `1`–`100000000` | Amount the receiver will pay the sender for this lead, in minor units of `currency`, after any receiver fee. Required when `outcome` = `sold`; absent otherwise. Same typo guard as `FINAL_PRICING.winning_price_cents`. |
| `currency` | string, ISO 4217 | Required when `payout_cents` or `counter_price_cents` is present. |
| `counter_price_cents` | integer, `1`–`100000000` | `price_rejected` only: the lowest price the receiver would pay. Informational in v1.3.0 (see Deferred questions). |
| `redirect_url` | string, `^https://`, ≤ 2048 | `sold` only, optional. The consumer's next page. Classed `restricted` (§2). |
| `redirect_expires_at` | string, date-time | Optional, with `redirect_url` only. |
| `final_by` | string, date-time | `pending` only, optional: the latest time the final event will be sent. |

**Status and disposition together (normative):**

- A receiver includes `disposition` only on an ack with `status` =
  `RECEIVED`, `ACCEPTED` or `DUPLICATE`. A receiver that routes
  asynchronously answers `RECEIVED` with `outcome` = `pending` (or with a
  forced test outcome, §6).
- A `DUPLICATE` ack for an idempotent replay MUST repeat the disposition of
  the original ack, byte for byte, when one was issued. Retries then learn
  the result instead of losing it.
- `rejection_reason`, the existing open enum, MAY accompany `outcome` =
  `unsold` or `price_rejected`. Its description widens from "when status =
  REJECTED" to "when the lead was refused or not bought".
- An ack with `status` = `REJECTED` and no `disposition` means the receiver
  refused the lead. A sender treats it as unsold.
- **Sender rule:** a lead is sold if, and only if,
  `disposition.outcome` = `sold`. A sender MUST NOT infer a sale from HTTP
  2xx or from `status` = `ACCEPTED` alone.

**Pending.** The final result arrives as an `ACCEPTED` (sold) or `REJECTED`
(unsold) event (§3), by subscription or through `GET /v1/lcp/events`.
`final_by` bounds the wait. After `final_by` with no event, the sender
treats the lead as unsold, and a later `ACCEPTED` event still supersedes
that by revision.

**Version gate.** `ack.json` is closed, so a strict 1.2 sender would reject
an ack that carries `disposition`. A receiver MUST include `disposition`
only when the acknowledged message's `lcp.version` is `1.3.0` or later,
parsed as semver and never compared as a string. A 1.2 sender therefore
never receives it.

### 2. Consumer redirect (normative)

The receiver measures the redirect from the consumer's own client: their
IP address, user agent and cookies. The rules below keep that measurement
honest.

- The sender MUST deliver `redirect_url` to the consumer's browser as a
  top-level navigation: an HTTP `302`/`303` from the sender's page, or a
  script navigation. It SHOULD do so immediately after the ack.
- The sender MUST NOT request the URL from a server. It MUST NOT prefetch or
  preload it, render it in an iframe, or pass it to a link-preview service.
  A server-side request records a false redirect from the wrong client.
- A receiver MAY wrap a downstream receiver's URL in its own relay hop
  (`https://relay.example/r/{token}` → `302` → downstream URL), which lets
  it measure redirect rate per sender. A relay:
  - MUST use an opaque, unguessable token with no consumer data;
  - MUST be HTTPS;
  - MUST answer `404` for an unknown token;
  - SHOULD answer a repeat hit before `redirect_expires_at` with the same
    destination;
  - MUST NOT add query parameters derived from consumer data.
- **Class.** `redirect_url` is a session-bearing URL. A buyer's landing page
  may show the consumer's prefilled application. Following
  `docs/PII-CLASSES.md` it is `restricted`: never logged, never in an event,
  never in a postback macro.
  - It is carried only on the ack back to the sender of the lead, which
    already holds the consumer record.
  - The emission matrix gains an `ack` column. `restricted` is allowed only
    in `disposition.redirect_url`, and only to the sender of the
    acknowledged `lead`/`post`. Every other row matches `event`.

### 3. Money-bearing lifecycle events

When the receiver of a lead reports that lead's commercial fate to its
sender, `details` SHOULD use these shapes. Examples are platform to
publisher, or buyer to platform.

| Event | Meaning | `details` |
|---|---|---|
| `ACCEPTED` | Sold. Also emitted for a sale already reported synchronously, so a postback subscriber that is not the posting server learns of it. | `payout_cents` (> 0), `currency`, `revision`, `sold_at` |
| `REJECTED` | Unsold after `pending`. Also emitted for an unsold result already reported synchronously, so a postback subscriber learns of it. | `revision`, optional `rejection_reason` |
| `REFUNDED` | Returned or reversed after a sale. | `payout_cents` (< 0), `currency`, `revision`, `returned_at`, optional `return_reason` |

**Field rules:**

- **`payout_cents` is a signed delta,** within `-100000000`–`100000000` and
  never `0`. It is the change this event makes to what the receiver owes the
  sender for the lead. The sum over all events for one lead is the net
  amount reported. Float major units are never valid.
- **One currency per lead.** Every money event for a lead carries the same
  `currency`. A receiver whose owed amount for a lead spans two currencies,
  or has none, MUST NOT report it under either label: it sends no money
  event and records the refusal for its operator.
- **A sale that owes nothing is not reported.** When a buyer took the lead
  but the sender is owed `0` (a zero-price payable), there is no money
  event, because `payout_cents` is never `0`, and no `REJECTED`, because
  the lead was not unsold. A sender waiting on `pending` applies the
  `final_by` rule, which matches its payout.
- **`revision`** is an integer ≥ 1, strictly increasing per `lead_id` for
  one sender–receiver pair. A sender keeps the highest revision it has seen
  and ignores a lower or equal one. Retries and out-of-order delivery are
  then harmless.
- **`return_reason`** is an open enum. Known values are `invalid_contact`,
  `duplicate`, `compliance_fail`, `fraud`, `consumer_dispute` and
  `buyer_error`.
- **All fields are `internal`.** The event class rule still holds: no
  consumer data in `details`.

**Settlement neutrality.** These shapes report. They do not invoice.
`post.pricing` remains the platform–buyer billing record, and the
deployment's statement of account remains the publisher payable record.

### 4. URL-template postbacks

`subscription` gains one optional property:

| Field | Type | Rule |
|---|---|---|
| `delivery` | string, enum `json` \| `url_template` | Default `json`, which is today's LEP-0001 behaviour, unchanged. |

When `delivery` = `url_template`, `callback_url` is a template and these
rules apply. A registration that breaks one is refused with `LCP-100` and
`field` = `callback_url`, and no new code is added.

1. **Literal destination.**
   - The template is at most 2048 bytes (UTF-8) before expansion, the
     `callback_url` limit.
   - The scheme, host, port and path are literal. Macros appear only in the
     query component, after `?`.
   - The existing SSRF rule (no embedded credentials, valid port, public
     resolved addresses only) runs on the literal host at registration and
     again before every delivery.
   - Redirects returned by the tracker are not followed.
2. **Closed macro set.** Any other `{…}` token is refused.

   | Macro | Value |
   |---|---|
   | `{lead_id}` | Event `lead_id`. |
   | `{external_id}` | The sender's `lead.external_id`, or empty. |
   | `{click_id}`, `{sub1}`…`{sub5}` | From `lcp.affiliate.tracking` on the lead (§5), or empty. |
   | `{event}` | The event name exactly as sent, for example `ACCEPTED`. |
   | `{payout}` | `details.payout_cents` as a decimal in major units. Uses the ISO 4217 exponent of `currency`, `.` as separator, no grouping, and a leading `-` when negative: `4450` USD → `44.50`; `-4450` USD → `-44.50`; `4450` JPY → `4450`. Empty when absent. |
   | `{payout_cents}` | `details.payout_cents` as an integer, or empty. |
   | `{currency}` | `details.currency`, or empty. |
   | `{revision}` | `details.revision`, or empty. |
   | `{test}` | `1` when the event is test traffic, else `0`. |
   | `{timestamp}` | Event `timestamp`. |

3. **Expansion.**
   - Every value is percent-encoded (RFC 3986; unreserved characters kept).
   - The expanded URL MUST NOT exceed 4096 bytes. A longer one is a
     delivery failure, not a truncation.
4. **Request.**
   - `GET` with no body. Any `2xx` is success.
   - Retries follow the subscription's existing delivery schedule.
   - The `event_types` allowlist applies unchanged. A subscriber that needs
     different tracker URLs per event (a conversion URL for `ACCEPTED`, an
     adjustment URL for `REFUNDED`) registers one subscription per URL.
5. **Signing.** Deliveries carry the same headers as a JSON delivery:
   `X-LCP-Timestamp`, `X-LCP-Event-Id`, `X-LCP-Idempotency-Key` (the
   event id) and `X-LCP-Signature`. The
   signature is computed as in `spec/01-transport.md`, with the expanded
   URL in place of the raw request body:
   `<timestamp>\n<idempotency-key>\n<expanded-url>`. A tracker that cannot
   verify ignores the headers.
6. **No consumer data, by construction.**
   - No macro resolves to a `contact`, `restricted` or `prohibited` value.
   - Adding a macro requires an LEP, and the new macro's source must be
     `public` or `internal`.
   - Trackers often embed their own API token in the template, so receivers
     MUST NOT log the expanded query string. They log host, status, latency
     and delivery id.
7. **Tenancy and addressing.** A postback fires only for leads the
   subscriber sent, the same tenant rule LEP-0001 applies to JSON
   deliveries. It fires only for events the receiver addresses to the
   subscriber about its own lead's sale result: a buyer's own `ACCEPTED`
   or `REJECTED` event about a post, which the publisher never sees, does
   not expand a publisher's template.

### 5. Registered extension `lcp.affiliate.tracking`

This carries the sender's own click and sub-ID values on a `lead` or `post`
so they can be echoed back through postback macros.

```json
{ "lcp.affiliate.tracking": { "click_id": "cl_8f3a2b", "sub1": "fb", "sub2": "adset_44", "sub3": "", "sub4": "", "sub5": "" } }
```

- **Keys** are `click_id` and `sub1`–`sub5`, all optional. Any other key
  is refused.
- **Values** are strings matching `^[A-Za-z0-9._~:-]{0,255}$`. The pattern
  excludes `@`, `+`, whitespace and `/`, so an email address or an E.164
  number cannot appear.
  - The LEP-0006 B.3.4 extension rule still applies: a run of 7–15
    digits reads as a phone number and is refused with `LCP-100`. A
    sender with numeric click ids prefixes them (`c_1234567890`) or sends
    them in `click_id` with a letter.
  - The no-consumer-data rule is normative on the sender. A receiver MAY
    refuse a value that its detector classes as contact data, with
    `LCP-100`.
- **Class** `internal`. Not `ping_safe`. Never forwarded to a downstream
  buyer: these values belong to the sender–receiver pair that carried them.

### 6. Test outcomes

This builds on the existing `test` envelope field, the `X-LCP-Test` header
and `LCP-013`. A receiver that advertises `features.test_outcomes`:

- MUST answer a test `lead` or `post` that carries `lcp.test.outcome`
  with that outcome, and MUST NOT route it to a live counterparty, bill
  it, or count it in any payable or rate. A test lead without the
  extension follows the receiver's existing test policy (`LCP-013`), and
  is answered `pending` when routed asynchronously.
- When it relays downstream, it MUST use the downstream's own test mode or
  not relay at all.
- MUST honour the registered extension `lcp.test.outcome`:

  ```json
  { "lcp.test.outcome": { "outcome": "sold", "payout_cents": 4450 } }
  ```

  - `outcome` is required and is one of the §1 values.
  - `payout_cents` is optional. When it is absent, the synthetic amount
    is fixed: `payout_cents` = `1000` with `currency` = `USD`, on the
    disposition and on the `ACCEPTED` event alike. A forced
    `price_rejected` answers `counter_price_cents` = `1000`, `USD`. An
    integrator's test therefore asserts the same values against every
    receiver.
  - A forced `unsold` or `price_rejected` still produces its final event:
    `REJECTED` with revision 1 and `rejection_reason` `no_buyer` or
    `price_too_high`.
  - The extension is legal only when `message.test` is `true`. On live
    traffic it is refused with `LCP-013`.
  - It takes effect only on a `1.3.0` or later message, because the
    forced result is reported in `disposition` (§1's version gate).
- On a forced `pending`, MUST send the final test event within 60 seconds,
  as `ACCEPTED` with revision 1, so the async path can be exercised.
- MUST emit the test's §3 events with `message.test` = `true`. JSON
  deliveries carry `X-LCP-Test: true`; URL-template deliveries expand
  `{test}` to `1`.
- SHOULD return, for a forced `sold`, a `redirect_url` on its own domain
  that serves a static confirmation page and records the hit. The
  integrator can then prove the browser redirect works before go-live.

### Valid example 1 — test lead forcing a sale, with tracking passthrough

This validates against the v1.2.0 schemas today: `extensions` is an open
namespaced object.

```json
{
  "lcp": {
    "version": "1.2.0",
    "message": {
      "id": "d1200000-0000-4000-8000-000000001201",
      "type": "lead",
      "timestamp": "2026-11-19T10:00:00Z",
      "sender_id": "pub_example",
      "receiver_id": "platform_001",
      "idempotency_key": "pub-example-lead-1201",
      "test": true
    },
    "payload": {
      "lead_id": "lead_1201",
      "external_id": "sess_8f3a2b",
      "submitted_at": "2026-11-19T09:59:58Z",
      "status": "NEW",
      "channel": "form",
      "consumer": {
        "first_name": "Test",
        "last_name": "Applicant",
        "email": "test.applicant@example.com",
        "phone": "+12025550143"
      },
      "location": { "country_code": "US", "state_region": "TX", "postal_code": "75001" },
      "compliance": {
        "consent_timestamp": "2026-11-19T09:59:50Z",
        "consent_source_url": "https://lp.example.com/apply",
        "consent_text_version": "consent-v1"
      },
      "attributes": { "vertical": "home_services", "schema_version": "1.0.0" },
      "extensions": {
        "lcp.affiliate.tracking": { "click_id": "cl_8f3a2b", "sub1": "fb", "sub2": "adset_44" },
        "lcp.test.outcome": { "outcome": "sold", "payout_cents": 4450 }
      }
    }
  }
}
```

#### Valid 2 — synchronous sale with a redirect (v1.3.0 shape)

```json
{
  "lcp": {
    "version": "1.3.0",
    "message": {
      "id": "d1200000-0000-4000-8000-000000001202",
      "type": "ack",
      "timestamp": "2026-11-19T10:00:01Z",
      "sender_id": "platform_001",
      "receiver_id": "pub_example",
      "correlation_id": "d1200000-0000-4000-8000-000000001201",
      "idempotency_key": "platform-001-ack-1201",
      "test": true
    },
    "payload": {
      "original_message_id": "d1200000-0000-4000-8000-000000001201",
      "status": "ACCEPTED",
      "lead_id": "lead_1201",
      "disposition": {
        "outcome": "sold",
        "payout_cents": 4450,
        "currency": "USD",
        "redirect_url": "https://platform.example/r/Zk3pQ8vN2mT6",
        "redirect_expires_at": "2026-11-19T10:30:01Z"
      }
    }
  }
}
```

#### Valid 3 — pending, then resolved by event

The ack payload:

```json
{ "original_message_id": "d1200000-0000-4000-8000-000000001203", "status": "ACCEPTED", "lead_id": "lead_1203",
  "disposition": { "outcome": "pending", "final_by": "2026-11-19T10:05:00Z" } }
```

The later event payload:

```json
{ "lead_id": "lead_1203", "event": "ACCEPTED", "timestamp": "2026-11-19T10:01:12Z",
  "details": { "payout_cents": 3900, "currency": "USD", "revision": 1, "sold_at": "2026-11-19T10:01:12Z" } }
```

#### Valid 4 — return after sale

```json
{ "lead_id": "lead_1201", "event": "REFUNDED", "timestamp": "2026-12-02T03:00:00Z",
  "details": { "payout_cents": -4450, "currency": "USD", "revision": 2, "returned_at": "2026-12-02T03:00:00Z", "return_reason": "invalid_contact" } }
```

#### Valid 5 — price rejection

```json
{ "original_message_id": "d1200000-0000-4000-8000-000000001205", "status": "ACCEPTED", "lead_id": "lead_1205",
  "rejection_reason": "price_too_high",
  "disposition": { "outcome": "price_rejected", "counter_price_cents": 5010, "currency": "USD" } }
```

#### Valid 6 — URL-template subscription

```json
{ "event_types": ["ACCEPTED", "REFUNDED"], "delivery": "url_template",
  "callback_url": "https://track.example.com/postback?cid={click_id}&event={event}&payout={payout}&cur={currency}&rev={revision}&test={test}" }
```

Expanded for Valid 4 (`click_id` = `cl_8f3a2b`):
`https://track.example.com/postback?cid=cl_8f3a2b&event=REFUNDED&payout=-44.50&cur=USD&rev=2&test=0`

#### Invalid A — sold without a payout

`{"outcome": "sold", "currency": "USD"}` is refused by schema:
`payout_cents` is required when `outcome` = `sold`.

#### Invalid B — macro in the host

`"callback_url": "https://{sub1}.track.example.com/pb?e={event}"` is
refused with `LCP-100`, `field` = `callback_url`, because macros are legal
only in the query component.

#### Invalid C — unknown macro

`"callback_url": "https://track.example.com/pb?e={event}&m={email}"` is
refused with `LCP-100`: `{email}` is not in the closed macro set.

#### Invalid D — forced outcome on live traffic

A lead with `message.test` = `false` and `extensions["lcp.test.outcome"]` is
refused with `LCP-013 TEST_TRAFFIC_POLICY`.

#### Invalid E — disposition to a 1.2 sender

A receiver acknowledging a message whose `lcp.version` is `1.2.0` with a
`disposition` violates §1's version gate. Conformance vector `SALE-0xx`
asserts that the 1.2-shaped ack omits it.

#### Invalid F — contact data in a sub-ID

`"sub1": "jane@example.com"` fails the `lcp.affiliate.tracking` pattern and
is refused with `LCP-100`, `field` =
`extensions.lcp.affiliate.tracking.sub1`.

## Privacy, security, and universal-core audit

**New consumer-data surfaces.** There is one: `disposition.redirect_url`,
classed `restricted` and carried only back to the sender of the lead, which
already holds the record. Every other new field is `public` or `internal`.
Postback macros resolve only from `internal` sources, so a tracker never
receives consumer data.

**Egress (§4).**
- URL-template delivery is a new outbound GET surface. It reuses LEP-0001's
  SSRF rule, adds a literal-host constraint and no-redirect-following, and
  bounds URL length.
- Trackers' own tokens in templates are protected by the no-query-logging
  rule.
- **Residual risk:** a subscriber can point a template at any public host.
  That is the same as JSON delivery today, and is bounded by the per-tenant
  subscription limits.

**Redirect integrity (§2).**
- Forbidding server-side fetches protects the downstream receiver's
  measurement and stops a sender from inflating redirect rate.
- Relay tokens are opaque and expire.
- **Open-redirect risk:** a relay only redirects to destinations it
  received from a downstream ack, never to a URL taken from a request.

**Money integrity.**
- All amounts are integer minor units with the existing typo guard.
- Signed deltas with revisions make returns reconcilable without mutable
  state on the sender.
- Nothing here changes a payable.

**Test isolation (§6).** Forced outcomes are refused on live traffic. Test
leads never reach a live counterparty or a payable.

**Universal-core audit (SPEC §12).**
- No vertical or market field enters the core. `disposition` and
  `delivery` are vertical-neutral.
- The affiliate sub-ID convention is market practice, so it is a registered
  extension, not a core field.
- Redirect handling applies to any vertical with a consumer handoff.
- Ping is untouched.

## Compatibility, versioning, and deprecation impact

- **MINOR.**
  - Optional property on a closed payload (`ack.disposition`), gated on the
    acknowledged message's version.
  - Optional property with a default that preserves today's behaviour
    (`subscription.delivery`).
  - Documented shapes inside an open object (`event.details`).
  - Registered extensions.
- **No change for 1.0–1.2 peers.**
  - A 1.2 sender never receives `disposition`.
  - A JSON subscription is unchanged.
  - Events without the new `details` keys stay valid.
- **`rejection_reason` description widening** is text-only. The field
  stays an open enum.
- **Deprecation:** none.

## SDK, OpenAPI, MCP, conformance, and operational impact

**Schemas (edited first at implementation):**
- `schemas/ack.json`: `disposition`, with `if`/`then` for the per-outcome
  required fields, and legal only on an ack whose `status` is `RECEIVED`,
  `ACCEPTED` or `DUPLICATE`.
- `schemas/subscription.json`: `delivery`.
- `governance/EXTENSION-REGISTRY.md`: two rows.
- `docs/PII-CLASSES.md`: the `ack` column and the `disposition.redirect_url`
  mapping row.
- Bundles and SHA-256 manifests regenerate.

**Ten SDKs:** the generated models and schema bundles carry
`disposition` and `delivery` at v1.3.0. Hand-written helpers (`is_sold`,
`format_payout`, a URL-template validator, a `test_outcome` builder) are
deferred: `check_sdk_surface` requires the same surface in all ten SDKs,
and the helpers ship together in one later SDK release.

**OpenAPI and MCP:**
- the ack response schema gains `disposition`;
- the subscription create schema gains `delivery`;
- MCP lead-submit tools return `disposition` unchanged.

**Conformance:** new suite `test-vectors/sale-lifecycle.json`, ids
`SALE-001`…:
- per-outcome ack shapes;
- the version gate;
- `DUPLICATE` repeating the disposition;
- signed-delta revision ordering;
- macro expansion, including the JPY exponent and negative payouts;
- the 4096-byte limit on the expanded URL;
- a golden HMAC signature for a URL-template GET;
- every Invalid example above.

**Operations:**
- `GET /v1/lcp/metrics` gains counters for disposition outcome and for
  postback delivery success and failure by host;
- per-sender redirect-rate measurement is a SHOULD for relays;
- reference-platform flags `LCP_FEATURE_SALE_DISPOSITION`,
  `LCP_FEATURE_URL_POSTBACKS` and `LCP_FEATURE_TEST_OUTCOMES`, each off by
  default.

## Alternatives considered

- **Decimal-string or float prices, the buyer API's own shape.** Rejected.
  Floats round, and a decimal string in one field next to minor units
  everywhere else (LEP-0010) is worse than either alone. Human-readable
  decimals live in `{payout}` and SDK formatters instead.
- **A new `disposition` message type sent after the ack.** Rejected. It
  doubles round trips for the common synchronous case, and the two-adopter
  rule applies to new message types. The async case is already covered by
  events.
- **New event names (`SOLD`, `UNSOLD`, `RETURNED`).** Rejected for v1.3.0.
  `ACCEPTED`, `REJECTED` and `REFUNDED` already carry those meanings, and
  `subscription.event_types` is a closed enum. Friendlier names for trackers
  are a deferred question.
- **Extension-only (`lcp.sale.disposition` in an ack extension).**
  Rejected. `ack` has no `extensions` container, and adding one is a larger
  core change than one optional object.
- **Keep it deployment-specific**, as SpearPointX's planned response body
  would. Rejected, because every buyer, network and publisher re-invents
  it, and that is the fork this record exists to prevent.

## Rollout, migration, observability, and rollback

1. **Schemas, vectors and the reference platform** land behind three
   feature flags, off by default. `capabilities().features` advertises each
   flag only when it is on.
2. **The reference deployment (SpearPointX)** enables
   `sale_disposition` and `test_outcomes` for publishers on `1.3.0`, then
   `url_postbacks` after the security/privacy review signs off.
3. **Publishers move by sending `lcp.version` `1.3.0`.** A sender that
   stays on `1.2.0` keeps today's ack.
4. **Observability:** outcome counters, postback success rate by host,
   redirect rate per sender, and test-versus-live split.
5. **Rollback:** turning a flag off removes the advertisement and the
   behaviour. Acks to `1.3.0` senders omit `disposition`, which senders
   MUST tolerate because it is optional. URL-template subscriptions are
   disabled, not deleted, and resume when the flag returns.

## Deferred questions

- **Re-post at the counter price.** `price_rejected` returns
  `counter_price_cents`, but a direct `lead` has no floor field to re-post
  with. Trigger: a second buyer API with price-reject semantics, or a sender
  asking to automate re-posts. Candidate: a `lead`-side floor reusing
  LEP-0009's bounds.
- **Friendly event aliases for trackers** (`{event_alias}` → `sold`,
  `unsold`, `returned`). Trigger: tracker-integration feedback that raw
  names are a support burden.
- **Redirect completion reported downstream.** A relay knows when the
  consumer arrived, and a buyer may want that as an event. Trigger: a buyer
  asking for it, or a dispute over redirect rate.
- **Shared-lead dispositions** (LEP-0003, multiple buyers per lead).
  Trigger: the first shared-lead deployment that pays publishers per slot.
- **Postback signature for GET.** §4.5 substitutes the expanded URL for the
  body. Review should confirm that this is sufficient against replay through
  a tracker, given the idempotency key is a header, not part of the URL.
