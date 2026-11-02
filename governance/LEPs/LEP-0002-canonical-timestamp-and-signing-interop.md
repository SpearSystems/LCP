# LEP-0002 — Canonical timestamp generation and cross-SDK signature interop

- **Title:** Canonical timestamp generation and cross-SDK signature interop
- **Author(s):** LCP maintainers (draft for adopter review)
- **Date:** 2026-11-02
- **Status:** **Implemented** — accepted 2026-11-03; implemented and verified same day (PR #158, blocking interop 10/10)
- **Target version:** v1.1.0 (documentation clarification; no wire-contract change)

## Decision (maintainer, 2026-11-03)

**Accepted.** Recorded by the maintainer (randybeno) under the per-decision
sign-off model. Decision basis: the independent ARB L-Spec review dossier
(<https://github.com/SpearSystems/LCP/issues/121#issuecomment-6578306330>) —
verdict FINDINGS with no acceptance blockers; every normative claim survived
execution-backed refutation (five SDKs probed live at the subject SHA,
signature identity 10/10, canonical form parseable by every executable
verifier, normalize-then-recompute class closed by the MUST-verbatim
wording). The earlier retro-audit corrections (C1–C10, PR #149) are landed;
the process finding F-4 (advisory→blocking drift) is neutralized by the
merged baseline-sync test (#152,
`test_known_fractional_baseline_matches_lep_evidence_table`).

Conditions carried to implementation: fix the "holds no outbound signer"
sentence (dossier F-1 — the platform signs outbound webhooks via
`router.py → messages.webhook_headers → auth.signature_for`, emitting the
canonical form; the format conclusion stands), the Swift "only the canonical
form" overstatement (F-2: Swift accepts `+00:00` offsets; it rejects only
fractional seconds), and the deferred-Q3 "JWT/JWS" misnomer (F-3: the
security block is the `HMAC-SHA256` mirror). The public 14-day window is
waived by the maintainer's recorded decision; the discussion issue remains
open and any post-decision adopter comment with new evidence reopens this
record per the trigger clauses.

Evidence corrections (evidence-table and derived-count fixes per ARB
retro-audit at `dee5584`) landed in PR #149 before acceptance.

Implementation: PR #158 (this branch `impl/lep-0002-canonical-timestamp`):
Java/Kotlin/Rust SDK whole-second emission + frozen-clock unit tests, SPEC
§9 canonical-generation paragraph, interop baseline flip and blocking CI.
Verification: `SDK signature interop` workflow on PR #158 (all ten SDK
generation-shape cells PASS; matrix job now red on any shape FAIL) plus
the `interop probe (java|kotlin|rust)` jobs and per-SDK `mvn|gradle|cargo
test` suites carrying the new `CanonicalTimestamp*` frozen-clock tests.

Public discussion: <https://github.com/SpearSystems/LCP/issues/121>
(tracked from `docs/V1.1-ROADMAP.md` Phase 5, "Canonical timestamp
generation and cross-SDK signature interop").

## Problem statement and evidence

LCP's HMAC canonical signing input (SPEC.md §9, "Authentication") is the
exact UTF-8 byte sequence:

```text
<timestamp>\n<idempotency-key>\n<raw-request-body>
```

The `<timestamp>` term is the literal `X-LCP-Timestamp` header string —
verifiers recompute the digest over the received string, never over a
normalized clock value (e.g. `canonical_signing_bytes` in
`implementations/sdk/python/lcp_sdk/signing.py`, `CanonicalSigningBytes`
in `implementations/sdk/go/lcp.go`). SPEC.md §9 constrains the replay
window but does not state what format a signer MUST emit, so each SDK
chose its own. The v1.0.2 external review flagged this; every line below
is re-verified against `origin/main` at draft time.

**Signer-side divergence (generation):** _state at draft time, superseded by implementation PR #158._
Java, Kotlin and Rust now emit the canonical whole-second form pinned in
SPEC.md §9; this table is the LEP-0002 dossier evidence, not the current
emission. The interop matrix baseline tracks the post-implementation state
(see `tools/sdk_interop_matrix.py` `KNOWN_FRACTIONAL_SDKS` and the caption
check in `tools/tests/test_sdk_interop_matrix.py`).

| SDK | File:line | Expression | Emitted form |
|---|---|---|---|
| Python | `implementations/sdk/python/lcp_sdk/envelope.py:10-12` (used at `client.py:64`) | `strftime("%Y-%m-%dT%H:%M:%SZ")` | whole seconds |
| TypeScript | `implementations/sdk/typescript/src/index.ts:22-23` (used at `:131-132`) | `new Date().toISOString().replace(/\.\d{3}Z$/, "Z")` | whole seconds (ms stripped) |
| Ruby | `implementations/sdk/ruby/lib/lcp_sdk.rb:76` | `Time.now.utc.strftime("%Y-%m-%dT%H:%M:%SZ")` | whole seconds |
| PHP | `implementations/sdk/php/src/LcpSdk.php:75` | `gmdate('Y-m-d\TH:i:s\Z')` | whole seconds |
| C# | `implementations/sdk/csharp/src/LcpSdk.cs:96` | `DateTimeOffset.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")` | whole seconds |
| Swift | `implementations/sdk/swift/Sources/LCP/Lcp.swift:39` | `ISO8601DateFormatter().string(from: Date())` (default `.withInternetDateTime` options) | whole seconds |
| Go | `implementations/sdk/go/lcp.go:164` | `time.Now().UTC().Format(time.RFC3339)` | **whole seconds** — the `time.RFC3339` layout carries no fractional-seconds token, so `Format` always drops the nanosecond field (verified at dee5584: the SDK probe formats the frozen instant `…10:20:00.123456789Z` as `2026-08-15T10:20:00Z`; CI matrix measures 7 whole-second / 3 fractional) |
| Java | `implementations/sdk/java/src/main/java/com/spearsystems/lcp/LcpSdk.java:54` | `Instant.now().toString()` | **fractional** (0/3/6/9 digits as returned by the clock; whole seconds only when the nano field is exactly zero) |
| Kotlin | `implementations/sdk/kotlin/src/main/kotlin/com/spearsystems/lcp/LcpSdk.kt:32` | `Instant.now().toString()` | **fractional** (as Java) |
| Rust | `implementations/sdk/rust/src/lib.rs:51` | `OffsetDateTime::now_utc().format(&Rfc3339)` | **fractional** (nanosecond subsecond is formatted when nonzero) |

The envelope `lcp.message.timestamp` defaults reuse each SDK's
generator (Ruby `lcp_sdk.rb:54`, Java `LcpSdk.java:23`, Kotlin
`LcpSdk.kt:21`, Rust `lib.rs:30`, Swift `Lcp.swift:16`, Go
`lcp.go:45`); only the Java, Kotlin, and Rust defaults inherit the
fractional divergence — the Ruby, Swift, and Go defaults already emit
the canonical whole-second form. The reference platform generates whole
seconds both for inbound replay-window checks
(`implementations/reference-platform/lcp_platform/storage.py:21-22` `now_iso`)
and for outbound webhook signing: the router's delivery path
(`router.py → messages.webhook_headers → auth.signature_for`) emits the
canonical form on the transmitted `X-LCP-Timestamp` header, so the platform
is an outbound signer already producing the shape this LEP pins. The MCP
adapter inherits the Python SDK's whole-second form
(`implementations/mcp-server/lcp_mcp_server/client.py` wraps
`lcp_sdk.LCPClient`).

**Concrete interoperability consequences:**

1. *Verifier asymmetry is a live failure, not a hypothetical.* Verifiers
   re-parse the header for the replay window. Nine of the ten parse
   leniently and accept both forms — Python `fromisoformat` with an
   RFC-2822 fallback (`signing.py:_parse_timestamp`; note `fromisoformat`
   only parses a 9-digit fraction on Python ≥ 3.11, and the supported
   floor is 3.10), Go
   `time.Parse(time.RFC3339)` (`lcp.go:73-74`, which tolerates a
   fractional second absent from the layout), Java `Instant.parse`
   (`LcpSdk.java:42`), Kotlin `Instant.parse` (`LcpSdk.kt:28`), Ruby
   `Time.iso8601` (`lcp_sdk.rb:27`), PHP `DateTimeImmutable`
   (`LcpSdk.php:28`), C# `DateTimeOffset.TryParse` (`LcpSdk.cs:59`),
   TypeScript `new Date(timestamp)` (`index.ts:78`), Rust
   `OffsetDateTime::parse(&Rfc3339)` (`lib.rs:41`). The Swift verifier
   (`Lcp.swift:33`) uses the default `ISO8601DateFormatter`, which
   rejects fractional seconds: a **Java, Kotlin, or Rust** signer can
   produce a request that a Swift verifier refuses outright, with an
   error ("timestamp outside replay window") that misattributes a format
   problem as a clock problem.
2. *Canonical bytes are underdetermined.* Because the signature covers
   the timestamp string, any verifier or intermediate that normalizes
   the received value (parses and reformats it) before recomputing the
   HMAC will disagree with the signer on the signed bytes whenever the
   two formats differ. The spec's silence invites exactly that class of
   implementation, and cross-language reviewers cannot tell "recomputed
   over the wire string" from "reformatted" today.
3. *Conformance cannot see the divergence.* The shared SDK vector
   (`test-vectors/sdk/hmac.json`) pins the fixed string
   `2026-08-15T10:20:00Z`, so every SDK signs a constant and passes
   without ever exercising its own clock. A generation bug in one SDK is
   invisible to CI.

Adopter signal: this is a cross-language change affecting all SDKs and
the signature surface, which LEP.md reserves for the process. The
evidence requirement is met by the in-tree citations above plus the
roadmap's external-review finding; live adopter reports are solicited in
the discussion issue.

## Scope

- Core: **clarification only** in SPEC.md §9 (and the SDK READMEs) — one
  canonical generation format plus the explicit note that the timestamp
  *string* is the signed input, not the clock. No new fields, no message
  types, no version bump of the wire contract; envelopes and headers are
  unchanged in shape.
- All ten SDK bundles: signer-side formatting alignment (three SDKs emit
  fractional today) and the envelope default-timestamp generators.
- Reference platform and MCP adapter: already canonical; cited as the
  conformance reference, no change.
- CI/conformance: new cross-SDK signature-interop job (evidence gate,
  runs before any SDK is touched); existing `test-vectors/sdk/hmac.json`
  stays authoritative.
- Vertical schemas, registered extensions, OpenAPI paths, and MCP tool
  contracts: **no change** (the header is transport metadata, already
  documented; no schema files are touched).

## Proposed wire shape

Canonical generation format for `X-LCP-Timestamp` (and the mirrored
`payload.message.security.timestamp`, and SDK envelope
`lcp.message.timestamp` defaults):

```text
YYYY-MM-DDTHH:MM:SSZ
```

— UTC, whole seconds, exactly one `Z`, no fractional part, no numeric
offset. This is already the form used by SPEC.md §9's own example
(`"timestamp": "2026-08-15T10:20:00Z"`), the shared HMAC vector, the
reference platform, and seven of ten SDKs.

Valid:

```text
X-LCP-Timestamp: 2026-08-15T10:20:00Z
```

Invalid (MUST NOT be generated by signers):

```text
2026-08-15T10:20:00.123Z      (fractional seconds)
2026-08-15T10:20:00.123456789Z
2026-08-15T10:20:00+00:00     (numeric offset instead of Z)
2026-08-15T10:20:00           (no zone marker — ambiguous local time)
Mon, 17 Aug 2026 10:20:00 GMT (RFC-2822; a receiver may parse it, no
                               conforming signer may emit it)
```

Signing canonicalization rule (normative note added to SPEC.md §9): the
HMAC covers the **exact timestamp string transmitted in the header**,
byte-for-byte. Signers MUST build the canonical input from the same
string they put on the wire; verifiers MUST recompute over the received
header value verbatim — never over a parsed-and-reformatted timestamp.
Receivers keep applying their existing (lenient) clock parsing to decide
the replay window; this proposal changes no verifier behavior.

## Privacy, security, and universal-core audit

- **Universal-core audit:** no new field, type, endpoint, or error code;
  the core message set is untouched. This is a binding clarification of
  an already-implicit convention (the spec's own examples and vectors
  use the canonical form).
- **Privacy:** whole-second granularity discards at most sub-second
  timing noise. Against a default 300-second replay window
  (SPEC.md §9; minimum operationally meaningful window discussed in ops
  docs), sub-second precision carries no additional privacy or security
  value, and no consumer PII field is involved — `X-LCP-Timestamp` is
  request metadata.
- **Security/replay:** the defense is the window check plus signature
  over exact bytes. Tightening the *emitted* format neither lengthens
  the window nor shortens it, and does not change what a verifier
  accepts. The proposal reduces attacker-visible ambiguity by pinning
  the signed-string invariant, closing the normalize-then-recompute
  failure mode in principle.
- **Abuse:** none new; identical signed-message volume and semantics.
- Verifier leniency today (documented above: parsers already accept
  whole-second and fractional forms everywhere except the strictest,
  Swift, whose default `ISO8601DateFormatter` accepts the canonical
  whole-second `Z` form and `+00:00` numeric-offset UTC variants but
  rejects fractional seconds) means the proposed signer format is the
  intersection every verifier already parses.

## Compatibility, versioning, and deprecation impact

- The change is **signer-side format tightening; verifiers unchanged**.
  Every receiver in the tree — and any reasonable third-party receiver
  built from the spec examples — already parses `YYYY-MM-DDTHH:MM:SSZ`:
  it is the exact string in SPEC.md §9, the envelope example at SPEC.md
  line 61, and `test-vectors/sdk/hmac.json`. No accepted input format
  stops parsing, so the N+2 compatibility window is not engaged; no
  deprecation notice is required.
- Wire contract, schemas, and error taxonomy are unchanged, so no
  protocol version bump: a v1.0 receiver interoperates with a v1.1
  tightened signer, and with an old fractional signer, exactly as today
  (except against a strict Swift verifier, which only ever accepted the
  canonical form — after this change every SDK signer produces it, so
  the known broken direction becomes safe).
- Old SDK versions continue to work in the field; they emit fractional
  timestamps that nine verifiers already parse. This is a compatibility
  *asymmetry removal*, not a breaking change.
- The signature itself is unaffected for any request: the signed string
  equals the transmitted header string both before and after the change.

## SDK, OpenAPI, MCP, conformance, and operational impact

- **SDKs (after acceptance):** Java (`LcpSdk.java:54`, `:23`), Kotlin
  (`LcpSdk.kt:32`, `:21`), Rust (`lib.rs:51`, `:30`) switch to explicit
  whole-second UTC formatting
  (`DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'")` with
  `withZone(ZoneOffset.UTC)` over `Instant.now()`; in `time`-crate terms
  `now_utc().replace_nanosecond(0)?.format(&Rfc3339)`). **Go needs no
  code change** — `time.Now().UTC().Format(time.RFC3339)` (`lcp.go:164`,
  `lcp.go:45`) already emits the canonical form because the layout has
  no fractional token. The **seven** already-canonical SDKs get no code
  change; per-SDK unit tests pin the emitted shape with a frozen clock
  so regressions are visible in each language's own CI.
- **OpenAPI/MCP:** no schema-surface change (the header is documented
  auth metadata, not a schema'd body field). The MCP adapter's client
  wrapper inherits Python's canonical form; its README example stays
  valid.
- **Conformance:** add `test-vectors/sdk/hmac.json` consumers unchanged;
  the new interop job (below) becomes the gate that was missing. No new
  vectors are proposed at Draft; if review wants a machine-checkable
  artifact, a whole-second format-check vector could be added under
  `test-vectors/sdk/` as part of implementation.
- **Documentation:** SPEC.md §9 gets the canonical generation format and
  the signed-string invariant; SDK READMEs and the reference-platform
  auth README repeat the one-line format statement.
- **Operations:** none — receivers need no configuration change, no
  redeploy, no window retune. Clock/NTP guidance remains the existing
  ops-doc concern (tracked separately in Phase 5 "Operator clock-skew
  guidance").

## Alternatives considered

1. **Keep per-SDK leniency ("any RFC 3339 is fine").** Zero cost today,
   but it leaves the documented live failure (fractional signer vs strict
   Swift verifier) in place, keeps the signed-bytes invariant implicit,
   and guarantees the CI interop job would have to assert *inequality*
   instead of one canonical byte string. Rejected as the status quo that
   generated the review finding.
2. **Tighten verifiers to reject non-canonical formats.** Breaks
   deployed senders (old SDK versions are in the wild), turns a
   documentation clarification into a compatibility break engaging N+2,
   and violates the decision rule that a v1 receiver must not be made to
   fail on currently-tolerated data. Rejected.
3. **Sign a normalized clock value (e.g. always truncate to seconds
   before signing but transmit the full format).** Splits the signed
   string from the transmitted header, making verification
   implementation-dependent and reproducing the exact ambiguity this LEP
   removes. Rejected.
4. **Deployment-specific formatting negotiated per partner.** Reintroduces
   per-pair configuration for a protocol-level byte invariant; fails the
   universal-core simplicity test. Rejected.

## Rollout, migration, observability, and rollback

Order is evidence-first; nothing in this plan is authorized until the
registry records `Accepted`:

1. **Interop CI job first.** New CI job signs one fixed message (frozen
   timestamp from `test-vectors/sdk/hmac.json`) in all ten SDKs and
   asserts the canonical signing bytes and the resulting signature are
   identical across languages, plus a generation-shape check per SDK with
   a frozen clock. On today's tree the generation-shape half *fails for
   **Java/Kotlin/Rust** by design (Go passes — `Format(time.RFC3339)`
   renders whole seconds; the shipped matrix flags it `ALIGNED` against
   the stale evidence row)* — the failure is the evidence that the
   job works; the job initially runs as an advisory/reporting job until
   acceptance, then becomes blocking in the implementation PR.
2. **SDK generation changes** (single batch, one release train): the
   three fractional emitters switch to canonical formatting; every SDK gains
   the frozen-clock format unit test. SDK version bumps are PATCH-level
   behavior alignment per bundle changelogs (no API change).
3. **Docs:** SPEC.md §9 normative paragraph + canonical example set,
   README/SDK README one-liners, CHANGELOG entry pointing at this LEP.
4. **Observability:** none required (verifiers unchanged); the interop
   job is the observable. Adopters who hit `timestamp outside replay
   window` against Swift receivers keep the same error, which now
   unambiguously means clock skew.
5. **Rollback:** revert is SDK formatting constants + docs; no wire or
   state to unwind. Since no verifier ever stops accepting the
   fractional forms, rolling back signer changes cannot strand any peer.

Migration for adopters with hand-written signers: none required for
interoperability (receivers stay lenient); emitting the canonical form
when convenient removes exposure to strict third-party verifiers. The
canonical format is what the spec examples already showed, so most
hand-written signers already comply.

## Deferred questions

- Should SPEC.md eventually *recommend* verifiers reject non-`Z`/offset
  forms, or additionally document an RFC-3339-with-offset tolerance,
  rather than leaving parse behavior implementation-defined?
- Should envelope `lcp.message.timestamp` defaults adopt the same
  explicit canonical statement in the schema documentation (it is already
  string-typed; no schema change proposed now)?
- Should the interop job also cover the transport-agnostic
  `payload.message.security` block (SPEC.md §9) — the HMAC-SHA256 mirror of
  the HTTP header set, not a JWT/JWS path — where the timestamp travels in
  the body rather than a header, including how the numeric attestation
  claims there interact with the canonical generation rule?
- Is a machine-readable format vector warranted under
  `test-vectors/sdk/`, or are per-SDK frozen-clock tests sufficient?

**Trigger to revisit:** any proposal to shorten the replay window below
roughly 10 seconds, any adoption of non-UTC canonical zones or
leap-second-sensitive pricing, or an adopter report of a strict verifier
in the wild rejecting something the lenient parsers accept — each would
turn today's clarification into a normative conformance requirement and
reopen this record with a Review cycle.
