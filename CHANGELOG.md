# Changelog

All notable changes to LCP (Lead Context Protocol) are documented in this
file. The format is based on [Keep a Changelog](https://keepachangelog.com/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.1.0] — 2026-11-03

Additive MINOR: canonical timestamp generation with a blocking cross-SDK
signature-interop gate; shared-lead slot allocation with capability-gated
slot fields and contact-hash exchange; request-history read surface;
marketplace FINAL_PRICING feedback plus lead/post `extensions` and
`{org}.form.questions`; verified agent attestation (L3) with
issuer-anchored JWKS; the ARB review process; and the SPEC §5/§9
conformance restorations below. No breaking wire change.

### Fixed

- **Reference platform (#176, #181, #182):** the three §5 intake error
  codings that had no emission path are restored on the wire.
  `Platform._validate_envelope` now re-codes the consumer phone axis — a
  non-E.164 `consumer.phone` / `consumer.secondary_phone` string is rejected
  `LCP-009 INVALID_PHONE` (400) at the documented #131 ordering point, ahead
  of schema validation; the published schema pattern is untouched, non-string
  values and every other axis keep `LCP-100`, and the corpus row L2-011
  already named this expectation (`"error_code": "LCP-009"`). Version
  negotiation lands on the same envelope boundary: a well-formed
  `lcp.version` outside the supported set is rejected `LCP-007
  SCHEMA_VERSION_UNSUPPORTED` (422) instead of silently ACKed and processed
  as v1 — the supported set is `validation.SUPPORTED_LCP_VERSIONS`, the one
  constant the §8 capabilities advertisement serves, so intake and
  advertisement cannot drift; a malformed version keeps the schema's
  `LCP-100` pattern axis. And territory: a pattern-valid but ISO 3166-1
  unassigned `location.country_code` (`"ZZ"`) is rejected `LCP-010
  INVALID_TERRITORY` (422) instead of silently ACKing into a no-offer match.
  Ruling: §3 mandates no intake-side country normalization, so lowercase
  `"au"` stays the schema pattern's `LCP-100` violation (messy sources
  normalize through the mapping `upper` transform, `test_new_flows.py`).
  Pinned by `tests/test_gap_intake_error_codes.py` (proposed matrix flips:
  ERR-007 / ERR-009 / ERR-010; dispositions live in the PR body — the
  matrix file stays locked).
- **Reference platform (#142, fixes #142):** a wrong bearer API key for a
  recognized sender now fails with `LCP-002 UNKNOWN_SENDER` instead of
  `LCP-012` — §5 confines LCP-012 to the HMAC signature/timestamp axis, and
  the bearer branch is identity (ruling in `docs/err-auth-triage.md`). The
  bearer branch previously had zero coverage: its test was shadowed by the
  `service.py` idempotency pre-check, so `test_bearer_with_wrong_api_key_is_rejected`
  now sends the matching fresh `X-LCP-Idempotency-Key` and asserts code and
  message together. HMAC timestamp/signature failures keep emitting LCP-012.
- **Reference platform (#131, fixes #131):** an envelope whose
  `lcp.message.type` is outside the closed §6 enum is rejected on the wire
  with `LCP-006 UNKNOWN_MESSAGE_TYPE` (was the generic `LCP-100` catch-all,
  contrary to the §5 taxonomy). A structural check in `_validate_envelope`
  re-codes exactly that one axis ahead of schema validation; every other
  validation failure keeps `LCP-100`, and conformance behavior for
  protocol-known types is untouched.
- **Python SDK (#136, fixes #136):** `LCPHTTPError` no longer embeds the
  full response body in `str()` — error objects reach logs and tracebacks,
  and bodies can carry PII in error messages or be arbitrarily large. The
  error string now carries status plus request id only (matching the
  TypeScript `LcpHttpError` and Go `HTTPError`, which already do this), and
  the body stays available on the bounded `.body` attribute (truncated past
  `MAX_BODY_CHARS` = 2048 for operator debugging). Non-trivial logic pinned
  by `HTTPErrorHygieneTests` in `tests/test_sdk.py`, including a live-server
  round-trip.

### LEP-0003 wire surface (v1.1): shared-lead slot fields and contacted-hash dedup exchange

Schemas + wire artifacts only — no platform logic (the enforcement set,
`CONTACTED` allow-list/advertisement, and slot-accounting tests land with
the platform lane; #153 T2/T3/T4/T6). Target release: v1.1.0 (additive
MINOR; `SDK_VERSION` stays 1.0.2 until coordinated release prep per
`docs/RELEASE.md` — capability negotiation in SPEC.md §8 is the switch).

- **Schemas:** `schemas/ping.json` gains optional `slots_available`
  (integer, `minimum: 1`) on the strict allowlist (allowlist semantics
  and `LCP-008` PII protection preserved; non-PII count) plus a
  top-level conditional — `exclusivity: "exclusive"` present ⇒
  `slots_available` const `1` (LEP-0003 invalid example B, dossier F8 /
  #153 T7). `schemas/bid.json` gains `slots_claimed` (`minimum: 1,
  maximum: 1` — multi-slot agency delivery deferred, F4/T5).
  `schemas/post.json` gains `contacted_hashes` (array of
  `^[a-f0-9]{64}$`, `maxItems: 100`, no `uniqueItems`; absent-vs-`[]`
  distinct). The bound is 100 because the record leaves the post-side
  array bound as a Deferred question; the rationale and the bulk-sync
  trigger are recorded in the schema `$comment`. `core.json`
  `$defs/exclusivity` unchanged.
- **SPEC.md:** §3 slot semantics (budget counts distinct awarded buyers,
  copies are never the budget, undeclared `shared` ⇒ 1, never oversell,
  never ping zero); §4 ping/bid/post field rows with the capability-gate
  rule and `CONTACTED` reporting guidance (32-entry/64-hex ingest MUST,
  at-least-once + `message.id` dedupe, digest-set union, absent-vs-`[]`);
  §6 closed-vs-open versioning clarification; §8 version-string
  membership normative text (full-semver elements, parsed
  `major == 1, minor >= m` comparison, hosting obligation — LEP-0003
  F6); §9 contact-hash preimage normalization (phone = verbatim wire
  bytes; email = proposed canonical, entries gated behind the pin plus
  #153 T9; never unsalted digests).
- **Examples:** `examples/ping-slots.json`, `examples/bid-slots.json`,
  `examples/post-contacted.json`, `examples/event-contacted.json` — the
  record's four valid v1.1 examples, validated by the repository's own
  conformance runner.
- **Test vectors:** L3-008–L3-022 — valid slot ping/bid/post/CONTACTED,
  absent-vs-`[]`, exclusive-one-slot accept case, and the record's
  invalid shapes (40-hex SHA-1 A, 63-hex RF-1 class, >100 bound,
  `slots_available: 0`, `slots_claimed: 2`, exclusive-multi-slot,
  slot-ping-to-v1.0-receiver capability-gate negative, 33-entry and `[]`
  report negatives). `test-vectors/sdk/validation-corpus.json` gains
  sdk-015–sdk-023 + `lcp-neg-exclusive-multislot-001`. New shared
  negative suite `test-vectors/sdk/negative/slot-fields.json` (with an
  accept control). New `test-vectors/sdk/contact-hash.json`: pinned
  preimage→digest pairs (phone and email rows recomputed byte-exact from
  `pair-secret-for-vectors-0123456789`) so SDK/platform hash builders
  cannot drift (LEP-0003 §3 proposed the name `dedup-hash.json`; this
  lane lands the identical fixture form under `contact-hash.json`).
- **Conformance runner:** `test-vectors/conformance.py` now HONORS each
  vector's `checks` array (#153 routed observation O3 — labels were
  decorative): every named check has an implementation, unknown names
  fail loudly, negative vectors pass only when their specific named rule
  fires. New checks implement the §8 capability gate (parsed-semver
  membership), slot minimum/conditional/maximum, contacted-hash shape
  and bounds, and report-direction rules. All 36 v1.0 vectors pass
  unchanged (headline regression proof); suite total 51.
- **SDK artifacts:** all ten SDK schema bundles + manifests regenerated
  (`tools/generate_sdk_models.py --write`);
  `check_sdk_schema_sync --check` and `check_reserved_fields` green.
- **OpenAPI:** `Capabilities.lcp_versions` required with full-semver
  item pattern and the membership rule (F6b); `BidPayload.slots_claimed`;
  `CONTACTED` documented on the event value list.
- **MCP:** `submit_bid` tool gains `slots_claimed` argument and
  pass-through; `_validated_envelope` behavior unchanged (the refreshed
  schemas flow through it automatically).
- **Docs:** `docs/SPEC-COVERAGE.md` gains SLOT-era rows (MSG-PING-04,
  MSG-BID-03, MSG-POST-04, MSG-EVENT-04, CAP-07, SEC-14; CNF-03 gap→green
  with ids); `docs/FIELD-DICTIONARY.md` gains the three field rows and
  `CONTACTED` on the event list.

### CI and tooling

- Logging-hygiene lint (`tools/check_logging_hygiene.py`, wired into the
  Test workflow): platform/SDK code under `implementations/` must not
  print to stdout or console-dump serialized payloads; operational
  messages use the documented `file=sys.stderr` allowance (reference
  platform startup and delivery-worker messages moved accordingly);
  operator CLIs (`admin.py`, `tools/`, `test-vectors/`) keep stdout by
  design; every `examples/integrations/` file now carries an explicit
  `example — not for production` header.
- Committed-secrets scan (`committed-secrets` job in the Security
  workflow): a pinned, SHA-256-verified Trivy binary (v0.76.1) runs
  `fs --scanners secret` with the repo's `trivy-secret-rules.yaml`
  custom rules over the checked-out tree of every push and pull
  request, so placeholder files like `kubernetes/secret.example.yaml`
  pass while real credentials (including values pasted over the
  `REPLACE_ME` sentinels) fail.

### LEP-0003: shared-lead slot accounting and the CONTACTED exchange (reference platform)

- **Slot accounting enforcement (LEP-0003 §5, blocking tests T2/T3/T4):**
  awards now flow through a single write-time decision point
  (`Store.try_award_post`) that counts **distinct awarded buyers** (the §1
  normative unit; `exclusive` = one slot, undeclared `shared` = 1, copies
  never consume the budget) and writes the post in one transaction -
  a transaction-scoped advisory lock (`pg_advisory_xact_lock`) per lead
  under Postgres (the §5-sanctioned mechanism; the earlier
  `SELECT ... FOR UPDATE` on the lead row deadlocked against concurrent
  schema-bootstrap DDL under the 4-worker delivery benchmark - CI run
  41561553846 - and the award path now takes no table lock beyond the
  delivery write itself, asserted by `test_award_path_holds_no_ddl_or_lead_locks`),
  BEGIN IMMEDIATE under SQLite. A partial unique index on `(lead_id,
  offer_id) WHERE kind = 'post'` makes double awards impossible at the
  storage layer (the
  advisory `has_delivery_for_offer` read-then-insert pair is removed);
  the auction-close sweep claims expired pings exactly once
  (`claim_expired_open_pings`, `FOR UPDATE SKIP LOCKED` on Postgres); the
  candidate filter refuses bids that arrived at/after a ping's expiry
  boundary or rode erased/rejected pings. Named gates:
  `test_slot_budget_never_oversells_under_concurrent_award`,
  `test_slot_budget_two_process_postgres_race` (mirrors the ARB dossier
  harness; runs in CI against the Test job's `postgres:16` service via
  `LCP_TEST_POSTGRES_URL`), `test_second_post_for_same_lead_offer_is_refused`,
  `test_max_buyers_bounds_distinct_buyers`.
- **CONTACTED reporting (T6/T8):** a delivered buyer with scoped
  `event:submit` credentials can report `CONTACTED` (previously refused by
  the closed `_BUYER_EVENT_TYPES`); the event is advertised in
  `capabilities().events`, replay dedupes by the event `message.id`
  (exact re-sends collapse to a `DUPLICATE` ack), and retried reports
  merge by digest **set** (§4 at-least-once semantics,
  `test_contacted_report_replay_is_idempotent`). Ingest enforces the §4
  MUSTs - entries must match `^[a-f0-9]{64}$`, 1-32 per report, `[]`
  refused - as validation rejections, not stores. Reported digests are
  stored per lead (`contacted_parties`, erasure-integrated) and re-keyed
  onto later posts **only from plaintext the platform delivered** (phone
  only, verbatim preimage; email-form entries stay behind the SPEC §9 pin
  + T9 vectors).
- **Capability-gated v1.1 fields:** `slots_available` and
  `contacted_hashes` are emitted only to receivers whose credential
  record advertises a full-semver `lcp_versions` element with
  `major == 1, minor >= 1` (parsed comparison, never string literal or
  prefix - F6/T10 rule), with envelope version `1.1.0`; non-advertising
  receivers keep bit-for-bit v1.0 bytes. The platform never pings a
  zero-remaining lead and never advertises `slots_available: 0`.
  `capabilities().lcp_versions` now advertises `["1.0.0", "1.1.0"]`.
- **Wire-example recurrence gate (T1/RF-1):**
  `tools/tests/test_lep_wire_examples.py` validates every fenced
  `### Valid example` block in `governance/LEPs/*.md` through the
  platform's own `SchemaValidator` and asserts the `contacted_hashes`
  digest **shape** (length 64 included) directly - with a planted-bug
  self-test proving a 63-hex value fails.

### LEP-0002: canonical timestamp generation and cross-SDK interop

- **SDKs (Java/Kotlin/Rust):** envelope default-timestamp and HMAC
  `X-LCP-Timestamp` generators switch to the canonical whole-second UTC
  wire form (`YYYY-MM-DDTHH:MM:SSZ`) via `DateTimeFormatter` with
  `ZoneOffset.UTC` (Java/Kotlin) and `OffsetDateTime::replace_nanosecond(0)?`
  before `Rfc3339` (Rust). The other seven SDKs already emitted this form;
  no code change. Each of the three updated SDKs gains a `CanonicalTimestamp*`
  frozen-clock unit test pinning `.123456789 ns → 2026-08-15T10:20:00Z`;
  the interop probes route through the SDK's own generation expression so
  the matrix reflects shipped behavior.
- **SPEC.md §9:** canonical-generation normative paragraph — signers MUST
  emit the whole-second UTC form; HMAC covers the exact transmitted
  timestamp string; verifiers MUST recompute verbatim, never reformat;
  receiver parse leniency is unchanged.
- **Interop blocking flip:** `tools/sdk_interop_matrix.py`
  `KNOWN_FRACTIONAL_SDKS` is now the empty set and any generation-shape
  FAIL exits 1 (was advisory). The `SDK signature interop` workflow
  (`.github/workflows/sdk-interop.yml`) turns red on any shape regression
  and keeps its weekly refresh schedule. `tools/tests/test_sdk_interop_matrix.py`
  carries the #152 truthfulness drift guard: the LEP-0002 evidence
  table is a superseded historical record, and the parser requires the
  `superseded by implementation PR` caption whenever the baseline is
  empty — a silent caption deletion or a reverted-Java fractional
  expectation turns the suite red.
- **Governance:** LEP-0002 wording fixes carried from the ARB decision
  (F-1 outbound webhook signer, F-2 Swift accepts `+00:00`, F-3 HMAC-SHA256
  mirror in the deferred-Q3 security block) and the Implementation/
  Verification links landed in the proposal; the registry's LEP-0002
  traceability cell gains the implementation PR link.
- See [`governance/LEPs/LEP-0002-canonical-timestamp-and-signing-interop.md`](governance/LEPs/LEP-0002-canonical-timestamp-and-signing-interop.md).

### Security and hardening

- Rust SDK: bump `h2` 0.4.15 → 0.4.16 (RUSTSEC-2026-0258, low-severity
  unbounded empty DATA frames DoS). Folded into v1.1 — no patch release
  for the published 1.0.2 crate; the fix ships with the next release.

### Coverage grind (matrix wave 2026-11-03)

- 34 SPEC-clause rows moved gap→green by executed tests across errors/
  capabilities, enum + provenance passthroughs, message lifecycle
  (scrub tri-state, dispute resolution edges, rejection_reason, full §8
  offer-restriction battery), sandbox both-direction enforcement, per-
  sender rate limiting with Retry-After, MCP discovery/passthrough, and
  idempotency-key header/envelope equality at both enforcement points.
- New `tools/check_universal_core.py`: the mechanical consumer for the
  SPEC §12 universal-core audit (14 checks, incl. demographics-absent
  and metrics-absent pins); wired into the Test workflow's tooling job.
- Test-only additions: `test_gap_errors_caps`, `test_gap_enum_message`
  (+ `test-vectors/sdk/enum-passthrough-corpus.json`), `test_gap_mcp_surface`,
  `test_gap_compliance_lifecycle_offers`, `test_gap_security` (+ SDK
  `test_security_noncorrelation`).
- Discovered spec-vs-code defects filed (wire-visible, so fixed via
  issues not test patches): #176, #177, #179, #180, #181, #182, #192,
  #193. Coverage matrix truth: 127 green / 8 gap / 9 exempt of 144.

### Fixed

- Reference platform: `capabilities()` advertises `conformance_level: "L2"`
  instead of `"L3"`. SPEC §7 defines L3 as agent binding (attestation
  *verification*); the platform validates the attestation's shape only, so
  the L3 claim was false while forged and free-text attestations ingested
  (#192). This is Phase 0 of the accepted LEP-0008 record; L3 is
  re-advertised only when enforce-mode verification ships with its V13–V24
  gates. Regression pin: `plat:test_conformance_level_is_l2_until_attestation_verification_is_live`.

### Added

- **Agent attestation verification (LEP-0008, accepted 2026-11-03).** The
  reference platform now verifies `provenance.agent.attestation` end to end:
  issuer-published JWKS resolution over the paired egress controls, the
  13-step §5 procedure (decode-time determinism, exact algorithm allowlist,
  claim binding, P1363-only ES256, kid-norm A), `observe`/`enforce` modes with
  `enforce` the default, and a frozen audit record that never stores token
  bytes (PR #213). SPEC §9 gains the normative token-form, claim-set,
  verification-order, replay/skew, failure-mapping and advertisement-honesty
  text; `schemas/core.json` pins the compact-JWS shape (`≤ 8192` chars, three
  non-empty base64url segments, no padding) with corpus vectors sdk-024–028
  (SEC-15). `conformance_level` now follows the enforced mode: `L3` in
  `enforce`, `L2` in `observe` — closing the false-L3 claim reported in #192.
- **Request-history log lookup (LEP-0005, accepted 2026-11-03).**
  `GET /v1/lcp/history/requests` and the MCP `get_request_history` tool: a
  paginated, filtered read surface over the platform's own audit tables
  (metadata only — no raw envelope bodies, no plaintext contacts). Rows are
  projected through a single tenant decision point
  (`lcp_platform/history.py`), contact fields appear only as the requester's
  per-pair HMAC or are absent, contact-shaped `lead_id` values are omitted at
  emission, `ack` is a legal filter that matches no stored row, and erased
  leads persist as `ERASED` rows that no keyed-hash filter can match. The row
  shape ships as `schemas/history-request-row.json` (a read-surface schema,
  excluded from the SDK validation bundles by design). Python SDK gains
  `LCPClient.request_with_headers()` (additive) so the MCP adapter can surface
  pagination values inside the tool result as the record requires.
### Fixed

- **`pricing.is_duplicate_resub` is now computed (SPEC §4, issue #177).**
  Posts carry the flag: `true` only when this buyer has already been
  delivered this lead (delivery-side prior-contact dedup, keyed on the
  lead + buyer pair); a second buyer on a shared lead is explicitly not a
  duplicate resubmission. Maintainer ruling A recorded on #177.

### Changed

- **Spec-coverage matrix is now a blocking gate.** The gap backlog reached
  zero (139 green / 0 gap / 11 exempt of 150 clauses): every former gap is
  either a shipped test, a fixed-and-verified issue, or an explicit exempt
  decision. `--fail-on-gap` runs in CI, so a new untested clause fails the
  build instead of silently reopening the ledger. LEP-0006's D7 question is
  decided (platform-side non-contact PII detection stays MAY; the publisher
  MUST governs; `SEC-17` records the exemption) and the delivery
  retry/dead-letter semantics are documented in the operations guide with
  the delivered-post acknowledgement deadline recorded as a v1.2 design item
  (#193).
- **Documentation is vertical-neutral.** The attachment capability (upload,
  HMAC raw-body signing, storage, scanning, retention/erasure) moved out of a
  vertical-branded page into [Secure attachments](docs/ATTACHMENTS.md), and
  the vertical-schema rules plus the shipped `verticals/` examples live in
  [Vertical schemas](docs/VERTICALS.md). Capability examples in the
  calls/quotas/onboarding guides no longer carry a vertical's name or ids. No
  wire, schema, or vertical-id change.

### Governance and verification (ARB wave 0–1.5)

- Autonomous Review Board charter (`governance/ARB.md`), dossier template,
  and LEP-0004 (Draft; agent-audited independent review replacing the
  second-maintainer labor while keeping maintainer sign-off) — issue #128.
- CLA workflow now exempts bot-authored PRs (#103) — human contributors
  keep the body-checkbox requirement.
- Dependabot groups `codeql-action` and `docker/*` bumps into single PRs
  (#106), eliminating the transient CodeQL lockstep failures.
- Negative security-vector suite (`test-vectors/sdk/negative/`, #132):
  14 vectors across invariants 1/2/5/6 of the SDK security-invariant list,
  with format spec, loud gap-skip policy, and runners for all 10 SDKs
  (Python, Java, Kotlin, PHP, TypeScript, Go, Ruby, C#, Rust, Swift — #154;
  the Swift verifier has no test/production mode state, so `production_mode_verify`
  stays a loud gap-skip there too).
- Conformance-level planted negatives for the last three owed ledger rows
  (`test-vectors/l2-lifecycle.json`, #154): L2-013/014 (location must carry
  state_region OR postal_code), L2-015/016 (OTP plaintext code rejected;
  hash+timestamp accepted), L2-017/018 (token inside ping `compliance_flags`
  rejected by a new `check_compliance_flags` presence-indicator run in
  `conformance.py`; boolean presence flags accepted).
- Cross-SDK signature-interop matrix (`sdk-interop.yml`, #139): all ten
  SDKs produce identical canonical signing bytes and signature for the
  shared vector; timestamp *generation* shape is reported advisory
  (7 whole-second / 3 fractional) and becomes blocking with LEP-0002
  implementation.
- SPEC→test coverage matrix (`docs/SPEC-COVERAGE.md`, #135): 137
  normative clauses mapped to named tests with a CI checker
  (`tools/check_coverage_matrix.py`); gaps and exemptions are explicit.
- Reserved-core-field enforcement (`tools/check_reserved_fields.py`,
  #146, closes #134): vertical attributes redefining core field names now
  fail pre-write in model generation and pre-hash in schema sync — AGENTS
  rule 6 is machine-enforced.
- Repo hygiene checkers (`repo-hygiene.yml`, #138/#140): action
  pin-truth (every `uses@sha` verified against upstream tags,
  rate-limit-resilient), docs-citation drift (advisory), and
  vulnerability-exception expiry (blocking; dated re-review rows).
- Adversarial reference-platform E2E (`test_adversarial_e2e.py`, #133):
  replay/idempotency, over-award, tampered-signature, unknown-type,
  dead-letter, and PII-boundary scenarios locked; findings filed as
  #131 (LCP-006 unreachable) and related.
- Runtime log-capture tests for Python/TypeScript/Go SDKs (#130) plus Go
  static coverage in the hygiene checker (#137); invariant 7 is now
  enforced statically and dynamically where CI runs it.
- Auth error-code triage (`docs/err-auth-triage.md`, #145): bearer-key
  mismatch must be LCP-002 per §5 — code fix pending ruling on #142;
  five §5-unassigned conditions filed for clarification (#143).

## [1.0.2] — 2026-08-18

### Security and hardening (repository audit remediation)

- Attachment authorization boundary: only the attachment owner, a
  `platform:admin`, or a buyer with a delivered winning `post` may download
  attachment bytes or metadata; ping-only buyers are denied with a uniform
  non-enumerating 404, and authorization decisions are audited without PII.
- WSGI binary attachment upload/download parity with the local threaded
  server: per-route body limits selected before `wsgi.input` is read, byte
  payloads bypass `json.dumps`, and binary response headers are forwarded.
- PostgreSQL offer discovery and filtering: relational `tenant_id`/`vertical`
  columns with backfill, `idx_offers_discovery` index, and portable candidate
  index queries; verified against a real Postgres 16 instance.
- Runtime lifecycle transition enforcement: a shared legal-transition table
  (tested against the conformance graph), LCP-004 rejection of invalid edges,
  terminal states including `ERASED`, role-scoped event permissions,
  message-ID idempotency, and audit records for every applied transition.
- Equivalent validators across all ten SDKs: envelope + message-type dispatch
  + vertical validation + recursive `ping_safe` enforcement + offer
  validation + offline `$ref` resolution + format assertions, driven by a
  shared 14-fixture cross-language validation corpus with per-SDK harnesses
  wired into CI.
- Sensitive vertical ping policy: ~33 fields flipped to post-only with
  documented privacy rationale; new negative ping vectors for insurance,
  legal, and MVA fields; `docs/PRIVACY-DATA-GOVERNANCE.md` data-minimization
  guidance.
- Expiry, consent, withdrawal, and erasure lifecycle: canonical `expiry`
  object (absolute/relative), lead/consent expiry enforced before matching
  and delivery, `CONSENT_WITHDRAWN` suppression, idempotent
  `ERASURE_REQUEST`, attachment expiry cleanup, and durable retryable object
  deletion.
- MCP adapter hardening: configurable `LCP_PLATFORM_ID` receiver, path
  traversal-guarded schema fallback, percent-encoded identifiers, and
  pre-send SDK validation of every tool-created envelope.
- Role-specific lead-status projections: publisher, buyer, and administrator
  views with per-role data exposure and uniform 404 for unauthorized callers.
- Offer delivery-window vertical/channel enforcement and conditional bid
  fields: `if/then` schema (price/currency required only for `accept`),
  matching OpenAPI 3.1, generated models, and under-floor accept rejection.
- Release metadata and evidence: synchronized runtime `__version__` values,
  immutable container digest binding in `release-manifest.json`, and a
  stale-release-state gate that fails the release workflow on outdated
  pre-release language in release-facing docs.
- Governance and operational hardening: CODEOWNERS, private security
  advisory route, CLA verification, request IDs, durable dead letters with
  replay/quarantine, structured worker metrics, and `npm ci` in CI.
- Documentation consistency: broken anchors fixed, a fence-aware link checker
  in CI, and copy-paste examples for WSGI attachments, Postgres offer
  discovery, lifecycle events, and role-scoped reads.

### Fixed

- Kotlin SDK compile failure: `SchemaValidator` migrated to the Jackson 3 API
  (`fields()` → `properties()`, non-generic `set()`, explicit list typing);
  the Kotlin SDK had never compiled against the networknt 3.x / Jackson 3
  pins and its CI job failed on every run.
- 16 Dependabot alerts: `python-multipart` pinned to 0.0.32 (the release
  containing every fix for CVE-2024-53981, CVE-2026-24486/40347/42561/
  53537/53538/53539/53540), resolving the transitive dependency of `mcp`.
- CI tooling job: `httpx` added to the tooling test dependencies so
  `test_validation_corpus` (which imports `lcp_sdk`) can import.
- Load benchmarks: both `benchmark_postgres_platform.py` and
  `benchmark_reference_platform.py` now generate a fresh absolute
  `expires_at` per record. The templates previously copied
  `examples/lead.json`'s fixed past dates, so every generated lead was born
  EXPIRED and the deliver-mode benchmark delivered zero webhooks on every
  CI run since it was added.

### Added

- LEP process adoption: `governance/LEP.md` operationalized via
  `governance/LEP-PROCESS-PLAN.md` (storage, numbering, review roles,
  14-day window, status lifecycle, roadmap tracking) and draft
  `LEP-0001` (batch submission and event subscriptions) filed for future
  adoption-scale work.
- Coordinated `SDK_VERSION` and package metadata bumped to 1.0.2 across all
  ten SDKs and the reference platform.

## [1.0.1] — 2026-08-17

### Added
- Non-blocking commit-attribution reporting, a one-command hook bootstrap,
  and a tracked `commit-msg` hook with configurable generated-identity policy
  that preserves human co-authors.
- Versioned requirement-profile and named service-area extensions in the
  reference platform, with adoption-readiness, LEP, and v1.1 roadmap guidance.
- Human-readable v1.0 field dictionary and advisory consent/evidence guide.
- Repeatable synthetic SQLite and concurrent PostgreSQL 18.4 benchmark tools,
  including local webhook latency simulation and documented capacity baselines.
- Conservative indexed offer candidate selection with fallback-safe matching,
  candidate-reduction regression thresholds, and PostgreSQL 18.4 CI reports.
- Published coordinated 1.0.1 package metadata for the reference platform
  extensions and the other SDK artifacts; see the signed release record in
  `docs/RELEASE.md`.

### Changed
- Converted the v1.0 release ticket and publishing-gap analysis into accurate
  post-release records, with the remaining governance and v1.1 work explicitly
  tracked.

## [1.0.0] — 2026-08-16

### Added
- Published the signed v1.0.0 release with verified package, container, SBOM,
  provenance, and release-manifest evidence.
- Complete Previous/Next navigation and `Page N of 6` numbering across all documentation pages, forming one continuous reading path through the docs site
- First adopter in [ADOPTERS.md](ADOPTERS.md): SpearPointX — performance-weighted lead exchange
- Release-ticket approval checklist (`docs/RELEASE-TICKET.md`) that turns dry-run artifact verification into a gate before the real tag
- Inline release-evidence review job that posts digest and conformance summaries on release pull requests
- Scheduled weekly probe (`release-verify.yml`) that verifies the published release evidence against the signed manifest and registry presence
- Dependency update decision record (`docs/DEPENDENCY-DECISIONS.md`) documenting applied and deferred Dependabot updates

### Changed
- Updated the MCP adapter for the MCP 2.x callback-based `Server` API while retaining compatibility with the 1.x decorator API; the SDK compatibility import gate now passes with `mcp 2.0.0`.
- Added the Kotlin Central Portal submission step required after Gradle's OSSRH compatibility upload, so the Kotlin deployment is automatically validated and published instead of remaining outside the Portal.
- Completed Kotlin Maven publication metadata and detached signing (sources, Javadocs, SCM, developers, and PGP signatures), fixed the nested Ruby SDK release working directory, and stopped retrying non-transient Central validation failures.
- Added immutable-version probes for retrying partially completed registry releases without attempting to republish Java, Kotlin, npm, crates.io, or RubyGems artifacts that already exist; crates.io publication now checks the sparse index and fails closed when registry state cannot be determined.
- Added the npm repository metadata required for provenance validation and the Ruby `rake` development dependency required by the trusted release task.
- Added complete Rust crate metadata (homepage, repository, documentation, and README) for a discoverable v1 package.
- Completed registry-side Trusted Publishing setup for crates.io and RubyGems; the crates.io bootstrap token was revoked and the RubyGems `lcp-sdk` publisher was activated by the successful v1.0.0 OIDC release.
- Fixed the npm release job to build the TypeScript SDK before publishing, so the bootstrap package will be replaced by the complete v1.0.0 distribution
- Switched Java and Kotlin Maven coordinates to the DNS-controlled `systems.spear` namespace, verified through `spear.systems`
- Recorded the NuGet `SpearSystems` organization as the `LcpSdk` package and Trusted Publishing owner while retaining `rbeno` as the OIDC token-requesting profile
- Moved the TypeScript SDK to the controlled `@spear-systems/lcp-sdk` npm scope after auditing the pre-existing `@spearsystems` namespace
- Updated the npm release job to Node.js 24 with npm caching disabled, meeting npm Trusted Publishing's current OIDC runtime requirements
- Made the Python release matrix use distinct GitHub environments for each pending PyPI publisher, allowing the three new monorepo projects to use OIDC Trusted Publishing without reusing an ambiguous pending-publisher identity
- Added an SDK packaging gate to the compatibility workflow that builds each SDK's distributable artifact on every push and PR (`python -m build`, `npm pack`, `dotnet pack`, `mvn package`, `cargo package`, `gem build`, `gradle jar`, `composer archive`), catching packaging regressions such as the sdist-to-wheel force-include breakage before release
- Fixed the release-evidence workflow: sign and verify now agree on Sigstore bundle names (`release-notes.md.sigstore.json` / `release-manifest.json.sigstore.json`), and the release manifest records the package identity (e.g. `lcp-sdk-python`) instead of the registry coordinate so the offline verifier accepts all 12 evidence records
- Added the LCP banner to the repository and made it the README masthead (`assets/lcp-banner.png`)
- Coordinated `SDK_VERSION` and all 12 package manifests to 1.0.0 and removed the remaining v0.1/draft status markers (README, SPEC, implementation decisions, changelog) ahead of the v1.0.0 release
- Applied Dependabot updates across GitHub Actions (checkout v7.0.1, setup-node v7.0.0, setup-python v7.0.0, setup-java v5.7.0, cosign-installer v4.1.2, dependency-review v5.0.0, crates-io-auth v1.0.5, ruby/setup-ruby v1.321.0, github-script v7) and SDKs (Rust hmac/getrandom/jsonschema/sha2, Go module bumps, TypeScript devDependencies, Kotlin plugin 2.4.10, JUnit 6.1.3)
- Resolved all known dependency vulnerabilities: pinned Jackson core/databind to 2.18.9 in the Java and Kotlin SDKs (nine CVEs on the transitive 2.18.3 pulled by networknt 1.5.9), bumped ajv to ^8.18.0 in the TypeScript SDK (GHSA-2g4f-4pwh-qvx6), and bumped golang.org/x/text to 0.39.0 in the Go SDK (GO-2026-5970); verified with `mvn test`, the Gradle test suite, `npm test`, `go test`, and osv-scanner reporting zero findings
- Replaced the flaky `search.maven.org` SOLR registry probe with direct `repo1.maven.org` artifact checks per coordinate, so the release dry-run's availability gate fails fast and reliably instead of timing out
- Added a pinned, SHA-verified osv-scanner job to the security workflow that scans every SDK lockfile on push, pull request, and the weekly schedule, failing on high/critical (or unrated) vulnerabilities while uploading the full SARIF to code scanning
- Fixed the Kyverno admission fixture for cosign 3.x: signing-config is enabled by default and rejects `--tlog-upload=false`, so the fixture now passes `--use-signing-config=false`; CI failures now surface the exact failing command in check-run annotations and upload a diagnostics artifact
- Applied Dependabot updates: jackson-databind 3.1.5 → 3.2.1 in the Java and Kotlin SDKs (second patched version for CVE-2026-59889; PRs #33/#32), MCP adapter constraint widened to `>=1.28.1,<2.1.0` (verified against mcp 2.0.0; PR #34), github-script 7.1.0 → 9.0.0 and download-artifact 4.2.1 → 8.0.1 (PRs #35/#36; the only v5 breaking change concerns downloads by artifact `id`, which this repo does not use)
- Completed the networknt json-schema-validator 3.x Jackson 3 rewrite for the Java and Kotlin SDKs (Dependabot PRs #16 and #25): validators migrated from `JsonSchemaFactory`/`SpecVersion` to `SchemaRegistry`/`Schema`/`SpecificationVersion`, and the SDKs' own JSON parsing moved from `com.fasterxml.jackson` to `tools.jackson` 3.1.5 (fixes CVE-2026-54512, CVE-2026-54513, and CVE-2026-59889); the Jackson 2 pins that mitigated the 1.5.9 transitive CVEs were removed. Verified with `mvn test` and the Gradle test suite
- Hardened reference-platform API-key storage: unsalted SHA-256 digests replaced with per-credential salted PBKDF2-HMAC-SHA256 (resolves the CodeQL weak-sensitive-data-hashing alerts) and constant-time verification; previously issued API keys must be re-provisioned via `upsert_credential`
- Fixed a path traversal in the Python SDK ping-safe check: the attacker-controlled vertical name is resolved against the preloaded schema registry instead of being interpolated into a filesystem path (resolves the CodeQL path-expression alerts)
- Visible CI status and a gated tagged-release workflow with a non-publishing dry run, registry collision checks, per-package signed SBOM/provenance evidence, Sigstore-signed notes, release manifest, and source SBOM
- Offline release-evidence verifier (`tools/verify_release_evidence.py`) that validates downloaded manifests, source archives, SBOMs, provenance statements, digests, and optional Cosign bundles before approval
- Hermetic Kyverno admission assurance using a pinned local OCI registry, generated fixture images, ephemeral signing keys, and local provenance/SBOM attestation rejection cases
- Protected release-record publication and maintainer documentation for GitHub branch rules, OIDC trusted publishers, release environments, and regulated-production approvals
- Declarative publisher mapping registry with versioned brand/form normalization, allowlisted transforms, OTP-aware quality signals, and PII-free source digests
- Distinct Kotlin Maven publication coordinate (`systems.spear:lcp-sdk-kotlin`) so Java and Kotlin SDKs can be published from the same release without artifact collision
- Controlled home-services service/subcategory taxonomy and a dedicated motor-vehicle-accident (`mva`) vertical
- Authenticated attachment upload/download with local AES-GCM storage and a production S3-compatible SSE-KMS adapter, fail-closed ClamAV scanning, immutable residency policy, and opaque storage references
- Call post delivery with call blocks, signed call outcome events, structured duration/disposition payable rules, and per-offer monthly quota/pacing reports
- Full Draft 2020-12 runtime validation APIs and generated typed models across all ten SDKs, with deterministic schema bundles and SHA-256 synchronization manifests
- Coordinated SDK version gate and protected trusted-registry publication workflow for PyPI, npm, NuGet, Maven Central, crates.io, RubyGems, Packagist, Go modules, and Swift Package Manager
- Repository-native paginated documentation navigation for publishers, buyers, developers, platform operators, security reviewers, and contributors
- Official multi-language SDK program with shared HMAC vectors, compatibility policy, Tier 1 Python/TypeScript/Go/.NET SDKs, and Tier 2/3 Java/PHP/Rust/Ruby/Kotlin/Swift reference SDKs
- Python raw-body webhook verification and MCP adapter reuse of the shared Python SDK transport/signing implementation
- SDK compatibility workflow covering every language package and the shared signing fixture
- Security/deployment hardening profile with Postgres support, fail-closed production configuration, AES-GCM envelope encryption, request limits, strict test-traffic separation, health endpoints, audit records, durable routing leases, SSRF/egress controls, and controlled lead erasure
- Cloud-neutral Kubernetes examples with separate API/worker deployments, network policies, disruption budgets, autoscaling guidance, and mounted secret/encryption-key configuration
- Production-oriented reference platform/router with SQLite/Postgres persistence, offer matching, ping/bid/post routing, signed webhook retries, admin CLI, and Docker sandbox
- Standalone Python SDK for envelope construction, JSON Schema validation, canonical HMAC signing, idempotency, and HTTP operations
- Buyer offer schema (`schemas/offer.json`) and implementation decision profile (`docs/IMPLEMENTATION-DECISIONS.md`)
- End-to-end synthetic publisher and buyer sandbox fixtures
- OpenAPI and MCP documentation for the canonical HMAC signing profile
- OpenAPI 3.1 specification for the HTTP transport binding (`api/lcp-openapi.yaml`)
- Code of Conduct (Contributor Covenant v2.1)
- Maintainers file
- Adopters file
- GitHub issue and pull request templates
- Trust model section in SECURITY.md
- AI contribution disclosure policy in CONTRIBUTING.md
- Bid message type (`schemas/bid.json`) — completes the ping/bid/post auction flow
- 4 new vertical schemas: insurance, solar, legal, home_services
- Platform integration guide (`docs/PLATFORM-INTEGRATION.md`)
- Call tracking fields: `forwarded_to`, `tracking_number` on call block
- IVR detail: `language`, `abandoned`, `menu_options_selected`
- OTP detail block: `channel`, `verified_at`, `verified_value_hash`, `attempts`
- Contact window: consumer availability hint (timezone, hours, days)
- Platform source: ad platform attribution in provenance
- Lead quality + verification: `verified_phone/email`, `verification{method, by, at}`
- Compliance scrubs: DNC, litigator, blacklist (`scrubs[]` array)
- Lead source/quality: `source_type` (open), `acquisition_method`, `is_incentivized`
- Ping quality flags: verified, incentivized, duplicate_risk, spam_score, scrub statuses
- Offer restrictions: excluded sources, reject incentivized, claim language, scrub rejections
- Delivery windows + capacity in capabilities
- Payable definition + payable status in post pricing
- Dispute window hours + expiry in post pricing
- `publisher_id` + `offer_id` + `lead_age_minutes` in ping
- `offer_id` in post
- `rejection_reason` (structured) in ack
- `consent_expires_at` in compliance
- `secondary_phone` + `preferred_contact_method` in consumer
- `email_hash` for email-based dedup
- `dedup_window_hours` hint in ping
- `exclusivity` hint in ping
- `submitted_at` on lead, call, and post
- `CONSENT_WITHDRAWN` + `ERASURE_REQUEST` event patterns
- Webhook signing documentation (same HMAC scheme)
- `transferred_from` + `queue` blocks on call
- 11 new mortgage fields (rate term, cash-out, bankruptcy, foreclosure, etc.)
- `mortgage_product` object in mortgage vertical — country-scoped product enums (US: conventional, FHA, VA, HELOC, jumbo, USDA; CA: insured, insurable, uninsured; GB: standard_variable, discount, tracker, offset; AU: principal_and_interest, interest_only, offset)
- Country-scoping rule for vertical schemas (AGENTS.md rule 7, SPEC.md §3)
- Spear Systems watermark added to all schemas, SPEC header, README, and MCP server

### Specification and initial build-out (2026-08-15)

- LCP specification (SPEC.md) — 14 sections + 2 appendices
  - Envelope/payload separation with transport-agnostic design
  - Canonical core: consumer (full_name, locale, no gender), location,
    compliance (consent_evidence[] array, consent_purposes[]), provenance
    (agent block), attributes, status, channel
  - 6 message types: lead, call, ping, post, ack, event
  - Error taxonomy (14 error codes, errors[] array shape)
  - Open/closed enum policy (message.type closed; status, channel, event open)
  - Status transition graph with terminal states + resolution paths
  - Security: per-pair HMAC-SHA256 phone hash, agent attestation (JWT),
    30s agent-as-consumer timeout, transport-neutral security block
  - Agent binding (MCP first, abstract binding rules for A2A)
  - Conformance tiers L1–L3, self-declared
  - Capability discovery (countries as ISO 3166-1 alpha-2)
  - Governance: Apache 2.0, anti-capture, extension registry, self-declared
    conformance, CLA
  - Exclusivity field (exclusive/shared, max_buyers)
  - Lead expiry (expires_at / ttl_seconds)
  - Settlement scope statement (LCP governs data, not money movement)

- JSON Schemas (Draft 2020-12) — 9 files
  - schemas/envelope.json, core.json, lead.json, call.json, ping.json,
    post.json, ack.json, event.json
  - verticals/mortgage.json (17 fields, all tagged ping_safe: true/false)
  - Ping schema is strict allowlist (additionalProperties: false)
  - Open enums: status, channel, event, call.status (no enum constraint)
  - Closed enum: message.type (triggers LCP-006)

- 6 example payloads (continuous AU mortgage lead story)
  - lead, call, ping, post, ack, event

- 27 test vectors across 3 conformance tiers
  - L1 (8): envelope, required fields, idempotency, UUID, timestamps
  - L2 (12): lifecycle, ping/post PII split, ping_safe enforcement,
    invalid phone, missing consumer, non-Western full_name
  - L3 (7): agent attestation, status transitions (25 edges), US TCPA
    consent, errors[] array, open-enum pass-through, exclusivity, expiry

- Conformance runner (test-vectors/conformance.py)
  - JSON Schema Draft 2020-12 validation via referencing library
  - Ping_safe enforcement (rejects non-safe fields in ping attributes)
  - Status transition graph validation
  - 27/27 tests passing

- Reference MCP server (implementations/mcp-server/)
  - 6 tools: submit_lead, submit_call, query_lead_status, get_schema,
    get_capabilities, list_offers
  - Stdio transport, stateless adapter
  - Local schema fallback (works without a live endpoint)
  - Bearer + HMAC authentication

- Governance documents
  - CLA (full text: copyright + patent grant, corporate contributions)
  - CONTRIBUTING.md (anti-capture clause, core vs. extension guidance)
  - SECURITY.md (responsible disclosure, PII-specific guidance)
  - TRADEMARK.md ("LCP compliant" usage rules)
  - EXTENSION-REGISTRY.md (namespace format, open registration)

- Deep-research review (docs/)
  - Cross-LLM review prompt
  - Sonnet 5 adversarial review — all 5 blockers + 15 should-fixes resolved

### Resolved (from deep-research review)
- B1: Ping PII safety → strict allowlist (additionalProperties: false)
- B2: Compliance block → generic consent_evidence[] array
- B3: Phone hash → per-pair HMAC-SHA256
- B4: Status transition graph published
- B5: Open/closed enum policy stated
- 15 should-fixes resolved (full_name, gender→attributes, errors[],
  consent_purposes[], expires_at, exclusivity, agent attestation as JWT,
  30s timeout, reserved-namespace rule, countries replaces markets, etc.)