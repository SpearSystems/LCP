# LEP-0010 — Multi-currency pricing: settlement currency, FX metadata, and auction comparability

- **Title:** Multi-currency pricing: settlement currency, FX metadata, and auction comparability
- **Author(s):** LCP maintainers
- **Date:** 2026-11-04
- **Status:** **Accepted (maintainer, 2026-11-04)** — recorded under the
  [D1](../../docs/DECISIONS-v1.2.md#d1--trigger-waiver-batch-of-2026-11-04) trigger waiver;
  implementation is gated on the blocking tests in [Tests](#tests)
- **Target version:** v1.2.0 (additive MINOR; optional fields on two existing
  closed payloads, no new message type, no new `LCP-*` error code)
- **Decision basis:** [`docs/DECISIONS-v1.2.md` D7](../../docs/DECISIONS-v1.2.md#d7--multi-currency-auction-semantics)
- **Discussion:** recorded through the v1.2 decision pass (D7). No separate
  public `[LEP]` issue was opened for this number; this record is the durable
  decision record.
- **Roadmap trigger:** `docs/V1.1-ROADMAP.md` §"Multi-currency auction
  semantics" — **"buyers in different currencies must compete in the same
  auction"** — and the v1.0 deferred row `spec/00-conformance.md:162` ("Multi-currency
  pricing … `price_cents_original` + `fx_rate` fields, or bilateral").
  **The trigger is not met** (see the waiver note below).

## Decision (maintainer, 2026-11-04)

**Accepted.** Recorded by the maintainer under D7 of the v1.2 decision
record. The wire shape, the comparability rule, the exclusion reason
vocabulary, and the rounding rule in this document are the decision D7
describes; this LEP is where D7's one-paragraph table is made
implementable (types, requiredness, arithmetic, the exclusion surface, and
the tests that block a merge).

**Waiver honesty (D1).** The roadmap trigger — buyers in different
currencies competing in one auction — produced **no adopter evidence** in
this repository or its tracker at the time of this decision. D7 is built
under the batch waiver of 2026-11-04, not on new adoption. Accordingly:

1. the surface is additive only, advertised through `capabilities()`, and
   unreachable for a peer that does not read the advertisement
   (Compatibility below);
2. it ships **disabled** in the reference platform
   (`LCP_FEATURE_FX_PRICING` off by default) while CI, vectors, and the
   conformance suite exercise it;
3. the deterministic evidence that stands in for the missing adopter
   signal is enumerated in [Tests](#tests) — executed vectors plus
   platform tests, not a testimonial; and
4. the re-evaluation date is **2027-05-04** (registry row), at which point
   the waiver must be re-argued or the surface withdrawn.

No independent ARB dossier is claimed for this decision, and no adopter,
deployment, or pilot is named anywhere in this record. Where the evidence
below is a measurement of this repository, it cites the file and line it
was read from.

## Problem statement and evidence

Cross-border lead generation is the commercial case: a US buyer purchasing
Australian mortgage leads, or an AU platform reselling to a UK call centre.
LCP prices everything in **integer minor units of one currency** and gives
the auction no way to express a second one.

**What the protocol has today.** `ping.floor_price_cents` +
`ping.currency`, `bid.bid_price_cents` + `bid.currency`, and
`post.price_cents` + `post.currency` (`spec/02-envelope.md:103-104` for ping, `:135-136` for bid,
`:170-174` for post); `currency` is schema-required on `ping` and `post`
(`schemas/ping.json:13-14`, `schemas/post.json:10-11`) and required on
`bid` only when `decision = accept` (`schemas/bid.json:62-67`). There is
no rate, no native-amount field, and no notion of settlement currency
distinct from the quoted currency.

**What that costs, measured.** The reference platform's auction loop
compares bids to the offer only when the strings are equal, and otherwise
drops the candidate **silently**:

```python
# implementations/reference-platform/lcp_platform/router.py (pre-D7 shape)
if bid["bid_price_cents"] < offer["floor_price_cents"]:
    continue
if bid["currency"] != offer["currency"]:
    continue            # ← a valid AUD bid against a USD offer simply vanishes
```

A cross-currency bid is therefore not "unsupported with an explanation";
it is invisible. The buyer receives `RECEIVED`
(`router.py` `submit_bid`, ack status `RECEIVED`), hears nothing further —
which is also what a *losing* bid hears (`spec/02-envelope.md:145`, "losers
receive no further messages") — and cannot tell a currency refusal from an
auction loss. A platform operator cannot count the refusals either: no
audit row, no reason, no metric.

**What the protocol already knows how to do.** `FINAL_PRICING` events
carry `winning_price_cents` in integer minor units plus `currency`, with a
typo guard (`spec/02-envelope.md:266`) and an explicit settlement-neutrality rule that
this LEP reuses verbatim for analytics: a pricing *report* must never
re-price a live auction or move a payable (`spec/02-envelope.md:278-292`). Prices are
already integer minor units everywhere, so the missing piece is not a unit
change — it is a second currency plus the metadata that lets two of them be
compared honestly.

**Why "just convert it" is not a design.** Minor units are not
comparable across currencies (100 JPY and 100 USD differ by ~150×), minor
units are not even comparable across the *same* pair when the ISO 4217
minor-unit exponents differ (USD has 2, JPY has 0), and a rate is a
time-bounded observation from a named source, not a constant. Any of the
three handled by guesswork produces a charge nobody can reconstruct.

## Scope

- **Core protocol (additive MINOR):** six optional fields inside
  `post.pricing` (`currency`, `original_price_cents`, `fx_rate`,
  `fx_source`, `fx_timestamp`, `fx_expires_at`) and the four `fx_*` fields
  on `bid`; one normative ranking rule in SPEC §8; one normative
  settlement/rounding rule; one exclusion-reason vocabulary on the
  routing-decision surface. `message.type` is untouched (closed enum,
  `spec/05-capabilities.md:32`), no new envelope shape, no new `LCP-*` code.
- **SPEC text** (split documents, D10): `spec/03-payloads.md` §3, `spec/02-envelope.md`
  §4 (bid field table + post `pricing` table), `spec/04-errors.md` §5 (no new
  code; the mapping table below), `spec/05-capabilities.md` §8
  (`features.multi_currency` advertisement and the comparability rule).
- **Reference implementation:** `LCP_FX_MAX_AGE_SECONDS` (default 300),
  `LCP_FEATURE_FX_PRICING` (default off), the comparability/exclusion path
  in the auction, the `bid.non_comparable` audit row, and the post-side FX
  echo. Enumerated in [Impact](#sdk-openapi-mcp-conformance-and-operational-impact).
- **Not in scope:** a platform-issued reference rate or any rate service;
  a registered list of FX providers; triangulation between more than one
  source in one auction; FX-aware `quote`/`counter` negotiation (LEP-0009);
  per-market accounting or tax rounding rules; refund/chargeback FX policy;
  multi-currency *quotas* or *monthly minimums* on offers.
- **Deliberately unchanged:** the offer's `floor_price_cents` +
  `currency` stay the auction's single settlement basis
  (`schemas/offer.json`, required). A platform does not gain a second
  settlement currency per offer; it gains honest handling of bidders and
  buyers who price in another one.

## Proposed wire shape

### 1. Effective settlement currency (normative resolution ladder)

The **settlement currency** is the currency the platform charges, invoices,
and pays out in. It is resolved once per price, in this order:

1. `post.pricing.currency`, when present;
2. otherwise `post.currency` (the payload-level field, required by
   `schemas/post.json:10-11` and populated from the offer by the reference
   platform, `messages.py:223`);
3. otherwise `USD`.

For a bid, the settlement currency is the ping's offer currency
(`offer.currency`, required), and a platform that cannot resolve it falls
back to `USD` — the reference platform's existing default
(`router.py:1205-1208`: `str(currency or "USD").upper()`).

**Reconciliation with the registry row.** The registry row says "absent
`currency` means USD". That is step 3 and it is the *terminal* default.
It cannot be step 1: `post.currency` is required, and the shipped example
posts a lead at `price_cents: 2200, currency: "AUD"`
(`examples/post.json:18-19`). If an absent `pricing.currency` overrode a
present `payload.currency`, every existing post would be silently
re-denominated from AUD to USD the day a reader adopts this LEP. Step 2
exists so that adopting the field changes no existing document's meaning;
step 3 exists so a payload carrying neither (schema-invalid today, but
seen in the wild) is read rather than guessed. **A v1.0/v1.1 post keeps
its current settlement currency under this ladder.**

When both `post.pricing.currency` and `post.currency` are present and
differ, the payload is malformed: the receiver MUST reject it
(`LCP-100 VALIDATION_ERROR`, `field: "pricing.currency"`). Two settlement
currencies in one billing record is exactly the ambiguity this LEP exists
to remove; picking one would be coercion.

### 2. `post.pricing` — field table

All six fields are **optional**. A v1.0/v1.1 sender emits none of them and
is unaffected (see *Compatibility, versioning, and deprecation impact*). Types below are what
`schemas/post.json:48-129` must carry (`additionalProperties: false`, so
anything not listed is a schema rejection).

| Field | Type | Constraint | Required when |
|---|---|---|---|
| `currency` | string | ISO 4217 alpha-3, `^[A-Z]{3}$` (the existing `core.json#/$defs/currency`, `schemas/core.json:499-501`). MUST equal `post.currency` when that is present. MUST NOT be the *native* currency when a conversion occurred — this field is the settlement currency, always. | Optional. Absent resolves per §1's ladder (`post.currency`, else `USD`), which keeps v1.0/v1.1 payloads at their current settlement currency. SHOULD be present whenever any `fx_*` field is present. |
| `original_price_cents` | integer | `minimum: 0`. **Native** minor units of the winning bid's `bid.currency` (see §3). | REQUIRED when any `fx_*` field is present; MUST be absent when no conversion occurred (there is no "original" on a single-currency post). |
| `fx_rate` | string (decimal) | Plain decimal, no sign, no exponent, no grouping, no leading-zero integer part, a mandatory fractional part of 6–12 digits, value in `[0.000001, 1000000]`. Normative pattern in 4.1. Direction: **native major units per 1 settlement major unit**. | REQUIRED when the award converted a cross-currency bid; MUST be absent otherwise. |
| `fx_source` | string | `^(bilateral|[a-z0-9][a-z0-9._-]{0,63})$`. Named rate provider, or the literal `bilateral` for a rate the two parties agreed directly. Compared by **exact byte equality**; case is normalised by the pattern, never by the receiver. | REQUIRED when `fx_rate` is present. |
| `fx_timestamp` | string (date-time) | Canonical whole-second UTC: `YYYY-MM-DDTHH:MM:SSZ`, exactly one `Z`, no fractional part, no offset (the LEP-0002 canonical form, `spec/01-transport.md:51-59`). The instant the rate was observed. | REQUIRED when `fx_rate` is present. |
| `fx_expires_at` | string (date-time) | Same canonical form. MUST be strictly later than `fx_timestamp`. Absolute end of the rate's validity. | REQUIRED when `fx_rate` is present. |

**Why the native amount is not self-describing (known limitation).**
`original_price_cents` is denominated in the winning bid's `bid.currency`,
which the receiving buyer sent and therefore holds, so the buyer can always
recompute the charge. A third party reading only the post — a publisher, an
auditor, a dispute reviewer — cannot decode the native currency from the
post alone, because D7's field list carries no native-currency code. This
LEP does not add a seventh field (the decision fixed the list); the gap is
recorded as deferred question **Q-1** with a concrete trigger, and until
then the platform MUST be able to answer it from its own bid record.

### 3. `bid` — field table (the price side that feeds the auction)

Four optional fields, flat on the bid payload (`bid` has no `pricing`
block; the existing price fields are flat, `schemas/bid.json:25-31`):

| Field | Type | Constraint | Required when |
|---|---|---|---|
| `currency` *(existing)* | string | ISO 4217 alpha-3 (existing). **Semantics refined, not changed:** this is the currency the bid is *expressed in* — i.e. the native currency of `bid_price_cents`. In v1.0/v1.1 it necessarily equalled the offer's currency; from v1.2 a bid MAY differ from it. | REQUIRED when `decision = "accept"` (existing condition, `schemas/bid.json:62-67`). |
| `bid_price_cents` *(existing)* | integer | `minimum: 0`. **Native** minor units of `bid.currency`. | REQUIRED when `decision = "accept"` (existing). |
| `fx_rate` | string (decimal) | As `post.pricing.fx_rate`, same direction and same pattern. | REQUIRED when `bid.currency` differs from the offer's currency; **MUST NOT** be relied on otherwise. |
| `fx_source` | string | As `post.pricing.fx_source`. | REQUIRED with `fx_rate`. |
| `fx_timestamp` | string (date-time) | As `post.pricing.fx_timestamp`. | REQUIRED with `fx_rate`. |
| `fx_expires_at` | string (date-time) | As `post.pricing.fx_expires_at`; MUST be strictly later than `fx_timestamp`. | REQUIRED with `fx_rate`. |

**No `original_price_cents` on the bid.** On a bid the native amount *is*
`bid_price_cents`; a second copy of the same number in the same message is
a divergence waiting to happen. The post echoes it once
(§2, `original_price_cents`) so the delivered billing record carries both
sides of the conversion.

**A same-currency bid ignores its FX fields (normative).** When
`bid.currency` equals the offer's settlement currency, the bid compares
directly by integer minor units and any `fx_*` metadata present is **inert
and MUST be ignored for comparison** (it is still schema-validated). No
conversion happens, so no coefficient can be wrong; rejecting the message
instead would punish a buyer that sends a uniform envelope. The reference
platform already behaves this way (`router.py:1211-1212` returns the amount
before reading metadata).

Rates and amounts are covered by the existing envelope HMAC and replay
window — no new authentication surface, no new secret.

### 4. Rate semantics and conversion arithmetic

#### 4.1 The rate is a plain decimal with declared precision

`fx_rate` is `native major units per 1 settlement major unit`. AUD per USD
is `1.520000`; USD per JPY is `0.00636943` (≈ 157 JPY per USD).

Tightened normative pattern (supersedes the looser shape currently landed in
`schemas/post.json:79-81` and the reference platform's
`_FX_RATE_RE`, `router.py:167-170`):

```
^(?:[1-9][0-9]*\.[0-9]{6,12}|0\.[0-9]*[1-9][0-9]{5,11})$
```

The pattern rejects, and a receiver MUST reject with
`LCP-100 VALIDATION_ERROR` / `field: "fx_rate"`: an exponent
(`"1.52e0"`), a comma decimal separator (`"1,52"`), a sign, whitespace, a
missing fractional part (`"2"`), zero (`"0"`), a leading-zero integer part
(`"007.000000"`), fewer than six fractional digits for a value ≥ 1
(`"1.52"`), and fewer than six *significant* fractional digits for a value
< 1 (`"0.000123"`). Trailing zeros are permitted and are read as declared
quantization, not as extra precision.

**Measured gap in the tree as it stands.** The landed schema pattern
`^[0-9]+(\.[0-9]{6,})?$` accepts `"1"`, `"0"`, `"007.000000"` and
`"0.000123"` (verified by validating the example payloads of this LEP
against `schemas/post.json` with `jsonschema` 4.26.0 from the repository
`.venv`; each of those four values **passes** the landed pattern). The
tightening is an implementation obligation, not an optional polish: an
integer-only rate and a zero rate are the two shapes a hand-rolled writer
produces first.

Bounds: value in `[0.000001, 1000000]` (all ISO 4217 fiat crosses fall
inside it), fractional digits ≤ 12. Twelve digits plus the `100000000`
minor-unit ceiling already used for typo-guarded prices
(`spec/02-envelope.md:266`) keeps exact arithmetic cheap, but **an implementation
MUST NOT rely on 64-bit integer cross-multiplication of unscaled
operands** — scale deliberately or use arbitrary-precision decimal. The
reference platform uses Python `Decimal` (`router.py:1244`).

#### 4.2 Minor-unit exponents are load-bearing

`original_price_cents` / `bid_price_cents` are **minor** units; `fx_rate`
is a **major**-unit quote. Converting therefore needs the ISO 4217
minor-unit exponent of both currencies:

```
settlement_minor_exact = native_minor × 10^(exp_settlement − exp_native) ÷ fx_rate
```

Worked, with the exponents pinned (USD = 2, AUD = 2, JPY = 0):

- `native_minor 50000` USD (USD 500.00) → settlement JPY at
  `fx_rate "0.00636943"`:
  `50000 × 10^(0−2) ÷ 0.00636943 = 78499.959965…` JPY →
  charge `78500` JPY (half-up, §7).
- The same division **without** the exponent factor yields `7849995.96`
  — a 100× overcharge. This is the failure mode the rule forbids.

A platform MUST carry the ISO 4217 exponent table it uses, and MUST treat
a currency whose exponent it cannot resolve as
`fx_currency_malformed` / `fx_settlement_currency_malformed` rather than
assuming 2. The reference platform has **no exponent table** today and
divides minor units directly (`router.py:1244`); it is correct only for
same-exponent pairs (AUD/USD, USD/EUR) and is wrong for USD/JPY, USD/KRW,
USD/ISK and every 0- or 3-exponent currency. Blocking test
**T-EXPONENT** covers it.

#### 4.3 Exact comparison, never a rounded or clamped value

Every comparability decision uses the exact decimal value of
`settlement_minor_exact`. No float, no bank rounding, no clamping to the
floor or to the guardrail. `Decimal("2200.5") < Decimal("2200.6")` decides
the auction; `2201` does not.

### 5. Comparability rule (normative)

**C1.** Two prices MUST NOT be compared unless their settlement currencies
are the same, **or** each is accompanied by an FX bundle that is present,
well-formed, non-stale, non-expired, and consistently sourced.

**C2.** "Present" means all four of `fx_rate`, `fx_source`, `fx_timestamp`,
`fx_expires_at` are non-empty on the candidate.

**C3.** "Non-stale" is evaluated against **one** `evaluation_time` shared by
every candidate of one auction evaluation (a single routing sweep), not per
candidate:
`evaluation_time − fx_timestamp ≤ LCP_FX_MAX_AGE_SECONDS` (default `300`,
**inclusive**, matching the replay-window convention of `SEC`/`ERR-016`) —
and `evaluation_time < fx_expires_at`. A `fx_timestamp` more than
`LCP_FX_MAX_AGE_SECONDS` **in the future** relative to `evaluation_time` is
also stale (clock-skew guard, same reason string). `fx_expires_at` MAY be
shorter than the max-age window and MUST NOT be read as extending it.

**C4.** "Consistently sourced" means every converted candidate in one
evaluation carries the same `fx_source` value, byte for byte. If the
converted candidates of one evaluation carry two or more distinct values,
**all** converted candidates in that evaluation are non-comparable
(`fx_source_mixed`) and only same-currency candidates remain. The platform
MUST NOT pick one source, average sources, or triangulate.

**C5.** A non-comparable offer MUST be **excluded** with the reason string
from §6, recorded, and left otherwise untouched: no default rate, no
assumed parity, no "rate of 1", no clamping to the floor, no rewriting of
the bid payload. Excluding with an explanation is reversible and auditable;
guessing a rate is not (D7 rationale).

**C6.** A non-comparable offer MUST NOT fail the auction. If every
candidate is excluded, the existing "no candidates → expire the lead" path
runs unchanged. A same-currency candidate is never affected by another
candidate's FX metadata.

**C7.** Format-invalid FX values (pattern, timestamp form, source shape)
are an intake fault where the platform can see them at intake
(`LCP-100`, §6), and a comparison-time exclusion otherwise — because the
feature flag can be enabled while bids are already stored, and stored bids
are never re-validated. Both paths use the same reason strings.

**C8.** The auction's ordering key is the exact settlement value, then the
existing deterministic tie-breaks (`estimated_contact_seconds`,
`received_at`, `buyer_id` — `router.py:1359-1367` in the current tree).
FX adds no new tie-break and changes no existing one.

### 6. Exclusion reasons and error-code mapping

The reason strings below are the vocabulary the platform emits. They are
**documented known values of the open `rejection_reason` enumeration**
(`schemas/ack.json:44`, `$comment`: "Open enum") and, at comparison time,
the `reason` of a `bid.non_comparable` audit row
(`router.py:1246-1261`: `action="bid.non_comparable"`, with `lead_id`,
`offer_id`, `buyer_id`, `currency`, `reason`). No new `LCP-*` code is
allocated and no schema enum changes: the open-enum mechanism carries the
meaning, exactly as LEP-0007's `ignored` did.

| Reason | Emitted when | Intake code (where detectable at intake) |
|---|---|---|
| `fx_rate_missing` | a cross-currency candidate carries no complete FX bundle (C2) | `LCP-100 VALIDATION_ERROR`, 400, `field: "fx_rate"` |
| `fx_rate_stale` | `evaluation_time − fx_timestamp > LCP_FX_MAX_AGE_SECONDS`, or the timestamp is too far in the future (C3) | `LCP-100`, 400, `field: "fx_timestamp"` |
| `fx_rate_expired` | `evaluation_time ≥ fx_expires_at` | `LCP-100`, 400, `field: "fx_expires_at"` — a rate already expired on arrival can never be used |
| `fx_rate_malformed` | `fx_rate` is not a string, or fails the pattern, or is `≤ 0` | `LCP-100`, 400, `field: "fx_rate"` |
| `fx_amount_malformed` | the price is not an integer ≥ 0 | `LCP-100`, 400, `field: "bid_price_cents"` |
| `fx_currency_malformed` | the native currency is not ISO 4217 alpha-3, or its minor-unit exponent is unresolvable | `LCP-100`, 400, `field: "currency"` |
| `fx_settlement_currency_malformed` | the settlement currency is not ISO 4217 alpha-3, or its exponent is unresolvable (§1) | `LCP-100`, 400, `field: "pricing.currency"` |
| `fx_source_missing` | `fx_source` is absent, not a string, or empty | `LCP-100`, 400, `field: "fx_source"` |
| `fx_timestamp_malformed` / `fx_expires_at_malformed` | not the canonical whole-second UTC form, or unparseable | `LCP-100`, 400, `field: "fx_timestamp"` / `"fx_expires_at"` |
| `fx_source_mixed` | two or more distinct `fx_source` values among the converted candidates of one evaluation (C4) | — comparison time only (a mixed set is not detectable per message) |
| `price_below_floor` | the exact settlement value is below `offer.floor_price_cents` | — comparison time only |

Notes that keep the vocabulary honest:

- **`price_below_floor` is required for converted candidates.** Today the
  floor test is a silent `continue` for every candidate
  (`router.py:1324-1325`); for a converted candidate it MUST be recorded
  with this reason, because "your AUD bid was below a USD floor after
  conversion" is exactly the surprise this LEP removes. Same-currency
  floor exclusions MAY keep the existing silent behaviour (no regression
  is introduced either way).
- **`LCP-014` is not allocated.** `docs/V1.2-EXECUTION-PLAN.md:111`
  (W1-09) offers "stale rate → `LCP-014` (or documented existing code)".
  This record chooses the documented existing code: a stale rate is a
  value-validity verdict on an authenticated message, and `LCP-100
  VALIDATION_ERROR` is the code the platform already uses for
  "validation rejection, not a store" (`spec/02-envelope.md:228`, `:276`;
  `spec/04-errors.md:24`).
  Allocating `LCP-014` for one of eleven reasons would leave the other ten
  mapping to nothing; the reason string, not the code, carries the
  diagnosis.
- `LCP-001 INVALID_FORMAT` stays the request-framing code (bad JSON, wrong
  content type, oversized body — `service.py:79-110`) and is not used for
  a price-value verdict.

### 7. Rounding, settlement, and what is payable

**One rounding event of record.** The auction ranks on
`settlement_minor_exact` (§4.3). When a cross-currency candidate wins, the
charge is computed **once**, at award, as the half-up rounding of that
exact value to an integer minor unit
(`(x + 0.5).floor` for `x ≥ 0`; prices are non-negative by schema, so the
negative-half-up ambiguity does not arise):

| Exact settlement value | Charge of record (half-up) |
|---|---|
| `220000` | `220000` |
| `2200.5` | `2201` |
| `78499.959965…` | `78500` |

That integer is `post.pricing.final_price_cents` and `post.price_cents`, it
is written once, and it is **immutable**: a later rate, a later auction
sweep, a re-ranking, or a retry MUST NOT recompute or adjust it. The
reference platform already rounds exactly there, after ranking
(`router.py:1388-1390`), with the comment "the rounded cents never feed
back into ranking".

**Rounding can collapse an order but must not create one.** Half-up is
monotone, so it can never invert a strict order — and it *can* turn one
into a tie: candidates at exact `2200.5` and `2200.6` both round to
`2201`, so a ranking performed on rounded values would lose the winner
that the exact values distinguish. The auction therefore uses exact values
(C8), and rounding is confined to the charge.

**Rounding must not admit a sub-floor bid.** Exact `149999.666…` (native
`449999` AUD cents at `fx_rate "3.000000"`) is below a `150000` USD-cent
floor even though half-up would present it as `150000`. The floor test uses
the exact value; the bid is excluded with `price_below_floor` (§6) and no
charge is created.

**A converted amount that cannot be expressed is excluded, not rounded to
zero.** If `settlement_minor_exact < 0.5`, the platform MUST exclude the
candidate (`price_below_floor` when a floor exists, otherwise the same
reason with the audit note `unrepresentable`) rather than record a `0`
charge. A zero charge is a commercial fact (a free lead), never a rounding
artifact.

**What is payable.** The settlement integer in `pricing.final_price_cents`
/ `price_cents`, governed by the existing `payable_definition`,
`payable_status`, and dispute window (`spec/02-envelope.md:170-174`, `schemas/post.json:48-129`). `post.pricing`
remains "the sole billing and dispute record between platform and buyer"
(`spec/02-envelope.md:278`). Nothing in this LEP changes when a lead becomes payable,
what a dispute window measures, or how an `ack` interacts with either.

**What is analytics.** `original_price_cents`, all four `fx_*` values, and
any `FINAL_PRICING` event. They are the conversion's evidence: the buyer
and the platform can both recompute `settlement_minor_exact` from them, so
a charge can always be reconstructed and a dispute argued without trusting
a log. They are **not** re-inputs: no later FX movement re-prices a
settled lead, and a `FINAL_PRICING` report — which may name a different
currency entirely — stays analytics and bid refinement only
(`spec/02-envelope.md:278-292`). Post-award FX drift is the platform's commercial
risk and MUST NOT appear as a payable adjustment without a separate,
explicitly-recorded agreement.

### 8. Valid examples

*(Validation was executed 2026-11-04 against the tree in this repository with
`jsonschema` 4.26.0 from `.venv`, resolving `core.json#/$defs/*` through a
`DRAFT202012` registry. The `pricing` block of Example 1 and of Example 2,
embedded in the shipped `examples/post.json` payload, **PASS**
`schemas/post.json` as landed. Example 3 **FAILS** `schemas/bid.json` today
with `Additional properties are not allowed ('fx_expires_at', 'fx_rate',
'fx_source', 'fx_timestamp' were unexpected)` — expected, and the reason
the bid-side schema change is a blocking item in [Tests](#tests). Example 2
is shown as a payload excerpt; it validates only in a complete post payload
(`lead_id`, `buyer_id`, `consumer`, `location`, `attributes` present).)*

#### Valid 1 — single currency, no conversion (v1.0/v1.1 shape, unchanged)

`pricing.currency` present and equal to the payload currency; no FX field
(§2 requires their absence when nothing was converted).

```json
{
  "price_cents": 2200,
  "currency": "AUD",
  "pricing": {
    "floor_price_cents": 1500,
    "premium_cents": 700,
    "final_price_cents": 2200,
    "uncapped_price_cents": 2500,
    "max_allowed_price_cents": 3000,
    "currency": "AUD",
    "price_guardrails": true,
    "is_duplicate_resub": false,
    "payable_definition": "form_lead_valid_phone",
    "payable_status": "payable"
  }
}
```

#### Valid 2 — cross-currency award, exact conversion, USD settlement

A US buyer bid `334400` AUD cents (`AUD 3,344.00`) at
`fx_rate "1.520000"`. `334400 ÷ 1.520000 = 220000` exactly, so no rounding
occurs and the charge equals the converted value; `original_price_cents`
plus the four `fx_*` values are the evidence the buyer can recompute.

```json
{
  "price_cents": 220000,
  "currency": "USD",
  "pricing": {
    "floor_price_cents": 150000,
    "premium_cents": 70000,
    "final_price_cents": 220000,
    "uncapped_price_cents": 250000,
    "max_allowed_price_cents": 300000,
    "currency": "USD",
    "original_price_cents": 334400,
    "fx_rate": "1.520000",
    "fx_source": "ecb-reference",
    "fx_timestamp": "2026-11-04T10:19:58Z",
    "fx_expires_at": "2026-11-04T10:24:58Z",
    "price_guardrails": true,
    "is_duplicate_resub": false,
    "payable_definition": "form_lead_valid_phone",
    "payable_status": "payable"
  }
}
```

#### Valid 3 — bid side, cross-currency into a USD-settling offer

`currency` is the bid's own currency (native, `AUD`); the bundle makes it
comparable with USD-settling candidates. This is the message shape
`schemas/bid.json` must accept for the feature to be reachable at all.

```json
{
  "ping_id": "550e8400-e29b-41d4-a716-446655440000",
  "decision": "accept",
  "bid_price_cents": 334400,
  "currency": "AUD",
  "estimated_contact_seconds": 45,
  "buyer_reference": "buyer-internal-987",
  "fx_rate": "1.520000",
  "fx_source": "ecb-reference",
  "fx_timestamp": "2026-11-04T10:19:58Z",
  "fx_expires_at": "2026-11-04T10:24:58Z"
}
```

### 9. Invalid examples

*(Example 1 is schema-valid and invalid only *for the auction* — the
distinction C7 turns on. Example 3 was executed against the landed schema
and is rejected; the observed message is quoted.)*

#### Invalid 1 — cross-currency bid with no rate → `fx_rate_missing`

`currency: "AUD"` against a USD-settling offer, no bundle. The schema
accepts it (`bid.json` has no cross-document condition); the auction MUST
exclude it with `fx_rate_missing`, MUST NOT treat `334400` as USD cents,
and MUST NOT assume parity.

```json
{
  "ping_id": "550e8400-e29b-41d4-a716-446655440000",
  "decision": "accept",
  "bid_price_cents": 334400,
  "currency": "AUD"
}
```

#### Invalid 2 — stale rate → `fx_rate_stale`

`fx_timestamp` is `1500 s` before the `10:20:00Z` evaluation, i.e. `5×`
the default `LCP_FX_MAX_AGE_SECONDS` of `300`. Non-comparable; the bid is
excluded, not re-priced with a newer rate the buyer never signed.

```json
{
  "ping_id": "550e8400-e29b-41d4-a716-446655440000",
  "decision": "accept",
  "bid_price_cents": 334400,
  "currency": "AUD",
  "fx_rate": "1.520000",
  "fx_source": "ecb-reference",
  "fx_timestamp": "2026-11-04T09:55:00Z",
  "fx_expires_at": "2026-11-04T10:25:00Z"
}
```

#### Invalid 3 — exponent notation → schema rejection

`"fx_rate": "1.52e0"` fails the pattern. Observed against the landed
`schemas/post.json`: `"'1.52e0' does not match '^[0-9]+(\\.[0-9]{6,})?$'"` —
mapped to `LCP-100 VALIDATION_ERROR`, `field: "fx_rate"`. (The same payload
with `"fx_rate": "1.52"` is also rejected by the landed pattern; `"1"`,
`"0"` and `"0.000123"` are **not**, and are rejected only after the §4.1
tightening.)

```json
{
  "pricing": {
    "currency": "USD",
    "original_price_cents": 334400,
    "fx_rate": "1.52e0",
    "fx_source": "ecb-reference",
    "fx_timestamp": "2026-11-04T10:19:58Z",
    "fx_expires_at": "2026-11-04T10:24:58Z"
  }
}
```

#### Invalid 4 — mixed sources → `fx_source_mixed` for every converted candidate

Two converted candidates in one evaluation (buyer A converted from AUD at
`ecb-reference`; buyer B converted from GBP at `bilateral`) plus buyer C
already bidding in USD. Rule C4: A and B are both non-comparable, each
recorded with `fx_source_mixed`; C still wins on its own integer minor
units. The platform does not quietly prefer one source, and it does not let
A and B's incompatibility disqualify C.

```json
[
  {
    "buyer_id": "buyer_aud",
    "bid_price_cents": 334400,
    "currency": "AUD",
    "fx_rate": "1.520000",
    "fx_source": "ecb-reference",
    "fx_timestamp": "2026-11-04T10:19:58Z",
    "fx_expires_at": "2026-11-04T10:24:58Z"
  },
  {
    "buyer_id": "buyer_gbp",
    "bid_price_cents": 170000,
    "currency": "GBP",
    "fx_rate": "0.790000",
    "fx_source": "bilateral",
    "fx_timestamp": "2026-11-04T10:19:58Z",
    "fx_expires_at": "2026-11-04T10:24:58Z"
  },
  {
    "buyer_id": "buyer_usd",
    "bid_price_cents": 219500,
    "currency": "USD"
  }
]
```

## Privacy, security, and universal-core audit

**PII.** No consumer data enters any field in this LEP. Classes follow
`docs/PII-CLASSES.md` (D9): `pricing.currency` is `public` ("a code, not a
commercial fact", `PII-CLASSES.md:680`), and every amount, rate, source,
and timestamp is `internal` (`PII-CLASSES.md:681-685`). The bid-side
`fx_*` fields are `internal` and need rows in that table when
`schemas/bid.json` gains them. Nothing here is eligible for a ping: the
ping allowlist is unchanged, so `LCP-008 PII_IN_PING` keeps its boundary.
No consumer identifier is joined to a rate, so aggressive FX analytics
cannot become a re-identification surface.

**Money integrity.** The rate is **self-attested**. A buyer that supplies
its own rate can shape its own converted bid, and `bilateral` rates are by
definition unverifiable. This LEP makes that visible rather than silent —
the rate, source, and timestamp travel with the price and are recorded in
the billing record — but it does not make it safe: a platform that needs
rate integrity MUST pin `fx_source` (refuse `bilateral`, name one
provider) and SHOULD reject a rate that deviates from its own reference
beyond a tolerance it defines. Neither mechanism is normative here;
both are deferred (Q-2, Q-3).

**Griefing, stated plainly.** C4's rule is mutual: a candidate cannot gain
by carrying a novel `fx_source`, because it excludes itself too — but it
can exclude a rival, and in a two-converted-candidate auction both vanish
and a same-currency bidder wins by default. The exposure is bounded (the
attacker never wins on the tricked axis) and the mitigation is the same
pinning above. It is recorded, not hidden (Q-3).

**Transport and authorization.** No new endpoint, scope, secret, or key
type. The bid is HMAC-signed over the payload, so rate tampering in flight
is already covered by `LCP-012`; replay by `LCP-005`/idempotency. The
audit row is written with the platform as actor and carries no consumer
data.

**Universal-core audit.** The fields work for: an AU publisher selling to
a US buyer (AUD native, USD settlement); a USD buyer into an AUD offer
(the reverse, same arithmetic); a JPY-settling deployment (0-exponent
minor unit, §4.2); a deployment with no FX provider at all (`bilateral`);
a deployment that pins one source (C4 with a single member); and
v1.0/v1.1 peers, which never see the fields. No field names a bank, a
provider, a jurisdiction, a tax regime, or a payment rail. The only
external dependency is ISO 4217 itself, and its minor-unit exponents are
part of the standard, not a vendor.

## Compatibility, versioning, and deprecation impact

- **Additive MINOR.** Every field is optional in both directions. A v1.2
  sender addressing a v1.1 receiver and a v1.1 sender addressing a v1.2
  receiver both keep working, in the second case with no FX metadata at
  all — which is exactly the pre-v1.2 behaviour.
- **The one real hazard is the closed `pricing` object.** It is
  `additionalProperties: false` (`schemas/post.json:129`), so a post that
  newly populates `pricing.currency` toward a v1.1 receiver is a schema
  rejection (`LCP-100`). Senders therefore MUST gate emission on the receiver's
  advertisement: `capabilities().lcp_versions` containing a parsed
  `major == 1, minor >= 2` element **and**
  `capabilities().features.multi_currency` present. This is the LEP-0003
  §4 gate verbatim, and the reference platform already advertises the key
  (`router.py:2387-2389`: `"multi_currency": "1.2.0"`) while its
  `lcp_versions` constant still lists only 1.0.0/1.1.0
  (`validation.py:23`) — a blocking gap, T-CAPGATE.
- **Bid side is the opposite case:** `schemas/bid.json:71` is also
  closed, so the fields are simply not accepted today. Nothing is
  redefined; `bid.currency` keeps its meaning (the currency of
  `bid_price_cents`) and merely stops being forced to equal the offer's.
- **`post.currency` semantics are unchanged.** It remains the payload's
  price currency and, from v1.2, the settlement currency. §1's ladder
  guarantees no existing document changes meaning.
- **No deprecation, no removal, no N+2 window needed.** Nothing in this
  LEP supersedes a field. The `spec/00-conformance.md:162` deferred row is resolved by
  this record and the roadmap row moves to implemented on ship.
- **Version string.** `lcp.version` continues to identify the core schema
  line; the capability advertisement, not the version literal, is what a
  sender gates on (D3's rule, `DECISIONS-v1.2.md` D3).

## SDK, OpenAPI, MCP, conformance, and operational impact

**Where the tree already is (measured 2026-11-04, this working tree).**

*Line pins in this section were read from the working tree on 2026-11-04; the
W1-09 implementation is landing concurrently, so the durable references are
the named symbols (`_settlement_price`, `_record_non_comparable_bid`,
`_FX_RATE_RE`) and the quoted code, not the numbers.* 
`schemas/post.json:71-94` and the ten SDK `schema-bundle.json` files plus
`sdk/python/lcp_sdk/schemas/post.json` already carry the six pricing
fields; `schemas/bid.json` does not. The reference platform already has
`LCP_FX_MAX_AGE_SECONDS` (`config.py:52-55`, `:113-114`, `:215`), the
comparability helper `_settlement_price` (`router.py:1184-1244`), the
audit writer `_record_non_comparable_bid` (`router.py:1246-1261`), the
mixed-source sweep (`router.py:1341-1358`), exact-decimal ranking
(`router.py:1359-1367`), and the half-up charge (`router.py:1388-1390`).
What follows is the delta to a conformant v1.2 surface.

| Surface | Obligation |
|---|---|
| `schemas/post.json` | Tighten `fx_rate` to §4.1; tighten `fx_source` to the §4.1 pattern; annotate all six with `x-lcp-pii` (`public` for `currency`, `internal` otherwise). |
| `schemas/bid.json` | Add the four `fx_*` fields; require all four when `currency` is present (the cross-document currency comparison stays a receiver rule). Annotate `internal`. |
| SDK bundles ×10 + `sdk/python/lcp_sdk/schemas/*` | Regenerate from the two schemas; SHA-256 manifests re-synced (`schema-sync` gate). Generated model types for the four bid fields. |
| OpenAPI | `Pricing` (`api/lcp-openapi.yaml:1171-1239`) carries the six post fields already, with the *loose* `fx_rate`/`fx_source` shapes; it must be tightened to §4.1 and its "Absent means USD" description corrected to §1's ladder. `BidPayload` (`api/lcp-openapi.yaml:1352-1397`) still lacks the four bid-side fields and the conditional requirement. |
| MCP | `submit_bid` (`implementations/mcp-server/lcp_mcp_server/server.py:306-350`) gains `fx_rate`, `fx_source`, `fx_timestamp`, `fx_expires_at`, documented as "send only when the bid currency differs from the offer's". No post-side tool exists (the platform emits posts), so nothing else changes. |
| Conformance vectors | The eight vectors in [Tests](#tests) under `test-vectors/`, plus a `sdk/schema-bundle` assertion that all ten bundles agree on the two schemas. |
| Docs | `docs/FIELD-DICTIONARY.md` rows (`:245-246`, `:267-268`, `:320-321`), `spec/03-payloads.md` §3, `spec/02-envelope.md` §4, `spec/04-errors.md` §5, `spec/05-capabilities.md` §8, a `docs/SPEC-COVERAGE.md` row group (FX-01…FX-nn), `CHANGELOG.md` under `[Unreleased]`, `docs/OPERATIONS.md` for the exclusion metric. |
| Reference platform | `LCP_FEATURE_FX_PRICING` (default off) gating intake of the four bid fields, the exclusion path, the audit row, and the post-side echo; the ISO 4217 exponent table (§4.2); `price_below_floor` for converted candidates; populate `pricing.currency`/`original_price_cents`/`fx_*` on a converted award (`messages.py:234-242` echoes none of them today); `lcp_versions` includes `1.2.0` when the feature is on. |
| Operational | One metric (`fx_excluded_total{reason}`), one audit query (`bid.non_comparable`), and an alert threshold for a spike in `fx_source_mixed` (the griefing signal). No new infrastructure, no rate service, no cache. |

## Alternatives considered

1. **Leave it deployment-specific / bilateral (no fields).** Cheap and
   zero-wire. Rejected: the auction can neither compare nor *explain*, and
   the delivered billing record cannot show what was converted. The
   current silent drop (`router.py` pre-D7 `continue`) is the status quo
   this LEP replaces with an auditable exclusion.
2. **A new `fx_quote` message type.** Rejected: `message.type` is closed
   (`spec/05-capabilities.md:32`), so every v1.0/v1.1 receiver rejects it with `LCP-006`,
   and price metadata would acquire its own lifecycle instead of travelling
   with the price it describes.
3. **Convert at the publisher and quote buyers in their own currency.**
   Rejected: it moves FX risk to the party with the least information, and
   it cannot express a multi-buyer auction — one publisher price is in one
   currency, while the buyers are in several.
4. **Store the rate as a JSON number (float).** Rejected outright: float
   money is the failure D7 forbids, `1e-7` style literals are already
   treated as a sentinel in this repository
   (`implementations/reference-platform/lcp_platform/attestation.py:201`),
   and only a string can carry declared, byte-exact precision.
5. **Express the rate as minor-per-minor.** Tempting: it removes §4.2's
   exponent factor entirely. Rejected: the number would no longer be the
   quote a human or provider supplies, so every rate would need an
   undocumented adjustment at the edge — a class of silent 100× errors
   traded for one explicit multiplication.
6. **Have the platform publish the rate and convert everything centrally.**
   Attractive (one source, verifiable, no self-attestation) but out of
   scope: it needs a rate service, an SLA, a quote surface, and a policy
   for who bears drift. Deferred as Q-2 with a real trigger.
7. **Reject cross-currency bids outright (today's effect, made explicit).**
   Rejected: it forbids the exact use case and converts a data problem into
   a hard commercial limit.
8. **Provider-signed rate attestations.** Reuses LEP-0008 machinery, but
   requires key distribution and a provider ecosystem that does not exist.
   Deferred (Q-3).

## Rollout, migration, observability, and rollback

**Rollout.** Ship the schema/SDK/OpenAPI/MCP surfaces and the vectors with
the flag **off**; enable in CI. `capabilities().features.multi_currency`
is advertised only when the behaviour is actually enabled in that build
(the advertisement is a promise, not a plan version). Senders gate on the
advertisement plus a parsed `minor >= 2` (see *Compatibility, versioning, and deprecation impact*).

**Migration.** None. Stored bids have no FX metadata, so a cross-currency
stored bid is non-comparable exactly as it is today; a same-currency stored
bid is untouched. No backfill, no rewrite, no dual-write. Posts already
delivered keep their meaning under §1's ladder. Databases need no new
columns: the FX fields live in the stored envelope, and the audit row uses
the existing audit table.

**Observability.** `fx_excluded_total{reason}` (one series per reason in
§6), `fx_converted_total`, `fx_settlement_charge_minor{currency}` for
reconciliation, and the `bid.non_comparable` audit row
(`router.py:1246-1261`) for case-level forensics. Dashboards need two
panels: exclusion rate by reason, and converted-award count versus
same-currency awards. No PII in any of them.

**Rollback.** Turn `LCP_FEATURE_FX_PRICING` off. With the flag off the
platform does not accept the bid-side fields, does not convert, does not
write `bid.non_comparable`, and does not echo FX fields on posts — i.e.
today's behaviour, including the silent skip for a cross-currency bid.
Because the post fields are optional, already-emitted posts stay valid
documents; nothing has to be un-emitted, re-computed, or back-filled. A
revert of the schema files is not required for a rollback and MUST NOT be
baked into one.

## Deferred questions and triggers

| # | Question | Trigger to revisit |
|---|---|---|
| Q-1 | Add a native-currency field (or an attested bid echo) so `original_price_cents` is self-describing on a post for a party that does not hold the winning bid | The first dispute, audit, or publisher reconciliation that cannot be resolved from the platform's own bid record |
| Q-2 | Platform-issued reference rate (platform converts, buyers quote in their own currency; removes self-attestation) | An adopter needs settlement conversion in production and cannot accept a self-attested rate; or a rate-integrity incident is reported |
| Q-3 | Rate-source policy: a registered provider list, per-auction pinned source, and/or a deviation cap against a platform reference (`LCP_FX_MAX_DEVIATION_BPS`) | Measured `fx_source_mixed` exclusions above 1% of cross-currency evaluations, a reported griefing case, or an adopter that refuses `bilateral` |
| Q-4 | Multi-source comparison with cross-rate triangulation | Two independent adopters run different pinned providers in the same market and need to trade against each other |
| Q-5 | `FINAL_PRICING` FX fields (`original_price_cents` + `fx_*`) so an external sale in another currency is comparable in analytics | A publisher reports external sales in a currency that differs from the platform's settlement currency |
| Q-6 | Refund/chargeback FX policy: which rate a reversal uses (the original rate, the refund-date rate, or the same exact-value arithmetic) | The first cross-currency refund or chargeback dispute |
| Q-7 | Ownership of the ISO 4217 minor-unit exponent table: pinned in the SPEC as a fixture, or advertised per currency in `capabilities()` | An ISO 4217 exponent change (redenomination, new currency) affects a live pair, or two conformant implementations disagree on an exponent |
| Q-8 | FX-aware `quote`/`counter` (LEP-0009 currently carries integer cents + currency only) | LEP-0009 is implemented **and** an adopter asks for cross-currency negotiation |
| Q-9 | Rounding policy variation (banker's/currency-specific) where an invoice or tax rule requires it | An invoice dispute caused by half-up; note that `docs/V1.2-EXECUTION-PLAN.md:228` mentions "banker's rounding" and D7 mandates half-up — **D7 governs**; the plan row is superseded on this point |

## Tests

Blocking: a merge that does not satisfy every row below is incomplete.
`plat:` prefix = reference-platform test, `vec:` = `test-vectors/` entry
run by the conformance runner, `sdk:` = per-SDK gate. "exists" means the
test is already in the tree (line pins read 2026-11-04) and names the gap
it must still close.

| ID | Test | Asserts |
|---|---|---|
| T-SCHEMA | `plat:test_fx_pricing_schema_accepts_and_rejects_field_shapes` | the `pricing` blocks of Examples 1–2 validate as landed, Example 3 once `schemas/bid.json` lands; `"1.52e0"`, `"1"`, `"0"`, `"007.000000"`, `"0.000123"`, `"1,52"` are rejected with `LCP-100` naming `fx_rate` |
| T-BID | `plat:test_cross_currency_bid_is_accepted_at_intake` | a bid carrying the four fields validates once `schemas/bid.json` gains them (Example 3 passes; it fails today with `additionalProperties`) |
| T-SAME | `plat:test_fx_same_currency_compares_directly` | same currency → integer minor units, no conversion, FX metadata inert (exists: `test_v12_contract.py:275`) |
| T-CONVERT | `plat:test_fx_cross_currency_valid_rate_converts_to_settlement_units` | exact decimal conversion (exists: `test_v12_contract.py:280`) |
| T-EXACT | `plat:test_ranking_uses_exact_conversion_not_rounded` | candidates at exact `2200.5` and `2200.6` rank strictly (rounding both to `2201` must not tie them) |
| T-EXPONENT | `plat:test_fx_minor_unit_exponent_conversion` | `50000` USD cents at `0.00636943` → `78499.959965…` and a charge of `78500` JPY; the unadjusted division is rejected as a failure of this test |
| T-MISSING | `plat:test_fx_missing_rate_is_non_comparable_with_reason` | excluded with `fx_rate_missing`; no charge, no post (exists: `test_v12_contract.py:287`) |
| T-STALE | `plat:test_fx_stale_rate_is_non_comparable_with_reason` | `age = max_age + 1` excluded, `age = max_age` **accepted** (inclusive boundary) (exists at `:292`, boundary half missing) |
| T-EXPIRED | `plat:test_fx_expired_rate_is_non_comparable_with_reason` | `fx_expires_at <= now` → `fx_rate_expired` (exists: `test_v12_contract.py:302`) |
| T-MALFORMED | `plat:test_fx_malformed_rate_is_non_comparable_with_reason` | `"65e-2"` → `fx_rate_malformed` (exists: `:312`) |
| T-MIXED | `plat:test_fx_mixed_sources_are_non_comparable_with_reason` | two sources → both converted candidates excluded, same-currency candidate still wins (unit-level exists at `:319`; auction-level and the "same-currency survives" half are missing) |
| T-FLOOR | `plat:test_fx_sub_floor_after_conversion_is_excluded_with_reason` | exact `149999.666…` under a `150000` floor is excluded with `price_below_floor` and produces no post |
| T-CHARGE | `plat:test_fx_settlement_charge_is_half_up_and_immutable` | `4401` @ `2.000000` → `2201`; re-running the sweep and updating the rate afterwards do not change the recorded charge |
| T-ECHO | `plat:test_post_echoes_fx_evidence_on_converted_award` | the post's `pricing` carries `currency`, `original_price_cents` and the winning bid's four `fx_*` values verbatim; a non-converted award carries none |
| T-CURRENCYMISMATCH | `plat:test_pricing_currency_must_match_payload_currency` | `pricing.currency != post.currency` → `LCP-100`, `field: "pricing.currency"` |
| T-CAPGATE | `plat:test_fx_fields_are_gated_on_v12_advertisement` | a post populating `pricing.fx_rate` toward a receiver advertising only `1.0.0`/`1.1.0` is a sender conformance failure; the v1.1 allowlist rejects it |
| T-FLAGOFF | `plat:test_fx_feature_off_restores_v11_behaviour` | with `LCP_FEATURE_FX_PRICING` off: no FX intake, no conversion, no `bid.non_comparable` row, no post echo, cross-currency bid skipped as today |
| T-AUDIT | `plat:test_non_comparable_bid_writes_audit_row` | one `bid.non_comparable` row per exclusion with `lead_id`, `offer_id`, `buyer_id`, `currency`, `reason`; no consumer fields |
| vec:fx-001 | `lcp-fx-single-currency-001` | Example 1 payload validates and a v1.0 auction outcome is unchanged |
| vec:fx-002 | `lcp-fx-cross-currency-exact-001` | Example 2 payload validates; conversion and charge recompute byte-exactly |
| vec:fx-003 | `lcp-fx-no-rate-excluded-001` | Invalid 1 → `fx_rate_missing` |
| vec:fx-004 | `lcp-fx-stale-rate-001` | Invalid 2 → `fx_rate_stale` |
| vec:fx-005 | `lcp-fx-exponent-malformed-001` | Invalid 3 → `LCP-100` |
| vec:fx-006 | `lcp-fx-mixed-source-001` | Invalid 4 → two `fx_source_mixed` exclusions, one same-currency winner |
| vec:fx-007 | `lcp-fx-rounding-deterministic-001` | the §7 table reproduces exactly (`220000`, `2201`, `78500`) across all ten SDKs' decimal handling |
| vec:fx-008 | `lcp-fx-exponent-mismatch-001` | USD→JPY conversion per §4.2, including the 100× failure being rejected |
| sdk:sync | `schema-sync` job | all ten `schema-bundle.json` files agree with `schemas/post.json` and `schemas/bid.json`; manifests re-synced |

Deferred-trigger tests are **not** written here: Q-1…Q-9 stay untested
until their triggers fire, and no test pretends they have.
