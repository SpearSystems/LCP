# v1.4 decision record (maintainer, 2026-11-19)

Scope: the decisions on LEP-0015 and LEP-0016, and the release they
authorize. Each section is an adopted decision, signed by the maintainer on
the date above. This record authorizes implementation and a release. It
does not make an unverified claim true.

## D1 — Review window waiver

**Decision.** The public review windows for LEP-0015
([#257](https://github.com/SpearSystems/LCP/issues/257)) and LEP-0016
([#258](https://github.com/SpearSystems/LCP/issues/258)) opened on
2026-11-19 with a deadline of 2026-12-03. The maintainer closes them early,
on the day they opened. No review comment had been received.

**Rationale.** The reference deployment (SpearPointX) needs a declarative
outbound adapter for its first non-LCP buyer, and a `mortgage` vertical
whose amounts follow the same whole-unit rule as `personal_loans` 2.0.0.
The maintainer is choosing to ship now rather than wait 14 days. Recording
that choice keeps the registry honest: a reader can see that no adopter
review took place.

**What stays open.**

- Any adopter may comment on the discussion issues or file a dissent under
  [`ARB.md`](../governance/ARB.md#dissent-protocol-anti-capture). A comment
  with new evidence reopens the affected record.
- Re-evaluation date: **2027-05-19**, recorded in each registry row.

## D2 — Independent review

**Decision.** No reviewer independent of the author has reviewed these
records. LEP.md requires an independent review for a material decision.
The maintainer waives that requirement for this decision, as for v1.3
([DECISIONS-v1.3 D2](DECISIONS-v1.3.md#d2--independent-review)).

**Evidence relied on.** A **SELF-AUDIT** run by the authoring lane at the
release candidate commit, recorded in
[RELEASE-TICKET-1.4.0.md](RELEASE-TICKET-1.4.0.md#3-verification-evidence):
the reference platform suite (including the `BMAP` render, decode and
refusal vectors run against the reference executor), the conformance
suite, every repository check, and the SDK suites for each toolchain on the
build host, plus the full CI matrix on the pull request. Per ARB rule 2, no
non-author-controlled run covers these surfaces.

## D3 — Security and privacy sign-off

**Decision.** LEP-0015 adds egress to arbitrary buyer hosts, forwards
`restricted` data in a body the platform renders, and reads buyer
responses. LEP-0016 changes no PII class. LEP-0015 needs a security/privacy
sign-off. The maintainer signs off for the protocol and the reference
implementation, under these constraints:

1. The reference executor ships **off** (`LCP_FEATURE_BUYER_MAPPING`,
   default `off`). Off, every buyer receives the LCP envelope at
   `webhook_url` and no response body is read, exactly as in v1.3.
2. The mapping format is not executable: no expressions or templates, a
   closed transform set and a closed rule language. URLs are static `https`
   under the existing SSRF and no-redirect rules, lead data never rides a
   URL or a header, and secrets are resolved by reference at send time and
   never stored, logged or audited.
3. The reference platform refuses every `x-lcp-regulated` path in a mapping
   at upsert and again at send, because it enrols no buyer under LEP-0013.
   Response bodies, buyer free text and redirect URLs are never stored.
4. **Condition on deployments.** A deployment that turns on
   `LCP_FEATURE_BUYER_MAPPING`, or any other executor of buyer mappings,
   MUST first record a sign-off from a **named human** security/privacy
   reviewer for that deployment, covering its buyer hosts, the secrets it
   mounts and the restricted fields its mappings read. This record does not
   certify legality or privacy fitness for any deployment.

## D4 — Decisions

- **LEP-0015 Accepted**, under D3. Buyer delivery mapping documents
  (`schemas/buyer-mapping.json`, `format_version` `1.0`), the native 1.3.0
  buyer ack read, one bounded buyer-leg counter re-post, the `BMAP`
  vectors, and `buyer_unconfirmed` as a known `rejection_reason`.
- **LEP-0016 Accepted.** `mortgage` 2.0.0 with `amount_currency` and
  bounded whole-unit amounts, `mortgage` 1.0.0 frozen under
  `verticals/archive/`, and the normative side-by-side rule: a receiver
  validates each message against the `attributes.schema_version` it names,
  and refuses an unserved version with `LCP-100` on that field.

Implementation: PR [#251](https://github.com/SpearSystems/LCP/pull/251).

**Scope notes recorded with the decision.**

- LEP-0016's "SDK models for both versions": no SDK has per-vertical typed
  attribute builders (attributes are untyped JSON in all ten SDKs, as for
  `personal_loans` 2.0.0). The SDKs ship both schemas in their bundles,
  validate by `attributes.schema_version`, and the archived 1.0.0 schema
  carries `deprecated: true`. A typed builder is a follow-up, not part of
  this decision.
- LEP-0016's per-sender 1.0.0 usage metric and optional normalisation are
  operational MAYs and are not implemented in the reference platform.

## Release consequence

`v1.4.0` is authorized as an additive MINOR over `v1.3.0` containing D4.
No envelope field is made required, no enum value is removed, and no error
code is added. A sender of `mortgage` 1.0.0 is unchanged for the 180-day
window. A buyer without a mapping or a declared `lcp_versions` 1.3.0 sees
no changed byte.
