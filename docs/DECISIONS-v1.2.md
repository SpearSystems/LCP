# v1.2 decision record (maintainer, 2026-11-04)

Scope: the decisions the [v1.2 execution plan](V1.2-EXECUTION-PLAN.md) calls
M0. Each row is an adopted decision, signed by the maintainer on the date
above. They authorize implementation; they do not tag a release, and they do
not make an unverified claim true.

## D1 — Trigger waiver (batch of 2026-11-04)

**Decision.** The v1.1 trigger gates are waived for the items enumerated in the
execution plan. The triggers themselves were **not met**; `LEP-0001` and
`LEP-0007` were Deferred on that basis on 2026-11-03, and this record supersedes
those Deferrals by maintainer decision, not by new adopter evidence.

**Compensating constraints.** Every item built under this waiver MUST be:

1. additive-only on the wire, advertised through `capabilities()`, and
   unreachable for a peer that does not read the advertisement;
2. disabled by default in the reference platform (`LCP_FEATURE_*` off), while
   remaining exercised by CI, vectors, and the conformance suite;
3. accompanied by the deterministic evidence that would have stood in for the
   missing adopter signal (benchmark, vector, or fixture), and a dated
   re-evaluation entry (`2027-05-04`) in the registry row.

**Rationale.** The maintainer is choosing to fund the work now. Recording that
choice as a waiver keeps the registry honest: a reader can see that no adopter
evidence was produced, and can challenge the decision at the re-evaluation
date.

## D2 — `PROCESSED` acknowledgement

**Decision.** Add `PROCESSED` to the `ack.status` enum and to the lifecycle as
a **terminal-success** transition `ACCEPTED → PROCESSED`.

**Rules.**

- Valid only on an ack for a `post` delivery, and only by the buyer that
  received that post.
- Idempotent: repeating `PROCESSED` for the same delivery returns the original
  acknowledgement.
- It never re-opens a payable, never changes `pricing`, and never becomes a
  precondition for `SOLD`. Payability remains governed by `pricing` and the
  existing dispute window.
- `PROCESSED` after `REJECTED`, `EXPIRED`, or `SOLD` is a transition error.

**Rationale.** The finding is "buyer CRM synchronization failures need
detection". A transport-level ack is not a CRM-level one; this adds the CRM
signal without touching money semantics.

## D3 — `core_schema_version`

**Decision.** Resolve by **documentation plus capability advertisement**, not
by a new envelope field.

- SPEC §2 states that `lcp.version` *is* the core schema version for this
  major line, and names the compatibility rule (a higher minor on the receiver
  never invalidates a lower-minor sender).
- `capabilities()` gains `core_schema_version`, set to the package
  `SDK_VERSION`.

**Rationale.** An envelope field that no sender is obliged to populate would
be decoration. The finding's real need — detecting a schema-level break — is
served by the receiver stating what it validates against.

## D4 — `street_address`

**Decision.** Add `location.street_address` as an optional string,
`ping_safe: false`, with the same scrub treatment as other address parts.

**Rationale.** Multiple verticals need the consumer's residential address; a
core field with a ping prohibition is smaller than six vertical copies.

## D5 — Raw IP address

**Decision.** Add `provenance.ip_address` (IPv4 or IPv6, format-validated),
**post-only** (`ping_safe: false`). `provenance.ip_hash` remains the ping-safe
form and is unchanged.

**Rules.** When both are present the platform MUST reject a mismatch that it can
detect only by hashing the raw value with the same key; when only the raw value
is present, the hash is derived for ping emission. Raw values obey the storage
and erasure rules in `docs/PRIVACY-DATA-GOVERNANCE.md`.

## D6 — Raw form submission

**Decision.** Register `{org}.intake.raw_form` as a post-only extension:
bounded array of verbatim question/answer pairs, total block ≤ 8 KiB, never
`ping_safe`, classed `restricted` under D9.

**Rationale.** Dispute evidence has no safe home in the core; the LEP-0006
extension machinery already enforces the container bound and PII policy.

