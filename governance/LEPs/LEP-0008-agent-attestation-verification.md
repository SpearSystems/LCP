# LEP-0008 — Agent attestation verification: making §9's L3 real (closes #192)

- **Title:** Agent attestation verification — issuer-anchored JWS verification at intake, §5 mapping, and staged L3 advertisement honesty
- **Author(s):** LCP maintainers (draft for adopter review)
- **Date:** 2026-11-03
- **Status:** **Draft** — this record is a numbered Draft proposal only. It
  authorizes **no** implementation: no schema edit, platform module, SDK
  helper, vector, OpenAPI change, or SPEC text ships from this document until
  an independent dossier and a maintainer `Accepted` decision are recorded in
  `governance/LEP-REGISTRY.md`. The proposed shapes are **validated by
  execution** (see "Verification runs") — every example envelope validates
  through the repository's own `SchemaValidator`, the example tokens are real
  Ed25519 signatures a reviewer can reproduce from the pinned seed, and the
  failure battery runs against a reference verifier implementing §Proposed
  wire shape exactly as drafted.
- **Target version:** v1.1.0 (MINOR — security/verification semantics and
  error-mapping text; **no new wire field, no new message type, no new §5
  code**; `schemas/core.json` tightens the *existing* `attestation` string's
  form, which is wire-visible for non-conformant traffic only — see
  Compatibility)
