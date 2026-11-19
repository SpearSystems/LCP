# Buyer delivery mapping

Most buyers do not speak LCP. A buyer delivery mapping is a versioned JSON
document that turns an LCP `post` into one request to a buyer's own API, and
the buyer's response into an LEP-0012 outcome: `sold`, `unsold`, `pending`
or `price_rejected`. It is the outbound counterpart of
[publisher mapping](PUBLISHER-MAPPING.md).

A mapping is data, not code. It has no expressions or templates, a closed
transform set and a closed rule language. It is a platform document: it is
never sent on the wire, and SDKs neither build nor read it.

- **Source:** [LEP-0015](../governance/LEPs/LEP-0015-buyer-delivery-mapping.md),
  accepted for v1.4.0 ([decision record](DECISIONS-v1.4.md)).
- **Schema:** [`schemas/buyer-mapping.json`](../schemas/buyer-mapping.json),
  `format_version` `1.0`.
- **Vectors:** [`test-vectors/buyer-mapping.json`](../test-vectors/buyer-mapping.json),
  the `BMAP` suite.

The rules below are normative for any platform that executes buyer
mappings. MUST, SHOULD and MAY are used as in RFC 2119.

## 1. Document and upsert

Every object in a mapping is closed. A platform MUST refuse at upsert, and
MUST NOT activate, a mapping that:

- fails `schemas/buyer-mapping.json`: an unknown key, transform or rule
  operator, a path outside the grammar, a `format_version` it does not
  implement, a URL with a macro, query placeholder or fragment, a
  `response.default` of `sold`, or `max_reposts` above `1`;
- reads a `payload.attributes.*` path that the declared vertical does not
  define at every listed `schema_versions` entry;
- reads a path that reaches an `x-lcp-regulated` field for a buyer that is
  not enrolled for that field under LEP-0013;
- reads a `restricted` path the buyer's requirement profile does not allow;
- has a `url` or `test_url` that fails the platform's egress rules (the
  same rules as `webhook_url`);
- sets a header the platform owns (`Host`, `Content-Length`,
  `Content-Type`, `Transfer-Encoding`, `Connection`), or names the
  idempotency header twice;
- has two `json` body targets where one is a prefix of the other
  (`a` and `a.b`);
- turns on `counter.repost` without declaring
  `response.fields.counter_price`.

The privacy checks MUST run again at send, and fail closed.

**Activation.** Upsert a new version, send synthetic test posts to
`test_url`, compare the decoded results, then deactivate the old version.
Rollback is the reverse: deactivate the new version and the previous one
resumes.

## 2. Paths

- A source path starts at the post's `lcp` object: `message.<field>` or
  `payload.<field>`. No other roots exist.
- A segment is a property name, `["..."]` for a key that contains a dot (an
  extension name), or `[n]` for an array index. There are no wildcards,
  filters or functions.
- A response path uses the same segments, rooted at the decoded body.
- A `body` target key is a dotted path in the buyer's body. With
  `encoding: json`, dots build nested objects. With `encoding: form`
  (`application/x-www-form-urlencoded`), the key is sent as written and
  every value MUST be a scalar.
- Headers carry only `const` and `secret` values. Lead data never rides a
  header or a URL.

## 3. Values and transforms

A body target takes exactly one source:

- `{"from": path}` with optional `transform` (applied in order), `map`
  (exact string match after transforms), `map_default`, `default` and
  `required`. A missing (or `null`) source takes `default`; without one the
  target is omitted, or, if `required`, the delivery stops (§6).
- `{"const": scalar}`.
- `{"secret": name}` with an optional `prefix`. The value is resolved from
  the platform secret store at send time. It MUST NOT appear in a mapping,
  a vector, a log, an error or an audit record.

Transforms are a closed set:

| Transform | Input | Result |
|---|---|---|
| `trim`, `lower`, `upper` | string | the string trimmed or case-folded |
| `string` | scalar | the JSON text of a number or boolean; a string unchanged |
| `integer` | integer, integral number, or decimal-digit string | integer |
| `boolean` | boolean, `"true"`/`"false"`, or `0`/`1` | boolean |
| `e164` | phone string | E.164, using `payload.location.country_code` for a national number |
| `phone_national` | E.164 `+1` number | its 10 national digits |
| `date` | RFC 3339 date-time or full-date | `YYYY-MM-DD` as written (no time-zone shift) |
| `date_time_utc` | RFC 3339 date-time with an offset | `YYYY-MM-DDTHH:MM:SSZ` |
| `cents_to_units` | integer minor units | divided by 10^e of `payload.currency` (ISO 4217); fails unless exact |
| `units_to_cents` | integer whole units | multiplied by the same factor |

A transform that cannot apply is a mapping failure (§6). It is never
coerced, rounded or silently dropped.

## 4. Request

- The method is `POST` and the URL is static.
- A post with `message.test: true` goes only to `test_url`. Without a
  `test_url` it is not delivered, and the lead gets the platform's test
  outcome. A test post MUST never reach `url`.
- `url` and `test_url` MUST pass the platform's egress rules at upsert and
  at send: `https`, the SSRF checks, and no redirects followed.
- **Idempotency.** With `idempotency`, the platform sends
  `message.idempotency_key` on every attempt, and may retry after the
  request was sent. Without it, the platform retries only failures where no
  request bytes were sent (DNS, connect or TLS errors). A failure after
  sending becomes `pending` (§5), so a buyer that cannot deduplicate is
  never sold the lead twice.
- `retry_on_status` re-enters the retry queue only under the idempotency
  rule above. Otherwise the status falls through to the rules.

## 5. Response

