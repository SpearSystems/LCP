# v1.3 decision record (maintainer, 2026-11-19)

Scope: the decisions on LEP-0012, LEP-0013 and LEP-0014, and the release
they authorize. Each section is an adopted decision, signed by the
maintainer on the date above. This record authorizes implementation and a
release. It does not make an unverified claim true.

## D1 — Review window waiver

**Decision.** The public review windows for LEP-0012
([#254](https://github.com/SpearSystems/LCP/issues/254)), LEP-0013
([#255](https://github.com/SpearSystems/LCP/issues/255)) and LEP-0014
([#256](https://github.com/SpearSystems/LCP/issues/256)) opened on
2026-11-19 with a deadline of 2026-12-03. The maintainer closes them early,
on the day they opened. No review comment had been received.

**Rationale.** The reference deployment (SpearPointX) needs the sale
lifecycle and the `personal_loans` vertical to onboard publishers. The
maintainer is choosing to ship now rather than wait 14 days. Recording that
choice keeps the registry honest: a reader can see that no adopter review
took place.

**What stays open.**

- Any adopter may comment on the discussion issues or file a dissent under
  [`ARB.md`](../governance/ARB.md#dissent-protocol-anti-capture). A comment
  with new evidence reopens the affected record.
- Re-evaluation date: **2027-05-19**, recorded in each registry row.

## D2 — Independent review

**Decision.** No reviewer independent of the author has reviewed these
records. LEP.md requires an independent review for a material decision.
The maintainer waives that requirement for this decision, as for the v1.2
batch ([DECISIONS-v1.2 D1](DECISIONS-v1.2.md#d1--trigger-waiver-batch-of-2026-11-04)).

**Evidence relied on.** A **SELF-AUDIT** run by the authoring lane at the
release candidate commit, recorded in
[RELEASE-TICKET-1.3.0.md](RELEASE-TICKET-1.3.0.md#3-verification-evidence):
the reference platform suite, the conformance suite, every repository
check, and the SDK suites for each toolchain on the build host, plus the
full CI matrix on the pull request. Per ARB rule 2, no
non-author-controlled run covers these surfaces.

## D3 — Security and privacy sign-off

**Decision.** LEP-0012 adds an outbound HTTP surface and carries a
session-bearing URL. LEP-0013 relaxes a PII boundary. Both need a
security/privacy sign-off. The maintainer signs off for the protocol and
the reference implementation, under these constraints:

1. Every LEP-0012 surface ships **off** in the reference platform
   (`LCP_FEATURE_SALE_DISPOSITION`, `LCP_FEATURE_URL_POSTBACKS`,
   `LCP_FEATURE_TEST_OUTCOMES`, all default `off`) and is advertised in
   `capabilities().features` only when enabled.
2. The reference platform implements only the fail-closed side of
   LEP-0013. It refuses every regulated value with `LCP-100` and never
   advertises `features.regulated_identifiers`.
3. **Condition on deployments.** A deployment that enables the LEP-0013
   lane MUST first record a sign-off from a **named human**
   security/privacy reviewer for that deployment (LEP-0013 rollout step 2).
   This record does not certify legality or privacy fitness for any
   deployment.

## D4 — Decisions

- **LEP-0012 Accepted.** Sale lifecycle: `ack.disposition`, money-bearing
  `details` on `ACCEPTED`/`REJECTED`/`REFUNDED`, `url_template` postbacks,
  and the `lcp.affiliate.tracking` and `lcp.test.outcome` extensions.
- **LEP-0013 Accepted**, under D3.
- **LEP-0014 Accepted.** `personal_loans` 2.0.0, including the
  `us_details` container that depends on LEP-0013.

Implementation: PR [#249](https://github.com/SpearSystems/LCP/pull/249).

LEP-0015 and LEP-0016 are not decided here. They stay in Review.

## Release consequence

`v1.3.0` is authorized as an additive MINOR over `v1.2.0` containing D4.
No envelope field is made required, no enum value is removed, and no error
code is added. A 1.0, 1.1 or 1.2 peer sees no changed byte unless it reads
an advertisement and opts in.