- **Discussion:** [#205](https://github.com/SpearSystems/LCP/issues/205)
  (`[LEP] Agent attestation verification — making L3 real (closes #192)`)
- **Origin trigger:** not a roadmap item — this record exists because
  [#192](https://github.com/SpearSystems/LCP/issues/192) (found landing
  `test/gap-security-w5`, filed from PR #195) demonstrated the platform
  advertises `conformance_level: "L3"` while performing zero attestation
  verification, and `docs/SPEC-COVERAGE.md:187` (`SEC-09`) stays `gap` for
  exactly that reason. `docs/V1.1-ROADMAP.md` carries no attestation line
  (verified at `974b9ca`); the trigger is the coverage matrix and the
  §7 honesty gap itself.
- **Security/privacy reviewer:** this proposal touches an authentication/
  identity rule, so per `governance/LEP.md` (Roles and independence) the
  Decision requires a named human security/privacy reviewer or the decision
  maintainer's explicit co-sign of an ARB-Security dossier. Draft status
  names none; the role MUST be filled at Review.

## Problem statement and evidence

SPEC.md §9 "Agent attestation" (`SPEC.md:915-931`) states the requirement in
three sentences that the shipping stack does not honor:

> `provenance.agent.attestation` is a signed token proving the agent's
> identity and authority. … The token is verifiable against a published key
> (JWKS or pinned public key). Free-text attestations are non-conformant —
> the token MUST be verifiable. This is a fraud-resistance requirement for
> high-volume agent-submitted leads. (`SPEC.md:917-931`, condensed)

And SPEC.md §7 grades the tier on it: **L3 = "full + dedup fingerprints +
capability discovery + published JSON Schemas + agent binding (attestation
verification)"** (`SPEC.md:691-692`).

**What ships.** `schemas/core.json:128-137` defines the agent block as three
plain strings, required-presence only (`attestation` is `{"type": "string"}`,
`core.json:133`). The reference platform's intake
(`implementations/reference-platform/lcp_platform/router.py:262-298`,
`ingest()`) validates presence via `_validate_envelope`
(`router.py:1858`, schema errors → `"LCP validation failed"`/`LCP-100` at
`router.py:1910-1917`) and never inspects the value again; the lead is
persisted at `router.py:359`. Meanwhile `capabilities()` advertises
`"conformance_level": "L3"` (`router.py:1769`). There is no JWT decode, no
signature check, no `exp` enforcement, no claim binding, and no published-key
(JWKS/pinned) surface on any shipped surface — platform, SDKs, or MCP
adapter (`grep -rn "jwks|jwt.decode|verify_attestation" implementations/`
matches only vendored dependency copies; #192 verbatim).

**Measured evidence (re-executed at `974b9ca`, this record's base).** Against
the in-memory reference platform (`require_auth=True`, HMAC-signed
`POST /v1/lcp/leads`, `X-LCP-Test: true`, `test_mode=True`, sender `pub_123`
registered), an agent-provenance lead ingests `200 RECEIVED` with **any**
attestation value; only key *absence* fails:

| attestation value | HTTP result today |
|---|---|
| `"trust me, I am a real agent (free text)"` | **200 `RECEIVED`** |
| `alg=HS256` JWT, claims `{iss:"fake", agent_id:"ai-1", …}`, signature segment `AAAA…` (wrong signature) | **200 `RECEIVED`** |
| same token expired (`exp=1000000000`) **and** `agent_id` contradicting `provenance.agent.agent_id` | **200 `RECEIVED`** |
| key absent | `400 LCP-100` (`'attestation' is a required property at provenance/agent`) — presence only |

This is #192's table reproduced at the new base SHA; the exact commands and
outputs are in "Verification runs" below.

**The MCP lane agrees the obligation exists and nobody performs it.** The
surface test carries the token byte-for-byte and explicitly notes that
normalization would break "§9 agent attestation verification"
(`implementations/mcp-server/tests/test_gap_mcp_surface.py:223-267`) — the
adapter is an HTTP client (`implementations/mcp-server/lcp_mcp_server/client.py:67`),
so whatever the platform enforces, MCP inherits; nothing enforces.

**Coverage-matrix honesty.** `SEC-09` is `gap` precisely because "a
forged-token rejection test cannot exist because the behavior does not"
(`docs/SPEC-COVERAGE.md:187`), and `CNF-03`'s green note rests on shape
checks: "attestation via L3-001" (`docs/SPEC-COVERAGE.md:165`). L3-001's own
fixture token cannot verify under **any** key — decoded (Verification runs
§1): header `{"alg":"HS256","typ":"JWT"}`, claims
`{"agent_id":"ai-assist-001","acting_for":"pub_123","issuer":"spd-agent-auth","iat":"2026-08-15T12:00:00Z","exp":"2026-08-15T12:30:00Z"}`,
signature segment the literal string `signature`, `iat`/`exp` string-typed,
and the issuer claim named `issuer` instead of the JWT registered claim `iss`
(`test-vectors/l3-advanced.json:53`). The v1.0 review log settled the
*decision* ("`attestation` is free text → Defined as signed JWT with
verifiable claims", `SPEC.md:1094`); what never landed is the *mechanism*:
key distribution, claim vocabulary, verification timing, and failure mapping
were left undefined (#192, "Why this is filed instead of fixed here").

**Why the envelope's own HMAC is not the answer.** Transport authentication
(`SPEC.md:799-830`) already signs the exact request bytes
(`<timestamp>\n<idempotency-key>\n<raw-request-body>`, HMAC-SHA256 under the
per-pair secret) and LCP-012/LCP-002 already cover signature and identity
failures (`SPEC.md:638-641`). That proves *which pair secret* produced *this
request*. It cannot assert *which acting principal* — one publisher
credential may front many agents, agents rotate, and §10's roles
(`SPEC.md:973-978`) make "agent as transporter … requires attestation
('acting for partner X')" (`SPEC.md:977-978`) a first-class binding the
transport layer structurally cannot carry. The attestation is therefore an
**assertion about the acting principal, signed by an authority the receiver
can resolve** — not a second transport MAC. This distinction drives every
design decision below (asymmetric-only algorithms, `aud`/sender binding, and
the failure-code mapping).

**Adopter evidence.** #192 is the interop-testing evidence required by
`LEP.md` ("evidence from adopters or interoperability testing"): the conformance
grind lane proved the gap adversarially on the reference platform. External
corroboration for the demand direction: SPEC §9 itself calls attestation
"fraud-resistance … for high-volume agent-submitted leads"
(`SPEC.md:930-931`) — the fraud class (forged agent provenance) is exactly
what L3 exists to bind.

## Scope

- **Core protocol (additive MINOR with one tightening):** normative §9 text
  (verification procedure, claim vocabulary — `iss`/`aud`/`agent_id`/
  `acting_for`/`iat`/`exp` as NumericDate, algorithm allowlist, timing,
  binding, failure classes), one §5 clarification sentence extending the
  identity axis (`LCP-002`) to attestation verification failures — the class
  `docs/err-auth-triage.md` §3 already established — and a `schemas/core.json`
  form tightening of the existing `attestation` string (compact-JWS pattern +
  size bound). **No new envelope field. No new message type. No new §5 code.
  No vertical change.**
- **SPEC text:** §9 "Agent attestation" rewrite (exact normative sentences in
  §5–§9 of "Proposed wire shape"); §5 scope sentences at `SPEC.md:638-641`;
  §7/§8 one-sentence advertisement-honesty rule (§Proposed wire shape 9).
- **Reference implementation:** an intake verification step in `ingest()`
  after `auth.authenticate`/rate limiting and before `insert_lead`
  (`router.py:294-298` → `router.py:359`), an issuer-JWKS resolver with the
  shipped `validate_webhook_url` policy class (`security.py:16`, precedent
  mapping `SecurityPolicyError`→`LCP-100` at `router.py:243-246`), and the
  staged rollout modes (observe/enforce) plus the Phase 0 advertisement gate
  (see Rollout).
- **SDK / OpenAPI / MCP / vectors:** impact list below (test names V1–V12);
  MCP inherits through the HTTP path — no adapter logic (`client.py:67`);
  OpenAPI's `conformance_level` documentation (`api/lcp-openapi.yaml:839`)
  already exists; only the L3-001 fixture token and its `checks` list change,
  plus new attestation vectors and a pinned fixture JWKS.
- **Not in scope (each with a trigger under Deferred questions):** buyer-side
  (agent-as-consumer) attestation; `jti` single-use replay caches; DID/VC
  or third-party-PKI trust chains; RSA algorithm expansion; a §8 capability
  flag for verification mode; forwarding-direction re-verification; a
  platform-hosted key-registry surface; per-lead token freshness (`iat`/`nbf`
  enforcement).

## Proposed wire shape

### 1. What the attestation binds (normative framing)

The attestation asserts: *principal `agent_id`, acting for party
`acting_for`, at the authority `iss`, for the audience `aud`, within
`iat`..`exp`* — signed with an **asymmetric** key the receiver can resolve.
It MUST NOT be used as, or replaced by, transport authentication: the
envelope HMAC (§9) stays the wire authorization (§Authentication), and a
receiver MUST NOT downgrade the HMAC check because an attestation verified
(or vice versa). The two axes answer different questions and fail with
different codes (§8 below).

A symmetric (MAC) attestation is prohibited: any receiver able to verify it
can also mint it, so it carries no non-repudiable principal binding and is
indistinguishable from the envelope signature the request already carries.
This is why candidate trust model C (Alternatives) adds nothing.

### 2. Token form

Compact JWS (RFC 7519 §7.1): `base64url(ASCII-header) "." base64url(ASCII-claims) "." base64url(signature)`, three non-empty segments,
UTF-8/JSON payloads, standard base64url alphabet without padding (padded
segments are accepted by parsers but MUST NOT be generated, mirroring the
LEP-0002 canonical-generation discipline at `SPEC.md:832-845`).

Schema-layer tightening (lands in `schemas/core.json:133`, `$defs/provenance
→ properties.agent → properties.attestation`):

```json
"attestation": {
  "type": "string",
  "pattern": "^[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+$",
  "maxLength": 8192
}
```

The pattern is *shape* validation only (three non-empty base64url segments);
everything above shape is verification (§5), which the schema cannot express.
Free-text values therefore fail early with the present class of error
(`LCP-100`, `field: "provenance.agent.attestation"`), while JWT-shaped junk
fails verification (`LCP-002`) — see §8.

Header requirements: `alg` MUST be present and in the allowlist (§3);
`kid` MUST be present (key selection, §4); `typ` SHOULD be `JWT`. A `b64`
header or detached signature (`alg:none`, empty signature segment) is
non-conformant (and the empty third segment already violates the §2 pattern).

### 3. Algorithm allowlist (closed)

| `alg` | Key type | Status |
|---|---|---|
| `EdDSA` | OKP/Ed25519 | MUST be supported; RECOMMENDED default |
| `ES256` | P-256 EC | MUST be supported |
| `HS256`/`HS384`/`HS512` (any MAC) | — | MUST be rejected — reason `algorithm` |
| `none`, or anything outside the table | — | MUST be rejected — reason `algorithm` |

Rationale in §1; the MAC-rejection rule also closes the classic
algorithm-confusion attack surface (an attacker re-signing with the *public*
key as a symmetric secret). Receivers MUST read `alg` from the header only to
select a verifier from this allowlist, and MUST derive the expected algorithm
from the resolved JWK's `alg` when present (mismatch → `algorithm`).

### 4. Key distribution — RECOMMENDED trust model (settles #192's open item)

`SPEC.md:928-929` names "JWKS or pinned public key" but defines no surface.
The record settles on:

**Canonical: issuer-published JWKS over HTTPS.** `iss` is an HTTPS issuer URL
under the publisher's or agent-authority's control; the verification document
is resolved deterministically as
`jwks_url = iss + "/.well-known/jwks.json"` (RFC 8615 path, per-issuer).
Rules the implementation MUST honor:

| Aspect | Normative rule |
|---|---|
| Resolution | Fetch over TLS 1.3 (§9 Transport, `SPEC.md:795-797`); MUST NOT fetch before the request is transport-authenticated (unauthenticated callers get LCP-012/LCP-002 first — no fetch amplification); the issuer URL MUST pass the shipped `validate_webhook_url` policy class (`security.py:16`: HTTPS-only, no loopback/link-local/site-local literals — SSRF guard). Policy-invalid `iss` → rejection reason `issuer_policy` (a sender-fixable identity failure, §8). |
| Selection | `kid` (required, §2) selects the JWK; `kty`/`crv`/`alg` of the selected JWK MUST be consistent with the header `alg` (§3 allowlist). No key found for `kid` in a successfully fetched document → `key_unknown`. |
| Bounds | JWKS document ≤ 16 KiB, ≤ 32 keys (format sanity, mirroring the LEP-0003 32-entry report bound); token ≤ 8192 bytes (§2 `maxLength`). |
| Cache | Cache per `(iss, kid)`; respect HTTP `cache-control`/`max-age`, hard cap 24 h; negative cache for fetch failures ≤ 30 s. Fetch timeout ≤ 2 s, and MUST NOT block the intake worker beyond it (bounded worker, else `key_unavailable`). |
| Rotation | Routine rotation = new `kid` alongside the old in the same JWKS; deployments MAY keep the previous key for a bounded window (mirrors §9's "During key rotation, a receiver MAY accept a previously active secret", `SPEC.md:829-830`). No protocol change needed. |
| Compromise | Issuer removes the `kid`; propagation = cache TTL. Blast radius is per-issuer (each issuer publishes its own keys) — never global. |

**Fallback: pinned public key (deployment configuration).** A closed
publisher↔buyer pair MAY configure `iss → pinned JWK` locally (the §9
"pinned public key" leg). The pin **replaces resolution for that issuer**
and is otherwise identical. Pins live in receiver deployment config — **not**
a new protocol/registry surface; registering sender-scoped keys as a canonical
protocol surface is Alternatives B (rejected as the primary, with reasons).

Why issuer-anchored, decided against the candidates (full trade-off table in
Alternatives considered): §9's `issuer` claim ("Authority that issued the
token") only *means* something if the receiver can resolve it;
agent-as-consumer/agent-as-source topologies (§10, `SPEC.md:973-981`) put a
single agent authority across many publishers, which per-pair registration
cannot model without inventing a registry; and the reference platform ships
zero discovery surface today, so both models pay bootstrap cost — issuer
JWKS pays it once per issuer and gets revocation via rotation for free.

### 5. Verification procedure (reference algorithm — executed in the battery)

Runs **once per ingest attempt**, after `auth.authenticate` and the
per-sender rate limit (`router.py:281-298`) and **before** `insert_lead`
(`router.py:359`) — the same ordering principle as §9's "Receivers MUST
reject requests outside the acceptable timestamp window before processing
PII" (`SPEC.md:826-827`): a failed verification MUST NOT persist PII.
`source_type != "agent"` or an absent `provenance.agent` block → the
procedure does not run (presence semantics unchanged, `core.json:135`).

```text
1  size ≤ 8192 and 3 non-empty base64url segments        else FAIL format        (schema layer normally)
2  decode header & claims as strict UTF-8 JSON           else FAIL format
3  header.alg ∈ {EdDSA, ES256}                           else FAIL algorithm
4  header.kid present                                    else FAIL key_unknown
5  iss policy-valid (https + SSRF class)                 else FAIL issuer_policy
6  resolve (iss → JWK[kid]) via cache/§4                 fetch failure → key_unavailable (500, §8)
                                                         no such kid    → FAIL key_unknown
7  verify signature over header.claims bytes             else FAIL signature
8  claims.exp + skew ≤ now  (skew: MAY ≤ 30 s, MUST NOT more)  else FAIL expired
9  claims.aud == envelope lcp.message.receiver_id       else FAIL audience
10 claims.agent_id == provenance.agent.agent_id         else FAIL claims_mismatch
11 claims.acting_for == authenticated sender_id, OR
   sender scopes contain "act_for:" + claims.acting_for else FAIL claims_mismatch
12 all pass → ACCEPT (bind agent_id/acting_for/iss/kid into the lead record)
```

Reason vocabulary (normative, closed): `format`, `algorithm`,
`issuer_policy`, `key_unknown`, `key_unavailable`, `signature`, `expired`,
`audience`, `claims_mismatch`. Evaluation stops at the first failing step —
the order is deterministic so the same forged input always yields the same
reason (executed: an expired **and** claims-contradicting token reports
`expired`, §10 of Verification runs).

Step 11 reuses the shipped scope machinery (`auth.py:187-189` `has_scope`,
scope strings per sender, `storage.py:502`): `act_for:<party>` is a scope
string in the existing credential registry, not a wire field — an agent
submitting with its **own** sender credential (`agent_pub_001` on behalf of
`pub_123`, the L3-001 topology) verifies iff the delegation was registered.
Scope matching is substring-free and exact (`act_for:pub_123`
matches party `pub_123` only); delegation *graphs* (chain-of-agents) are
Deferred.

### 6. Claim set (normative vocabulary; settles #192's "claim binding")

| Claim | Required | Type | Rule |
|---|---|---|---|
| `iss` | MUST | string | HTTPS issuer URL (§4). **Replaces** the v1.0 table's non-JWT `issuer` private claim — the current §9 table (`SPEC.md:920-926`) is corrected at implementation; existing tokens using `issuer` are already unverifiable, so this is a clarification, not a break. |
| `aud` | MUST | string | Equals the envelope `lcp.message.receiver_id`; the receiver MUST additionally confirm that value is its own platform identity — already enforced today (`router.py:277-280`, receiver mismatch → `LCP-002`). Stops cross-receiver replay (§7). |
| `agent_id` | MUST | string | Byte-equal to `provenance.agent.agent_id` (extends `SPEC.md:922` "matches" into a normative MUST). |
| `acting_for` | MUST | string | Equal to the authenticated `sender_id`, or carried by its `act_for:` scope (§5 step 11). |
| `iat` | MUST | integer (NumericDate, RFC 7519 2) | Whole seconds. NOT enforced at intake in v1 (freshness = issuer policy; see Deferred). String timestamps are format-invalid — L3-001's string `iat`/`exp` is fixed at implementation (V8). |
| `exp` | MUST | integer (NumericDate) | Enforced per §5 step 8. |
| `nbf`, `jti` | MAY | — | `nbf` SHOULD be honored if present (same skew rule); `jti` is recorded but MUST NOT be required or single-use-cached in v1 (§7 rationale; Deferred trigger). |
| others | MAY | — | Ignored (forward-compatibility mirrors §6 "unknown optional fields ignored", `SPEC.md:660-662` — claims are open by design inside a verified signature). |

Unknown-to-this-spec *required* claims are not a concept: absence of a MUST
claim above is `claims_mismatch`/`format` (decode-time), never ignorable.

### 7. Replay, clock, and window rules (units-precise)

**Units (normative).**

| Quantity | Unit | Definition |
|---|---|---|
| one attestation | one signed assertion | exactly one (agent_id, acting_for, iss, aud) tuple per token; a token asserting two principals is not expressible (single claim set). |
| one verification | per ingest attempt | at most once per lead submission, at the §5 point; retries of the same envelope re-verify (same deterministic result, cached keys). |
| one failure | exactly one reason | first-failing step of §5 wins; no multi-reason payloads. |
| token validity | seconds | `exp` − `iat`, issuer-chosen; the protocol bounds only the *acceptance* window (§5 step 8's skew ≤ 30 s). The RECOMMENDED maximum lifetime is 900 s (advisory; normativizing it is Deferred with data). |
| replay window (transport) | 300 s default | unchanged §9 HMAC window (`SPEC.md:828`, `config.py:33`); the attestation rules are independent of it. |
| agent-evaluation window | 30 s default | §9 agent-as-consumer (`SPEC.md:960-967`), pinned "exactly 30" by the #195 lane (`implementations/reference-platform/tests/test_gap_security.py:491-517`; default `ping_timeout_seconds` 30, `messages.py:140`). Chosen as the **ceiling for clock-skew allowance** so no token accepted at intake can have been expired longer than one transaction window. |

**Clock rule.** `exp` is compared against the **receiver's** wall clock in
NumericDate seconds; deployments MAY configure a skew allowance ≤ 30 s
(default 0). No `iat` freshness requirement at intake — a 5-minute-old valid
token is *expected* behavior (agents batch many submissions), which is why
per-token single-use caching (`jti` replay cache) is deliberately absent:
the transport replay defenses (envelope HMAC over exact bytes +
idempotency-key equality + replay window — all shipped and pinned by the #195
lane's SEC-06 tests) already stop *request* replay; a *reused* token inside
its `exp` on new envelopes is the intended use, and cross-receiver reuse is
killed by `aud` (step 9). A `jti` cache would add unbounded state to defend
a non-threat; the Deferred trigger covers the case where forwarding or
exfiltration evidence changes that calculus.

**Cross-pair/cross-receiver replay (the binding #192 asked to settle).**
`(aud, iss→kid, acting_for==sender|scope)` together mean: a token minted for
platform_001 fails at platform_002 (`audience`); a token minted by publisher
A's authority fails at publisher B's submissions (`claims_mismatch`/
`key_unknown` — B cannot even produce issuer A's signatures because the
algorithms are asymmetric). No new state required.

### 8. Failure mapping onto the existing §5 taxonomy (no new code)

| Failure | Layer | Code | HTTP | Wire `field` | `details.reason` |
|---|---|---|---|---|---|
| `attestation` absent | schema (present behavior, `core.json:135`) | LCP-100 | 400 | `provenance.agent` | (schema error text, unchanged) |
| not three-segment JWS (free text, `none`, detached) / > 8 KiB | schema (§2 pattern) | LCP-100 | 400 | `provenance.agent.attestation` | (schema error text) |
| algorithm rejected | verifier | LCP-002 | 401 | `provenance.agent.attestation` | `algorithm` |
| `kid` missing / unknown in fetched JWKS | verifier | LCP-002 | 401 | `provenance.agent.attestation` | `key_unknown` |
| `iss` policy-invalid | verifier | LCP-002 | 401 | `provenance.agent.attestation` | `issuer_policy` |
| signature invalid | verifier | LCP-002 | 401 | `provenance.agent.attestation` | `signature` |
| `exp` past (+skew) | verifier | LCP-002 | 401 | `provenance.agent.attestation` | `expired` |
| `aud` mismatch | verifier | LCP-002 | 401 | `provenance.agent.attestation` | `audience` |
| claim↔envelope contradiction | verifier | LCP-002 | 401 | `provenance.agent.attestation` | `claims_mismatch` |
| JWKS unreachable / oversized / invalid document | verifier I/O | LCP-500 | 500 | `provenance.agent.attestation` | `key_unavailable` |

Why LCP-002 for verification failures rather than new policy invention:
§5's taxonomy sentence names the axes — "these are different failure modes
(**identity** vs. replay protection)" (`SPEC.md:639-641`);
`docs/err-auth-triage.md` §3 ruled that credential failures on the *identity*
axis belong to LCP-002; the platform already stamps intake **binding**
failures LCP-002/401 — receiver-not-this-platform (`router.py:277-280`) and
authenticated-sender-mismatch (`router.py:290-293`) — and `LCP-002` is
`AuthenticationError`'s default code (`auth.py:28`). An unverifiable
attestation is precisely a failing assertion of *who is acting*, on a
message whose transport identity is fine — same axis, existing code,
`details.reason` disambiguates. The §5 sentence at `SPEC.md:638` ("covers an
unrecognized `sender_id`") is extended at implementation by one clause:
"…or a recognized sender whose agent attestation fails §9 verification."
LCP-500 for `key_unavailable`: the receiver cannot complete verification
because of *its* (or its dependency's) availability; a 4xx would blame the
sender for the receiver's infrastructure. It is retryable and idempotency
(`SPEC.md:825-826` header-key equality + §4 ack semantics) keeps retries
safe; deployments MUST NOT silently fail-open to 200 in enforce mode
(observe-mode logging is the *only* sanctioned soft path, Rollout Phase 2).

### 9. Capability advertisement honesty (the L3 gate)

Normative addition for §7/§8 (and the reason Phase 0 exists):

> A receiver MUST NOT advertise `conformance_level: "L3"` unless it performs
> §9 agent-attestation verification as specified in §9; an endpoint that
> implements every other L3 leg except attestation verification advertises
> the highest tier it fully satisfies.

`SPEC.md:691-692` already makes this true *by definition* — L3 *includes*
attestation verification — the addition only names the advertisement
consequence. For the reference platform, which today ships dedup
fingerprints, capability discovery, and published schemas but not
verification, the honest advertisement is `"L2"` (it fully satisfies L2;
§7's tiers are cumulative grades, not per-leg flags). No new capabilities
field is minted to express partial tiers — minting one is a larger wire
surface than the fix.

### Valid example — agent-submitted lead with a verified attestation (direct sender)

Publisher credential `pub_123` submits through its own sender identity; the
token's `acting_for` equals the sender, `agent_id` matches the envelope,
`aud` matches `receiver_id`, EdDSA signature verifies against the fixture
issuer's pinned key (key material + seed below).

```json
{
  "lcp": {
    "version": "1.0.0",
    "message": {
      "id": "91d44346-f7d8-5d31-a8cd-c6c990858499",
      "type": "lead",
      "timestamp": "2026-11-03T09:00:00Z",
      "sender_id": "pub_123",
      "receiver_id": "platform_001",
      "correlation_id": null,
      "idempotency_key": "lep0008-valid-agent-lead-001"
    },
    "payload": {
      "lead_id": "lead_lep0008_agent_001",
      "external_id": "pub-internal-456",
      "submitted_at": "2026-08-15T10:19:50Z",
      "status": "NEW",
      "channel": "form",
      "consumer": {
        "first_name": "Jane",
        "last_name": "Smith",
        "email": "jane.smith@example.com",
        "phone": "+61412345678",
        "locale": "en-AU"
      },
      "location": {
        "country_code": "AU",
        "state_region": "NSW",
        "postal_code": "2000",
        "city": "Sydney",
        "timezone": "Australia/Sydney"
      },
      "compliance": {
        "consent_timestamp": "2026-08-15T10:19:45Z",
        "consent_source_url": "https://publisher.example.com/landing/mortgage",
        "consent_text_version": "v2.1",
        "consent_purposes": [
          "calls",
          "sms",
          "email",
          "share_with_partners"
        ],
        "consent_evidence": [
          {
            "type": "verified_consent",
            "provider": "internal",
            "token_or_url": "evd_abc123def456"
          }
        ],
        "otp_verified": true,
        "dnc_checked": true,
        "scrubs": [
          {
            "type": "dnc_national",
            "provider": "au_dncregister",
            "result": "clean",
            "checked_at": "2026-08-15T10:19:48Z"
          },
          {
            "type": "litigator",
            "provider": "litigator_list",
            "result": "clean",
            "checked_at": "2026-08-15T10:19:49Z"
          },
          {
            "type": "blacklist",
            "provider": "internal",
            "result": "clean",
            "checked_at": "2026-08-15T10:19:49Z"
          }
        ]
      },
      "provenance": {
        "received_at": "2026-11-03T09:00:01Z",
        "source_type": "agent",
        "source_id": "pub_123",
        "source_url": "https://publisher.example.com/landing/mortgage",
        "ip_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        "user_agent_hash": "f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5",
        "campaign_id": "camp_mortgage_nsw_2026",
        "creative_id": "cre_001",
        "landing_page_url": "https://publisher.example.com/landing/mortgage",
        "agent": {
          "agent_id": "ai-assist-001",
          "acting_for": "pub_123",
          "attestation": "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCIsImtpZCI6ImF0dGVzdC1rZXktMSJ9.eyJpc3MiOiJodHRwczovL2FnZW50cy5wdWJsaXNoZXIuZXhhbXBsZS9hdHRlc3RhdGlvbiIsImF1ZCI6InBsYXRmb3JtXzAwMSIsImFnZW50X2lkIjoiYWktYXNzaXN0LTAwMSIsImFjdGluZ19mb3IiOiJwdWJfMTIzIiwiaWF0IjoxNzgwMjcyMDAwLCJleHAiOjIwODAwMDAwMDB9.ndo1szT6NLNuZwaiEmvY4-4oxfrA_UUyuoFjTV_4hFzAfk9tsP4P9kcdEyLu1WsA3Qv6iw0pTa_YFMI_FELpDw"
        }
      },
      "attributes": {
        "vertical": "mortgage",
        "schema_version": "1.0.0",
        "loan_type": "refinance",
        "loan_amount_cents": 65000000,
        "loan_amount_band": "500k_750k",
        "property_type": "single_family",
        "property_value_cents": 88000000,
        "property_value_band": "800k_1m",
        "credit_rating_band": "excellent",
        "employment_status": "employed",
        "annual_income_cents": 12000000,
        "annual_income_band": "100k_150k",
        "ltv_ratio_band": "70_80",
        "is_first_home_buyer": false,
        "has_existing_mortgage": true
      },
      "exclusivity": {
        "exclusivity": "shared",
        "max_buyers": 3
      }
    }
  }
}
```

Verification against this example (executed — see Verification runs):
schema PASS under the published tree **and** under the §2 overlay; verifier
steps 1–12 PASS; the fixture JWK is

```json
{"kty": "OKP", "crv": "Ed25519", "x": "1bCirUUETlbD2Z3ZNcYH95IskGacAJdPgSxxLRswlbY", "kid": "attest-key-1", "alg": "EdDSA"}
```

with `iss = "https://agents.publisher.example/attestation"` (JWKS at
`iss + /.well-known/jwks.json`), claims
`{"iss": …, "aud": "platform_001", "agent_id": "ai-assist-001", "acting_for": "pub_123", "iat": 1780272000, "exp": 2080000000}`,
and the private key derived deterministically from the seed
`SHA-256("lep-0008-fixture-ed25519-seed")` — any reviewer regenerates every
token byte-for-byte (`cryptography` 50.0.0, `Ed25519PrivateKey.from_private_bytes`).
`exp` is deliberately far-future so the pinned example still verifies on
re-runs; the claims otherwise match production shape.

### Valid example — delegated agent sender (`act_for:` scope)

Same issuer token (claims unchanged), but submitted with the **agent's own**
sender credential `agent_pub_001`, whose credential registry entry carries
scopes `["lead:submit", "act_for:pub_123"]` (existing scope machinery,
`auth.py:187-189`). Verifier step 11 accepts via the scope; the same token
submitted by `agent_pub_001` **without** the scope fails `claims_mismatch`
(executed — Verification runs §4).

```json
{
  "lcp": {
    "version": "1.0.0",
    "message": {
      "id": "957d1a12-4320-5d42-bec0-ef620fa8f464",
      "type": "lead",
      "timestamp": "2026-11-03T09:00:00Z",
      "sender_id": "agent_pub_001",
      "receiver_id": "platform_001",
      "correlation_id": null,
      "idempotency_key": "lep0008-valid-delegated-lead-001"
    },
    "payload": {
      "lead_id": "lead_lep0008_delegated_001",
      "external_id": "pub-internal-456",
      "submitted_at": "2026-08-15T10:19:50Z",
      "status": "NEW",
      "channel": "form",
      "consumer": {
        "first_name": "Jane",
        "last_name": "Smith",
        "email": "jane.smith@example.com",
        "phone": "+61412345678",
        "locale": "en-AU"
      },
      "location": {
        "country_code": "AU",
        "state_region": "NSW",
        "postal_code": "2000",
        "city": "Sydney",
        "timezone": "Australia/Sydney"
      },
      "compliance": {
        "consent_timestamp": "2026-08-15T10:19:45Z",
        "consent_source_url": "https://publisher.example.com/landing/mortgage",
        "consent_text_version": "v2.1",
        "consent_purposes": [
          "calls",
          "sms",
          "email",
          "share_with_partners"
        ],
        "consent_evidence": [
          {
            "type": "verified_consent",
            "provider": "internal",
            "token_or_url": "evd_abc123def456"
          }
        ],
        "otp_verified": true,
        "dnc_checked": true,
        "scrubs": [
          {
            "type": "dnc_national",
            "provider": "au_dncregister",
            "result": "clean",
            "checked_at": "2026-08-15T10:19:48Z"
          },
          {
            "type": "litigator",
            "provider": "litigator_list",
            "result": "clean",
            "checked_at": "2026-08-15T10:19:49Z"
          },
          {
            "type": "blacklist",
            "provider": "internal",
            "result": "clean",
            "checked_at": "2026-08-15T10:19:49Z"
          }
        ]
      },
      "provenance": {
        "received_at": "2026-11-03T09:00:01Z",
        "source_type": "agent",
        "source_id": "agent_pub_001",
        "source_url": "https://publisher.example.com/landing/mortgage",
        "ip_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        "user_agent_hash": "f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5",
        "campaign_id": "camp_mortgage_nsw_2026",
        "creative_id": "cre_001",
        "landing_page_url": "https://publisher.example.com/landing/mortgage",
        "agent": {
          "agent_id": "ai-assist-001",
          "acting_for": "pub_123",
          "attestation": "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCIsImtpZCI6ImF0dGVzdC1rZXktMSJ9.eyJpc3MiOiJodHRwczovL2FnZW50cy5wdWJsaXNoZXIuZXhhbXBsZS9hdHRlc3RhdGlvbiIsImF1ZCI6InBsYXRmb3JtXzAwMSIsImFnZW50X2lkIjoiYWktYXNzaXN0LTAwMSIsImFjdGluZ19mb3IiOiJwdWJfMTIzIiwiaWF0IjoxNzgwMjcyMDAwLCJleHAiOjIwODAwMDAwMDB9.ndo1szT6NLNuZwaiEmvY4-4oxfrA_UUyuoFjTV_4hFzAfk9tsP4P9kcdEyLu1WsA3Qv6iw0pTa_YFMI_FELpDw"
        }
      },
      "attributes": {
        "vertical": "mortgage",
        "schema_version": "1.0.0",
        "loan_type": "refinance",
        "loan_amount_cents": 65000000,
        "loan_amount_band": "500k_750k",
        "property_type": "single_family",
        "property_value_cents": 88000000,
        "property_value_band": "800k_1m",
        "credit_rating_band": "excellent",
        "employment_status": "employed",
        "annual_income_cents": 12000000,
        "annual_income_band": "100k_150k",
        "ltv_ratio_band": "70_80",
        "is_first_home_buyer": false,
        "has_existing_mortgage": true
      },
      "exclusivity": {
        "exclusivity": "shared",
        "max_buyers": 3
      }
    }
  }
}
```

### Invalid example A — free-text attestation (the #192 row-1 forgery)

`provenance.agent` block as otherwise-valid `valid-a` but

```json
"agent": {
  "agent_id": "ai-assist-001",
  "acting_for": "pub_123",
  "attestation": "trust me, I am a real agent (free text)"
}
```

Today: `200 RECEIVED` (#192; re-executed at `974b9ca` — Verification runs
§1). Under this proposal: rejected at the **schema** layer —
`'trust me, I am a real agent (free text)' does not match '^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$' at provenance/agent/attestation`
→ `400 LCP-100` (executed under the overlay — Verification runs §3). §9
already declares free text non-conformant (`SPEC.md:929-930`); the pattern
only makes the schema see what the spec always said.

### Invalid example B — forged HS256 JWT (the #192 row-2 probe, verbatim)

```json
"agent": {
  "agent_id": "ai-assist-001",
  "acting_for": "pub_123",
  "attestation": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhZ2VudF9pZCI6ImFpLTEiLCJhY3RpbmdfZm9yIjoicHViXzEyMyIsImlzcyI6ImZha2UiLCJpYXQiOjE3ODAwMDAwMDAsImV4cCI6MTc4MDAwMDYwMH0.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
}
```

This is the exact token from #192's reproduction probe (`alg=HS256`, claims
`{iss:"fake", agent_id:"ai-1", …}`, `AAAA…` signature segment). Today:
`200 RECEIVED`. Under this proposal: passes the §2 shape pattern (the schema
cannot decode) → verifier step 3 rejects `algorithm` → `401 LCP-002`:

```json
{
  "errors": [{
    "code": "LCP-002",
    "name": "UNKNOWN_SENDER",
    "message": "Agent attestation failed verification",
    "field": "provenance.agent.attestation",
    "details": {"reason": "algorithm"}
  }],
  "request_id": "req-example"
}
```

(Even if MACs were allowed, step 7 would fail `signature` — the token fails
twice over.)

### Invalid example C — expired and claims-contradicting (the #192 row-3 probe)

The same fixture issuer's key re-signing `{"iss": "https://agents.publisher.example/attestation", "aud": "platform_001", "agent_id": "someone-else", "acting_for": "pub_123", "iat": 1780272000, "exp": 1000000000}` —
i.e. **validly signed**, expired (2001), and contradicting
`provenance.agent.agent_id = "ai-assist-001"`:

```json
"agent": {
  "agent_id": "ai-assist-001",
  "acting_for": "pub_123",
  "attestation": "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCIsImtpZCI6ImF0dGVzdC1rZXktMSJ9.eyJpc3MiOiJodHRwczovL2FnZW50cy5wdWJsaXNoZXIuZXhhbXBsZS9hdHRlc3RhdGlvbiIsImF1ZCI6InBsYXRmb3JtXzAwMSIsImFnZW50X2lkIjoic29tZW9uZS1lbHNlIiwiYWN0aW5nX2ZvciI6InB1Yl8xMjMiLCJpYXQiOjE3ODAyNzIwMDAsImV4cCI6MTAwMDAwMDAwMH0.2DRnJdD5R97PPnZl6JuUB4804VmohjUL4mSjN9qf8O08ELENuYXTNmJ8nlfpIAyUeNnJheFvwqhVwlXfjGuGBg"
}
```

Today: `200 RECEIVED`. Under this proposal: step 8 fires first →
`401 LCP-002`, `details.reason: "expired"` (deterministic axis order —
Verification runs §4 proves the claims contradiction would *also* fail at
step 10 but the procedure stops at the first failure).

### Invalid example D — cross-receiver replay

`valid-a`'s token re-issued with `"aud": "platform_002"` and submitted to
platform_001 (`receiver_id: "platform_001"`) — a token minted for one
platform replayed at another:

```json
"attestation": "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCIsImtpZCI6ImF0dGVzdC1rZXktMSJ9.eyJpc3MiOiJodHRwczovL2FnZW50cy5wdWJsaXNoZXIuZXhhbXBsZS9hdHRlc3RhdGlvbiIsImF1ZCI6InBsYXRmb3JtXzAwMiIsImFnZW50X2lkIjoiYWktYXNzaXN0LTAwMSIsImFjdGluZ19mb3IiOiJwdWJfMTIzIiwiaWF0IjoxNzgwMjcyMDAwLCJleHAiOjIwODAwMDAwMDB9.ZoMSbChBjYlDWZibe8h1r6hMu5yGpmk7F-7ubMht819Jq1luCcUdKBIrFsJOni4qjfgJuENWu916TKYEl_q2Cg"
```

Verifier step 9 → `401 LCP-002`, `details.reason: "audience"`. (The
attestation itself verifies cryptographically — this is exactly why an
audience binding, not signature-checking alone, is what stops cross-pair
replay.)

### Invalid example E — claims contradict the envelope

Validly signed, unexpired, correct `aud`, but `agent_id` = `"someone-else"`
against `provenance.agent.agent_id` = `"ai-assist-001"`:

```json
"attestation": "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCIsImtpZCI6ImF0dGVzdC1rZXktMSJ9.eyJpc3MiOiJodHRwczovL2FnZW50cy5wdWJsaXNoZXIuZXhhbXBsZS9hdHRlc3RhdGlvbiIsImF1ZCI6InBsYXRmb3JtXzAwMSIsImFnZW50X2lkIjoic29tZW9uZS1lbHNlIiwiYWN0aW5nX2ZvciI6InB1Yl8xMjMiLCJpYXQiOjE3ODAyNzIwMDAsImV4cCI6MjA4MDAwMDAwMH0.9XoyrxTJpiy27YcseTbyrl8pr_zN6VcdcWakQg9h7pK3RcuqEH5MokV-OPR3CUEUxyX-ccYRiYJI849uzC1mBA"
```

Step 10 → `401 LCP-002`, `details.reason: "claims_mismatch"`. Same shape for
an `acting_for`/sender contradiction without the `act_for:` scope (executed:
Verification runs §4, `valid-b-without-scope`).

### Invalid example F — `alg: none` detached signature

```json
"attestation": "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJpc3MiOiJodHRwczovL2FnZW50cy5wdWJsaXNoZXIuZXhhbXBsZS9hdHRlc3RhdGlvbiIsImF1ZCI6InBsYXRmb3JtXzAwMSIsImFnZW50X2lkIjoiYWktYXNzaXN0LTAwMSIsImFjdGluZ19mb3IiOiJwdWJfMTIzIiwiaWF0IjoxNzgwMjcyMDAwLCJleHAiOjIwODAwMDAwMDB9."
```

Empty third segment → fails the §2 schema pattern (`400 LCP-100`) before any
header parsing; if it somehow reached the verifier, step 3 rejects `none`.
Belt and braces (executed both layers — Verification runs §3/§4).

### Invalid example G — tampered signature under a compliant header

`valid-a`'s token with the final six signature characters replaced
(signature flips under an *allowed* `EdDSA` header):

```json
"attestation": "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCIsImtpZCI6ImF0dGVzdC1rZXktMSJ9.eyJpc3MiOiJodHRwczovL2FnZW50cy5wdWJsaXNoZXIuZXhhbXBsZS9hdHRlc3RhdGlvbiIsImF1ZCI6InBsYXRmb3JtXzAwMSIsImFnZW50X2lkIjoiYWktYXNzaXN0LTAwMSIsImFjdGluZ19mb3IiOiJwdWJfMTIzIiwiaWF0IjoxNzgwMjcyMDAwLCJleHAiOjIwODAwMDAwMDB9.ndo1szT6NLNuZwaiEmvY4-4oxfrA_UUyuoFjTV_4hFzAfk9tsP4P9kcdEyLu1WsA3Qv6iw0pTa_YFMI_AAAAAA"
```

Step 7 → `401 LCP-002`, `details.reason: "signature"` — the pure forgery case
distinct from the algorithm-confusion case in example B.

### Invalid behavior example H — unknown `kid` (rejected) vs unreachable JWKS (500)

Same token, issuer's JWKS fetched successfully but containing no
`attest-key-1`: step 6 → `401 LCP-002`, `key_unknown` — the sender's/issuer's
fixable state. Issuer unreachable at all (timeout): `500 LCP-500`,
`details.reason: "key_unavailable"` — the receiver's infrastructure answer,
retryable, never fail-open in enforce mode. Neither is schema-expressible;
both are asserted by named tests (V9/V10).

## Privacy, security, and universal-core audit

**PII boundary.** `provenance.agent.*` sits in the provenance object that §3
defines as non-PII (`SPEC.md:181`). The proposal adds no PII and adds two
MUST-NOTs: the claims set MUST NOT embed consumer identifiers or contact data
(anything verifiable cross-pair via `aud`/`iss` is a correlation vector), and
logs/telemetry MUST NOT retain full attestation values (a token is replayable
bearer-grade material until `exp` — record `iss`+`kid`+hash for diagnostics,
never the signature bytes; the lead store already persists the block under
§3's storage rules, `SPEC.md:938-945` PII posture applies unchanged since the
field is not PII).

**Abuse cases engaged:**

- *Forged agent provenance* (the #192 class): closed by verification +
  algorithm allowlist + asymmetric-only keys.
- *Cross-pair token transplant:* closed by `aud` + issuer-key binding
  (§7) — a token cannot be shown to a receiver it was not minted for, and
  other senders cannot mint issuer A's tokens.
- *Algorithm confusion:* closed by the allowlist derived from the resolved
  JWK (§3), not from attacker-chosen `alg` alone.
- *SSRF / fetch amplification via `iss`:* closed by auth-before-fetch,
  `validate_webhook_url` policy class, size/count/TTL bounds, and a
  2-second bounded fetch (§4).
- *Fail-open temptation:* prohibited — enforce mode MUST NOT substitute an
  accept when key resolution fails; the sanctioned soft path is the explicit
  observe phase, which logs but does not record a conformance claim.
- *Log-replay of captured tokens:* mitigated by the log-hygiene MUST-NOT;
  residual risk (store contents = replayable until `exp`) is bounded by
  short recommended lifetimes and `aud` binding, with `jti` single-use
  Deferred (its trigger names the day tokens get forwarded).

**Universal-core audit (§12, `SPEC.md:1021-1047`).** No new core field —
tightening the form of a shipped required string is a validation change, not
a namespace addition; `tools/check_reserved_fields.py` and
`tools/check_universal_core.py` gates listed under Verification runs pass
unchanged on this record (docs-only). The verification machinery lives at
platform/deployment level; agents stay out of core (§10 "never coupled into
the core", `SPEC.md:968-971`). Nothing executable crosses the wire (no
matching rules — decision rule honored); `act_for:` is a scope *string* in
the existing credential registry, not an expression.

**Trust-model security statement.** Verification moves the fraud-resistance
anchor from "the pair secret can sign anything" (envelope HMAC — true
today; a compromised pair secret produces perfectly-looking traffic) to
"an issuer-controlled key asserts the principal" — strictly better for
agent fraud, and *explicitly not* a repudiation guarantee against the
publisher itself (the publisher controls its issuer keys and could mint any
claim; the binding is per-issuer). The record refuses to overclaim this in
§9 text; stronger non-repudiation requires the deferred third-party trust
models.

## Compatibility, versioning, and deprecation impact

- **Semver:** MINOR (v1.1.0) — verification semantics + error-mapping text +
  one schema tightening; nothing additive is required of conformant senders
  (they already send verifiable tokens, per §9's MUST at `SPEC.md:929-930`)
  and nothing is removed. AGENTS.md rule (protocol-taxonomy/security changes
  bump MINOR/MAJOR) satisfied; `lcp_versions` advertisement already carries
  `1.1.0` on the reference platform (`validation.py:23`
  `SUPPORTED_LCP_VERSIONS`, advertised at `router.py:1738`).
- **The wire-visible break, stated exactly:** leads carrying free-text or
  unverifiable attestations that `200 RECEIVED` today will fail — at the
  schema layer with `LCP-100` (shape), at the verification layer with
  `LCP-002` (identity) or `LCP-500` (key availability) per the §8 mapping.
  Those submissions are **already non-conformant** under
  §9's published MUST ("Free-text attestations are non-conformant",
  `SPEC.md:929-930`); the change enforces published behavior, which is the
  LEP.md boundary between "a bug that restores the published behavior" and
  LEP material — it stays LEP material because the *mechanism* (codes, key
  distribution, timing) never existed, which #192 itself ruled.
- **Closed-object rules untouched:** no field is added to any closed object,
  so the §6 version-negotiation hazard (`SPEC.md:662-666`) never arises;
  v1.0 senders hitting v1.1 receivers send the same bytes (their junk simply
  fails, per the line above). v1.1 senders hitting v1.0 receivers: unaffected
  (v1.0 accepts any string — a v1.0 receiver under-verifies, exactly as today,
  and per §7 must not claim L3).
- **SDK/schema-sync:** core.json pattern changes the published schema bundle;
  generated SDK models are type-level (`string`) and unaffected in shape —
  manifests/SHA bundles regenerate per the traceability gate (LEP.md
  Implement items 3). No `schema_version` per-vertical change.
- **Vector compat:** `L3-001` keeps its presence/shape assertion but its
  token is re-issued (it cannot verify under any key — decoded evidence in
  Problem statement); a runner-level `attestation_present` check remains
  meaningful, verification checks are new vectors (V8/V11).
- **Deprecation:** nothing deprecated; the `issuer` claim name in the §9
  table is a *documentation correction* (no shipped producer uses JWT
  libraries that emit it verifiably — it is not even a registered claim; the
  table's own recommendation posture (`SPEC.md:918` "recommended format")
  gives no compatibility floor to preserve).
- **No silent L3 advertisement during development:** Phase 0 (Rollout) makes
  the advertisement honest *before* the MINOR lands — flagged as the one
  maintainer-ratifiable decision adjacent to the wire.

## SDK, OpenAPI, MCP, conformance, and operational impact

| Surface | Impact at implementation |
|---|---|
| `schemas/core.json` | §2 pattern + `maxLength` on `attestation` (`core.json:133`); regenerated bundles + manifests per traceability gate. |
| SPEC text | §9 rewrite (claim table incl. `iss`/`aud`, allowlist, procedure, timing); §5 identity-axis clause (`SPEC.md:638-641`); §7/§8 advertisement-honesty sentence (§9 of this record). |
| Reference platform | new `attestation.py` verification module + resolver; `ingest()` step between `router.py:294-298` and `router.py:359`; mode config (`observe`/`enforce`, default `enforce` at ship); Phase 0 advertisement line (`router.py:1769`). |
| OpenAPI | capabilities shape unchanged (`api/lcp-openapi.yaml:839` already documents `conformance_level`); intake error examples gain the `details.reason` variants — documentation-level. |
| MCP | **zero logic** — HTTP client path inherits enforcement (`client.py:67`); `test_gap_mcp_surface.py` byte-for-byte assertions still pass (the adapter never normalizes); its stub-platform fixture's `"L3"` (line 128) is test-double data, not a platform claim, but the V12 pin ensures a real-platform advertisement change lands with matching docs. |
| SDKs (all languages, per implementations/sdk/) | signer/verify helper for the §2/§6 form (builders already accept an arbitrary string — unchanged); canonical NumericDate emission mirrors LEP-0002's whole-second discipline (`SPEC.md:832-845`); no transport change. |
| Vectors + runner | `test-vectors/attestation/fixture-jwks.json` (the record's pinned JWK + seed derivation documented); positive `lcp-pos-attestation-verified-001` and negatives `-forged-`, `-expired-`, `-audience-`, `-claims-mismatch-`, `-free-text-`, `-alg-none-` (platform-tier; schema-tier negatives via existing expect-fail form with `error_code` fields, precedent `l1-envelope.json:93`); L3-001 token re-issued. |
| Coverage matrix | `SEC-09` flips `gap → green` only with the verification tests shipping (V1–V10); `CNF-03` note updated to name attestation verification tests, not shape-only (`docs/SPEC-COVERAGE.md:165`). |

**Named implementation-phase tests (the gate list, T-style per LEP-0003):**

| Gate | Obligation |
|---|---|
| V1 | `plat: test_attestation_free_text_rejected` — invalid A → `400 LCP-100`, field pointer. |
| V2 | `plat: test_attestation_forged_hs256_rejected` — invalid B → `401 LCP-002`, `reason=algorithm`. |
| V3 | `plat: test_attestation_expired_first_axis` — invalid C → `expired` (axis-order determinism). |
| V4 | `plat: test_attestation_cross_receiver_rejected` — invalid D → `audience`. |
| V5 | `plat: test_attestation_claims_mismatch_rejected` — invalid E both axes. |
| V6 | `plat: test_attestation_alg_none_and_detached_rejected` — invalid F, schema layer. |
| V7 | `plat: test_attestation_tampered_signature_rejected` — invalid G → `signature`; valid-a accepted end-to-end (fixture resolver). |
| V8 | vectors: L3-001 re-issued verifiable under the fixture key + `attestation_present` check retained; positive/negative attestation vectors land and the runner consumes them. |
| V9 | `plat: test_attestation_unknown_kid_rejected` (`key_unknown`) and `plat: test_act_for_scope_delegation` (valid-b accepted; same token + unscoped sender → `claims_mismatch`). |
| V10 | `plat: test_attestation_jwks_unavailable_fails_closed` — unreachable issuer → `500 key_unavailable`, **zero** lead rows; observe-mode logs only. |
| V11 | `tool: test_lep_wire_examples` continues to pass on this record's `### Valid example` envelopes against the published tree (it does, today — Verification runs §5), and a `checks`-driven attestation case enters the vector lane. |
| V12 | `plat: test_capabilities_conformance_level_matches_behavior` — advertisement honesty pinned (§9 of this record); Phase 0 lands it first. |

**Operational:** metrics per ingest attempt `{verification outcome × reason}`
plus issuer-fetch latency/error counters; audit rows reuse
`store.insert_audit` (`router.py` pattern) with reason metadata (never the
token bytes); dashboards/alerts on `key_unavailable` rate (issuer outage is
a *visible* failure now, which is the point of LCP-500 vs today's silent
accept).

## Alternatives considered

Trust models (candidate set from #192's "key distribution undefined"):

| Candidate | Verdict | Reasoning |
|---|---|---|
| **A. Issuer-published JWKS (DID-URL/HTTPS `iss`)** | **RECOMMENDED — proposed** | Matches §9's `issuer` claim and "JWKS or pinned public key" text; per-issuer revocation via `kid` rotation; models one agent authority across many publishers (§10 roles); zero new protocol surface (URL rules + existing policy class). Costs: cache/SSRF/timeout machinery (bounded above), issuer-availability dependency (fail-closed + observe rollout). |
| **B. Pre-registered agent public keys per sender (sender-scoped registry)** | Rejected as canonical; kept as **deployment pin fallback** (§4) | Would need a new registration surface (key upload/API/schema — none ships today; #192's own grep evidence), cannot express third-party agent authorities shared across publishers, and makes `iss` decorative — spec text already anchors trust to the issuer. Deterministic, no network fetch, and the right shape for closed bilateral pairs, hence the config-pin form. |
| **C. Per-pair symmetric attestation (reuse pair secrets)** | **Rejected** | Indistinguishable from the envelope HMAC (which already signs the *whole request* under that same secret, §1): every verifier can mint; zero additional principal binding; MAC allowlist ban (§3) makes forgery detection trivial instead. Adds a signature nobody can repudiate. |
| **D. Trusted third-party / PKI / verifiable credentials** | Out of scope; Deferred with trigger | No PKI exists in this ecosystem; chain validation dwarfs the L3 need today. |
| **E. Keep advertising L3 while verification is absent** | **Rejected** | §7 says L3 *includes* attestation verification (`SPEC.md:691-692`); the advertisement is a conformance claim, currently false. #192's alternative ("normative spec note that verification is a deployment-layer concern") is variant F below. |
| **F. Dilute §9: "verification is a deployment-layer concern" spec note** | Rejected | Converts the protocol's only fraud-resistance requirement for agent traffic into an option and redefines §7 by footnote — decision-rule territory ("changes that make a v1 receiver fail…" is not the axis; this silently lowers the tier semantics). The *deployment* already chooses keys per §9's phone-hash precedent of keeping distribution bilateral — *verification* is what makes L3 the L3. |
| **G. Mint a new §5 code (e.g. LCP-014 `ATTESTATION_INVALID`)** | Rejected | §5's identity axis covers it with one clarifying clause, on the `err-auth-triage` precedent and shipped LCP-002 intake-binding precedents (`router.py:277-293`); the taxonomy is closed and its bloat is a compatibility cost paid for nothing. |
| **H. Behavior unchanged; fix only the coverage matrix** (mark SEC-09 exempt) | Rejected | Records the gap as intended behavior — institutionalizes the L3 misadvertisement #192 filed. |

Also considered and folded in: `jti` replay cache (part of A, refused —
rationale §7), `act_for:` scope machinery reuse vs new delegation wire field
(scope chosen; the registry *interface* is Deferred), string-claim
vocabulary compat shims (refused — unverifiable under any key today, so no
producer loses anything).

Keeping the whole behavior deployment-specific was weighed per the LEP.md
decision rule and fails for the same reason as E/F: `conformance_level` is a
protocol-level claim; a deployment MAY still choose stricter local policy on
top of the floor.

## Rollout, migration, observability, and rollback

Staged; each phase is independently shippable and observable. The phases
are the answer to #192's warning that "rejection changes published
behavior" — they sequence *who* is inconsistent *when*.

**Phase 0 — advertisement honesty (bug-class PR, lands BEFORE this MINOR;
the one decision in this record adjacent to wire-visible output that the
maintainer must ratify early).** One-line reference-implementation change:
`conformance_level: "L3"` → `"L2"` at `router.py:1769`, plus V12's pin test
and a `CNF-03`/`SEC-09` note update. It **restores published behavior**
(§7 already excludes non-verifying endpoints from L3; `SPEC.md:691-692`) —
the LEP.md rule that bug PRs may bypass the LEP path applies to *this step
alone*, and the record flags it explicitly because the maintainer, not this
lane, makes that boundary call. No adopter can be harmed by a *more* honest
capability string under §8's parsed-semver comparison (nothing in the spec
keys behavior off L3). MCP/SDK test fixtures pinning the literal (the stub
dict at `test_gap_mcp_surface.py:128`) are test-double data and stay green
(verified in the battery run of the tree as-is; the adapter passes through
whatever the platform returns).

**Phase 1 — wire (this record, post-Acceptance):** SPEC §5/§7/§8/§9 text;
core.json form tightening; vectors + fixture JWKS; SDK helpers;
implementation PRs link this LEP + registry row per the traceability gate;
the tightening's CI-visible effect is confined to non-conformant fixtures
(L3-001 re-issued).

**Phase 2 — observe mode (deployable before flipping enforcement):**
verification runs everywhere in enforce-capable deployments with
`mode=observe`: every §5 procedure outcome is recorded
(`{outcome, reason, iss_host, kid}` — never the token), leads still accept,
zero new rejections on the wire. This phase answers the question this Draft
cannot: *how much accepted traffic actually carries verifiable tokens today?*
Field counts on observe mode (and adopter reports solicited on #205) are the
evidence; the unit is the ingest attempt, and the exit criterion is the
reason-mix being dominated by `format`/`algorithm` (i.e. junk, not honest
senders mid-migration) with the `expired`/`signature` classes at noise
levels for an agreed interval (deployments set; suggested review point: 2
weeks).

**Phase 3 — enforce:** `mode=enforce` becomes the conformance-relevant
default (spec-level: verification is mandatory at §9's MUST; a deployment
running observe mode is *not* L3-honest and must not advertise L3 — same
rule as Phase 0). Per-sender grandfather windows are deliberately **not**
proposed: §9 already said free text is non-conformant since v1.0, and
soft-per-sender exceptions are exactly the "vendor-specific defaults" LEP.md
exists to prevent. A deployment that needs migration grace stays in Phase 2.

**Observability (all phases):** counters per §5-reason; issuer-fetch
latency/error gauges; `LCP-500` rate alert (issuer outage), audit rows on
verification failure with reason; no token bodies in any of the above
(privacy audit).

**Rollback:** Phase 3→2 (mode flag) is the primary lever — accept behavior
returns byte-identical to today's wire while verification continues to
record. Phase 2→1: verification disabled entirely (flag), schemas keep the
pattern (free-text senders still break at the schema line — the tightening
and the verifier are separable but the pattern was already implied by §9;
a full schema rollback is redeploying the prior bundle, no data migration
exists). Phase 0 is never rolled back (an honest advertisement is not a
regression). The reference `lcp_versions` advertisement is unaffected by
rollback direction — nothing gates v1.1 fields on this feature, so no
negotiation trap opens mid-rollout (unlike the LEP-0003 slot fields).

**Migration (senders):** conformant senders do nothing (they already mint
verifiable tokens); non-conformant senders adopt an issuer key + mint per
§2/§6 — the SDK helper is the on-ramp; observe-mode counters tell each
publisher exactly which of their agent stacks fail which axis, before a
single request starts rejecting.

## Deferred questions

Each with its revisit trigger — none blocks this proposal:

1. **Buyer-side (agent-as-consumer) attestation.** Today verification
   binds the *submission* direction only; a buyer's agent accepting a
   delivered post acts under the buyer's receiver-side credentials, and the
   submission attestation MUST NOT be treated as buyer-side authority
   (direction rule, §7). *Trigger:* any platform ships agent-consumer
   acceptance with delegation needs (marketplace lane, LEP-0006 surfaces)
   or a dispute turns on "which buyer agent accepted".
2. **`jti` single-use replay cache.** Refused at v1 as unbounded state
   defending a threat already closed by `aud` + envelope HMAC + idempotency
   (§7). *Trigger:* evidence of captured-token replay within `exp`, or the
   attestation ever being forwarded beyond the receiver (then reuse becomes
   the security boundary).
3. **Forwarding attestations downstream** (platform → buyer posts with the
   agent provenance block): currently out of scope (provenance rides the
   lead where a deployment chooses); forwarding changes the replay calculus
   (item 2) and the log-hygiene surface. *Trigger:* a buyer requests
   verifiable provenance without contacting the platform.
4. **DID / cross-platform trust lists / third-party issuer registries**
   (Alternatives D): *Trigger:* agent identity portability across platforms
   becomes an adopter ask, or issuer-key compromise blast radius proves
   insufficiently local.
5. **`act_for:` delegation as a registered interface** (discovery,
   multi-tenant scope hygiene, delegation chains): today an out-of-band
   registry string. *Trigger:* second adopter builds agent-scoped senders
   (evidence solicited on #205) or chains-of-agents appear.
6. **RSA (`RS256`) expansion** (§3 allowlist): *Trigger:* a production
   issuer demonstrably without Ed25519/P-256 keys.
7. **Normativizing a maximum token lifetime** (recommendation 900 s today):
   *Trigger:* Phase 2 field data on real lifetimes.
8. **A §8 capability flag for enforcement mode** (`observe` vs `enforce`):
   refused at Draft because senders gating on it would normalize soft mode;
   *Trigger:* interop evidence that mode-gating saves a migration — at which
   point the L3 advertisement rule (§9) needs re-review too.
9. **`iat`/`nbf` freshness enforcement** (short-validity pressure on
   honest-but-lazy issuers): *Trigger:* Phase 2 data showing long-lived
   tokens dominating real traffic.

## Verification runs (executed 2026-11-03)

Environment: repository venv (`python 3.12.12`), `jsonschema 4.26.0` +
`cryptography 50.0.0` (both per `requirements-ci.txt`), worktree at
`974b9ca80fc50a128c9bc24e4d5a5c950d080cd3`. Battery:
`/tmp/lep0008-battery.py` (scratch, not committed); re-extraction run replays
the exact bytes **from this file** back through the validators/verifier.

**1. #192 reproduction at this SHA (published behavior — evidence, not the
proposal).** In-memory platform, `require_auth=True`, `test_mode=True`,
sender `pub_123`, HMAC-signed `POST /v1/lcp/leads` (canonical headers, fresh
uuid5 `message.id` per case), agent provenance with three attestation values
(free text / #192's forged HS256 token verbatim / expired-and-mismatched):

```text
repro[repro-freetext]          -> HTTP 200 ack=RECEIVED
repro[repro-forged192]         -> HTTP 200 ack=RECEIVED
repro[repro-expired-mismatch]  -> HTTP 200 ack=RECEIVED
```

The L3 advertisement line was confirmed at `router.py:1769`; no code was
changed for this reproduction.

**2. Fixture materialization (reproducible).** `seed =
SHA-256("lep-0008-fixture-ed25519-seed")` → Ed25519 keypair → JWK
`{"kty":"OKP","crv":"Ed25519","x":"1bCirUUETlbD2Z3ZNcYH95IskGacAJdPgSxxLRswlbY","kid":"attest-key-1","alg":"EdDSA"}`;
all record tokens are `JWS(seed-key, header, claims)` exactly as §Valid
example states. Token strings appear in the record verbatim (base64url,
three segments).

**3. Schema battery — published vs §2 overlay.** Overlay = copy of
`schemas/` with core.json's `attestation` gaining the §2 pattern +
`maxLength: 8192`; both trees run through the repository's own
`lcp_platform.validation.SchemaValidator` (the T1 lane's validator). The
record's two `### Valid example` envelopes and the invalid-A/F shapes:

```text
schema[valid-a]          published=PASS   overlay=PASS
schema[valid-b]          published=PASS   overlay=PASS
schema[invalid-freetext] published=PASS   overlay=REJ "'trust me, I am a real agent (free text)' does not match '^[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+$' at provenance/agent/attestation"
schema[invalid-none]     published=PASS   overlay=REJ (same pattern, empty third segment)
```

The `invalid-*` `published=PASS` results **are the gap** (the published tree
accepts free text — #192 restated at schema level); under the proposal they
return `400 LCP-100` through `router.py:1910-1917`.

**4. Reference-verifier battery (implements §5 steps 1–12 as drafted).**
Tokens signed with the fixture key; receiver = `platform_001`, sender
scopes as named:

```text
verifier[valid-a (sender==acting_for)]                  PASS
verifier[valid-b (delegated sender w/ act_for scope)]   PASS
verifier[valid-b-without-scope]                         REJ reason=claims_mismatch
verifier[freetext]                                      REJ reason=format
verifier[forged-192 HS256]                              REJ reason=algorithm
verifier[expired]                                       REJ reason=expired
verifier[wrong-aud (cross-receiver replay)]             REJ reason=audience
verifier[claims-mismatch (agent_id)]                    REJ reason=claims_mismatch
verifier[unknown-kid]                                   REJ reason=key_unknown
verifier[http-iss]                                      REJ reason=issuer_policy
verifier[alg-none detached]                             REJ reason=format
verifier[tampered-signature]                            REJ reason=signature
verifier[jwks-unreachable]                              500 key_unavailable (fail-closed, no accept path)
verifier[expired+mismatch (#192 row-3)]                 REJ reason=expired   ← first-failing-axis order proven
```

Every invalid example A–G in this record maps to exactly one executed line
above (A: format×2 layers; B: algorithm; C: expired; D: audience; E:
claims_mismatch; F: format; G: signature; H: key_unknown / 500).

**5. Baseline repository gates.** After this file lands on its branch:
`tools/check_lep_registry.py` (numbering/links/status/sections — the new
registry row and this record), `tools/check_doc_citations.py --strict`
(every `path:line` cite above verified against `974b9ca`; baseline on main is
`0 broken`), `tools/check_reserved_fields.py`,
`tools/check_universal_core.py`, `test-vectors/conformance.py` (unchanged —
no schema edit lands at Draft), and `tools/tests/test_lep_wire_examples.py`
(T1 gate: both `### Valid example` envelopes validate through the *published*
tree today; the re-extraction run in §3 executed exactly what the gate will
re-run in CI). Results are recorded in the draft PR's CI.

**Recording-honesty notes (self-checked):** this is a Draft; nothing above
proposes new §5 codes (mapping table reuses LCP-100/002/500 on shipped
precedents), ships no runtime, and the two `### Valid example` envelopes are
byte-identical to the executed battery's JSON — the record was written with
placeholders, the exact bytes injected from the battery output, and then a
second **re-extraction run (2026-11-03) re-parsed this file itself** and
replayed every claim against the committed bytes: both Valid envelopes
`PASS` the published **and** overlay trees; invalid A/F `REJ` the overlay
(exact schema error text quoted above); the reference verifier re-run against
the extracted tokens reproduced every recorded outcome line-for-line
(valid-a `PASS`, valid-b `PASS`, without-scope `REJ claims_mismatch`, B
`algorithm`, C `expired`, D `audience`, E `claims_mismatch`, G `signature`).
Equality assertions pinned: injected tokens equal the first battery's stored
bytes, and #192's forged token decodes to the claims recorded in example B.
Example tokens carry no PII and no production key material (fixture seed
published). The claim-table change (`issuer`→`iss`) is stated as a
correction, not a silent spec edit; the only wire-adjacent item requiring an
early maintainer decision is Phase 0, flagged in the rollout section.