The body is read up to `max_bytes`, capped by the platform's own limit,
decoded per `encoding`, and then discarded. Only the decoded fields
persist. A body over the limit is undecodable.

Rules are evaluated in order and the first match wins. A `when` clause
holds when every test holds: `status` (a list of codes), and `path` with
one of `equals`, `in` or `exists`. Comparison is type-strict: `1` does not
equal `"1"`. No match takes `default`, which is `pending` or `unsold` and
never `sold`.

**Decoded fields.**

- `price` and `counter_price` convert from `units` or `cents` into integer
  minor units of the post's currency. A value outside `1`–`100000000`
  minor units, or with a fractional minor unit, fails to decode.
- `redirect_url` is kept only if it starts with `https://` and is at most
  2048 characters. It is handled as LEP-0012 §2 requires: relayed to the
  sender, never fetched, never logged.
- `buyer_reference` is kept if it is at most 128 characters, for
  reconciliation.
- `reason` maps the buyer's value onto a known `rejection_reason`; an
  unmapped value becomes `map_default`, or `other`. A rule's own `reason`
  takes precedence. Buyer free text is never retained.

**Money is never invented.** A `sold` pays the decoded `price`; with no
`price` declared it pays the posted `payload.price_cents`. A `sold` whose
declared `price` fails to decode is `pending`, never `sold` at the posted
price.

**Ambiguity is `pending`:** a timeout after sending, a 2xx body that fails
to decode, and a match on `default: pending`. A `pending` result is
resolved by reconciliation before the platform's final window. If it is
still unresolved then, the lead's result is `REJECTED` with
`rejection_reason` `buyer_unconfirmed`.

## 6. Failures before sending

A missing `required` target, a failed transform, an unconfigured secret, or
a regulated path the buyer is not enrolled for stops the delivery before
any bytes are sent. The platform records the delivery as `FAILED` with
reason `mapping_failed`, records the target key (never the value), dead-
letters it, and gives the lead the outcome of an undelivered post (SPEC §9).

## 7. Native LCP buyers

A buyer whose profile declares `lcp_versions` including `1.3.0` (parsed
semver) needs no mapping. The platform posts at `lcp.version` `1.3.0`,
reads the response (bounded by its response limit) as an LCP `ack`,
validates it against `schemas/ack.json`, and keeps only `status`,
`rejection_reason` and `disposition`. A 2xx response without a valid ack
for that post keeps the v1.3 behaviour. The buyer's disposition is the
buyer's statement to the platform; the sender's disposition is computed by
the platform's own payout policy.

## 8. Counter re-post

On a `price_rejected` with a valid `counter_price`, the platform MAY
re-post once to the same buyer at the counter price, if all hold:

- `counter.repost` is true (`max_reposts` is at most `1` in format `1.0`);
- the counter is at or above the platform's floor for this lead and offer
  (platform policy, never sent to the buyer);
- the post-response deadline (SPEC §9) has not passed.

The re-post has a new `message.id` and the original
`message.idempotency_key` suffixed `:r1`. Otherwise, or if the re-post is
refused, the buyer result is `unsold`. A buyer-leg `price_rejected` is
never forwarded to the sender as-is: the sender sees one final result.

## Reference platform

The reference platform's executor is
[`lcp_platform/buyer_mapping.py`](../implementations/reference-platform/lcp_platform/buyer_mapping.py),
run against the `BMAP` vectors by `tests/test_buyer_mapping.py`.

- **Flag.** `LCP_FEATURE_BUYER_MAPPING`, off by default. Off, every buyer
  receives the LCP envelope at `webhook_url` and the response is unread,
  exactly as in v1.3. A deployment MUST NOT turn it on before the named
  security and privacy sign-off in [DECISIONS-v1.4](DECISIONS-v1.4.md) D3.
- **Admin.**

  ```bash
  lcp-platform-admin buyer-mapping upsert --file mapping.json
  lcp-platform-admin buyer-mapping list --buyer-id buyer_example_loans
  lcp-platform-admin buyer-mapping deactivate --mapping-id example-loans-direct-v2 --version 2.0.0
  ```

  With the flag on, a post uses the highest active mapping version for its
  buyer, vertical and `schema_version`. The buyer still needs its
  credential and an offer `webhook_url`; the mapping's URL replaces the
  destination, and the buyer gets no LCP `DELIVERED` event.
- **Secrets.** `{"secret": "example_loans_api_key"}` reads the environment
  variable `LCP_BUYER_SECRET_EXAMPLE_LOANS_API_KEY` at send time. Mount it
  from the deployment's secret manager.
- **Restricted and regulated paths.** The mapping reads only the post the
  platform built for that buyer, which already carries exactly what the
  buyer may receive, so a `restricted` path needs no separate allowance.
  The reference platform enrols no buyer for regulated identifiers (it does
  not advertise `features.regulated_identifiers`), so every regulated path
  is refused at upsert and at send.
- **Results.** Each decoded result is kept in `buyer_results` (outcome,
  reason, deciding rule, buyer reference), the source for per-mapping
  outcome counts and the default-rule drift alarm. Response bodies and
  redirect URLs are never stored. The platform acknowledges leads
  asynchronously, so a buyer's `redirect_url` has no sender ack to ride and
  is dropped.
- **Payables.** `sold` is `payable` at the decoded price, `unsold` is
  `not_payable`, and `pending` stays `pending` with reason
  `buyer_pending`. An operator confirms a pending sale with an admin
  `ACCEPTED` event; one still pending after 24 hours becomes `not_payable`
  with reason `buyer_unconfirmed`, and the publisher receives `REJECTED`
  with that reason.