## D7 — Multi-currency auction semantics

**Decision.** Extend `post.pricing` (and the bid-side fields that feed it) with:

| Field | Type | Rule |
|---|---|---|
| `currency` | ISO 4217 alpha-3 | Settlement currency; absent means `USD` for compatibility |
| `original_price_cents` | integer | The buyer's native minor unit price |
| `fx_rate` | decimal string | Native per settlement unit, ≥ 6 significant digits, no exponent |
| `fx_source` | string | Named rate provider or `bilateral` |
| `fx_timestamp` | RFC 3339 UTC | Age MUST be ≤ `LCP_FX_MAX_AGE_SECONDS` (default 300) |
| `fx_expires_at` | RFC 3339 UTC | Absolute validity end |

**Auction rule.** Two prices MAY be compared only when their settlement
currency matches, or when both carry a rate that is present, non-stale at
comparison time, and sourced consistently. Missing, stale, malformed, or
mixed-source rates make the offer **non-comparable**: it is excluded from the
auction with an explainable reason, never silently coerced. Ranking uses
integer minor units in settlement currency; rounding is half-up at final
presentation only, and the rounded value MUST NOT feed the auction.

**Rationale.** Never compare raw minor units across currencies. Excluding with
an explanation is reversible and auditable; guessing a rate is not.

## D8 — Agent-to-agent negotiation

**Decision.** Add `quote` and `counter` to `message.type` as the negotiation
surface (LEP-0009), capability-gated and off by default.

**Rules.**

- Ping-safe: integer cents, currency, `expires_at`, `max_rounds`,
  `negotiation_id`. No consumer PII.
- Bounded: default 3 rounds, hard cap 5; each counter MUST be monotone in the
  platform's favor relative to the party it answers.
- A `quote`/`counter` below the ping's floor is refused with an explicit
  reason, never clamped.
- Accepting a counter yields a single-use binding token that stands in for a
  bid; replay returns the original outcome.
- Expiry is absolute and cannot exceed the ping's TTL.

## D9 — PII sensitivity taxonomy

**Decision.** Adopt `docs/PII-CLASSES.md` with five classes —
`public`, `internal`, `contact`, `restricted`, `prohibited` — and annotate
**every** schema property with `x-lcp-pii`.

**Rules.**

- Ping emission allows `public`, `internal`, and hash-only projections of
  `contact`. `restricted` and `prohibited` never appear in a ping.
- A field's class may never be downgraded by a vertical, extension, or
  deployment. The validator refuses a downgrade at load time.
- `prohibited` names values LCP will not carry at all (government identifiers,
  full payment credentials, biometric templates); the refusal is a validator
  error naming the field, not a scrub.

## D10 — SPEC split

**Decision.** Split `SPEC.md` (1303 lines, above the documented ~1000-line
threshold) into `spec/00-conformance.md`, `01-transport.md`,
`02-envelope.md`, `03-payloads.md`, `04-errors.md`,
`05-capabilities.md`, plus `SPEC.md` as the normative index.

**Rules.** Section numbering is preserved in the filenames' order; every
inbound anchor (`SPEC.md#…`) resolves via an anchor stub; the citation checker
runs `--strict` over the split.

## D11 — Hosted conformance

**Decision.** Ship a self-hostable conformance runner (container image) and an
optional hosted deployment. No certification or trademark claim is made from
the hosted instance until a second independent operator runs the same image
and publishes a matching report.

## D12 — Second maintainer

**Decision.** Prepare, do not claim. `CODEOWNERS` and the exact ruleset change
(1 required approval, `prevent_self_review: true`) are recorded in
`docs/MAINTAINER-RELEASE-SETUP.md`; the applied-state section continues to say
that independent release approval does not exist while the maintainer is the
only human with release duties.

## Release consequence

`v1.2.0` is authorized as a MINOR release containing D2–D11. `v1.3.0` is
reserved for the D8 wire surface if its implementation is not complete when
`v1.2.0` ships; the LEP record, not the release note, decides that.
