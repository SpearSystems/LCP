# LEP-0008 — Agent attestation verification: making §9's L3 real (closes #192)

- **Title:** Agent attestation verification — issuer-anchored JWS verification at intake, §5 mapping, and staged L3 advertisement honesty
- **Author(s):** LCP maintainers (draft for adopter review)
- **Date:** 2026-11-03
- **Status:** **Implemented** — accepted 2026-11-03; implemented (#213 verifier, #215 schema/SPEC + mode-derived L3)
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
## Decision (maintainer, 2026-11-03)

Implementation: PR #213 (13-step §5 verifier, issuer-JWKS resolver, kid-norm A, observe/enforce) and PR #215 (SPEC §9 normative text, `schemas/core.json` token form, corpus sdk-024–028, `conformance_level` follows enforcement).

**Accepted.** Basis: two independent dossier rounds on issue #205 — the
first found three majors (a half-guarded egress path, undecided decode-time
claim validation, and an unspecified ES256 signature encoding), the second
re-attacked the revision and found five minors (sandbox-mode egress escape,
nested/exponent/typ parser forks, an anchor drift); all were folded in-record
(PRs #209/#210). Decision parameters, both chosen deliberately:

- **F5 duplicate-`kid` norm: option A** — a document carrying two JWKs with
  the same `kid` is invalid, and verification fails `key_unknown` (401).
  Fail-closed and deterministic; option B (first-wins plus a MUST-warn)
  leaves the outcome implementation-dependent, which is the class of defect
  the rounds were written to eliminate.
- **Phase 0 ratified:** the reference platform MUST advertise
  `conformance_level: "L2"` until enforce-mode verification is live and green
  in CI, and MUST NOT advertise L3 on the strength of schema presence.
  This is executed **ahead of implementation** as a bug-class honesty fix —
  the current `"L3"` advertisement (`router.py:1769`) is a false claim while
  forged and free-text attestations ingest `200` (reproduced at every audited
  SHA).

Conditions carried to implementation: the V13–V24 gates land as named tests
(including the zero-dereference header policy and P1363-only ES256); L3 is
re-advertised only when enforce-mode verification is green; the observe mode
ships configurable with `enforce` the default at ship.

**Independence note (recorded, not glossed):** both dossier rounds were
maintainer-controlled SELF-AUDIT runs under the ARB charter's rule 2, which
does not satisfy independent review on its own. The maintainer's co-sign
recorded here is the record's independent-review anchor for this
PII/authentication surface; the charter's dissent path stays open — an
adopter re-run of the published harness at `6926246` that contradicts any
claim above blocks this decision until adjudicated.

Public window waived by the maintainer's recorded decision; #205 stays open.

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
adapter. #192 quoted `grep -rn "jwks|jwt.decode|verify_attestation"
implementations/` as "matches only vendored dependency copies"; re-executed
at `0529702` the same grep returns **zero** matches — vendored copies were
pruned since #192, so the current state is stronger than the quoted
evidence, not weaker (revision F9; the claim of record is "no verification
surface exists", which both grep generations prove).

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
  pinned after the auth/rate-limit block and the dedup early-return paths
  (`router.py:279-295`, `router.py:297-318`) and before `insert_lead`
  (`router.py:359`) — §5 placement, revision F6a; an issuer-JWKS resolver
  carrying the **paired** egress controls as shipped on the delivery path
  (`validate_webhook_url` + resolve-aware `validate_egress_host`,
  `security.py:16`/`security.py:33`, `follow_redirects=False`,
  `trust_env=False` — pattern at `router.py:1019-1028`, revision F1;
  precedent mapping `SecurityPolicyError`→`LCP-100` at `router.py:243-246`),
  and the staged rollout modes (observe/enforce) plus the Phase 0
  advertisement gate (see Rollout).
- **SDK / OpenAPI / MCP / vectors:** impact list below (test names V1–V24);
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
segments are decodable by parsers but MUST NOT be generated, mirroring the
LEP-0002 canonical-generation discipline at `SPEC.md:832-845`). Precedence
is pinned: the §2 schema pattern excludes `=`, so a receiver rejects any
padded token at the **schema layer** (`400 LCP-100`) before the verifier
runs — "decodable by parsers" describes generator discipline and the base64
libraries honest producers use, not receiver leniency (revision F6b;
`vec: lcp-neg-attestation-padded-100`).

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

Receiver-side header policy (re-dossier R4; decided at §5 step 2, the shape
step — generation-side `typ` stays a SHOULD, unchanged): `typ` is OPTIONAL on
receipt and MUST NOT decide a verification outcome — its value never admits a
token, and a non-`JWT` value (`at+jwt`, RFC 8725-style) MUST NOT reject one
(JWT and `at+jwt` variants of identical claims verify identically). §6's
claims analogue ("others … Ignored") is hereby mirrored to headers: receivers
MUST ignore header members outside `alg`/`kid`/`typ`, and MUST NOT
dereference URL-bearing members (`x5u`, `x5c`, `jku`) — §5 step 7's
`iss → JWK[kid]` resolution (or a configured pin) is the *only* key-resolution
path. `crit` is the one member that MUST NOT be ignored: this profile defines
no extensions, so any `crit` header names an extension the receiver cannot
understand — unknown crit-adjacent members are a `format` failure at step 2,
not silence.

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
from the resolved JWK's `alg` when present (mismatch → `algorithm`). The
allowlist test is an **exact, case-sensitive string match** performed at §5
step 3, before any key lookup: no trimming, case-folding, aliasing, or
post-lookup relaxation — `"edDSA"`, `"EdDSA "`, or `"ES256"`-shaped-with-
whitespace are `algorithm` failures, and a token that fails step 3 MUST NOT
cause issuer resolution (§4) to run.

**Signature encoding (normative; revision F3).** An EC signature is the
IEEE P1363 fixed-width `r ‖ s` concatenation per RFC 7518 §3.4: for ES256
each of `r` and `s` is the field-element magnitude left-padded with zeros to
32 octets, big-endian — the signature is **exactly 64 bytes**. DER
(`ECDSA-Sig-Value` SEQUENCE) and any other variable-width encoding MUST NOT
be accepted: a receiver that accepts DER and one that accepts only JWS
canonical bytes reject each other's valid tokens (70–72-octet DER —
length depends on the top bits of `r`/`s` — vs a fixed 64-octet `r ‖ s` for
the same signature; both observed, see Revision battery), which
breaks the determinism this record sells. Width is checked before the
cryptographic verify (step 8 precheck → `format`); a correct-width buffer
that fails the math fails `signature`. EdDSA signatures are likewise
fixed-width (64 bytes, `crypto_sign` output; no alternative encoding
exists). Implementation-phase gates: `plat: test_attestation_es256_der_rejected`
(V17) and an ES256 positive vector in the V8 family (V8 amended below);
fixture note: the vector lane ships an ES256 fixture keypair derived
deterministically from a published seed — private scalar
`d = int.from_bytes(SHA-256("lep-0008-fixture-p256-seed"), "big")` on P-256
(valid scalar; pinned `kid` `attest-es256-1`) — mirroring the reproducible-
from-seed property of the Ed25519 fixture above; the implementation PR
publishes the resulting public JWK, and reviewers re-derive every ES256
vector byte-for-byte as they do the Ed25519 ones (executed in the Revision
battery).

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
| Resolution | Fetch over TLS 1.3 (§9 Transport, `SPEC.md:795-797`); MUST NOT fetch before the request is transport-authenticated (unauthenticated callers get LCP-012/LCP-002 first — no fetch amplification). Egress is guarded by the **paired controls exactly as shipped on the delivery path** (`router.py:1019-1028`) — the pre-resolution string check alone is NOT the guard: every JWKS fetch MUST (a) pass `validate_webhook_url` (`security.py:16`) tightened to **HTTPS-only** (the shipped class permits `http` under `allow_insecure_webhooks`; attestation resolution MUST NOT, in any mode) with no user-info in the URL (already enforced: "must not contain embedded credentials", `security.py:20-21`); (b) pass `validate_egress_host` (`security.py:33`) — the **resolve-aware** leg, **in every deployment mode**: the attestation fetch MUST NOT inherit the shipped class's `allow_insecure_webhooks` skip (`security.py:42-43` returns before `getaddrinfo`; `security.py:66-67` short-circuits the predicate block), so no insecure-webhook escape hatch applies to a JWKS fetch, `test_mode` sandbox deployments included (`config.py:86` — the flag is reachable only there; re-dossier R1): `getaddrinfo` the hostname and reject `is_private`/`is_loopback`/`is_link_local`/`is_multicast`/`is_reserved`/`is_unspecified` **post-resolution** (`security.py:44-54`, `66-74`), which is what kills the obfuscated-literal class the string check waves through (`https://2130706433`, `https://0x7f000001`, `https://127.1`, `https://localhost` all map to 127.0.0.1 at `getaddrinfo` and pass `validate_webhook_url` — executed, Revision battery); (c) `follow_redirects=False` — a 3xx from a policy-valid issuer is NOT followed (revalidation of a final URL is NOT an acceptable alternative at v1; the shipped delivery client is the precedent); and (d) `trust_env=False` — no `HTTP(S)_PROXY`/env interception of the fetch. These four are MUSTs on **every outbound fetch attempted on the verification path** — the JWKS dereference *by verifiers* included, not just "the JWKS fetch" as a service routine — including negative-cache retries (re-dossier R4: header bytes are attacker-chosen, so any egress an implementation might otherwise trigger from them, e.g. a library-side `x5u` dereference, inherits this guard; the §2 header-member rule forbids that dereference outright, so the paired controls guard the one path that legitimately exists). Residual DNS-rebinding TOCTOU (resolve at check ≠ resolve at connect) is named, not claimed closed: connection-pool reuse and re-resolve hardening are implementation-PR concerns; the no-redirect/no-proxy legs remove the two same-process escalation paths. Policy-invalid `iss` → rejection reason `issuer_policy` (a sender-fixable identity failure, §8). |
| Selection | `kid` (required, §2) selects the JWK; `kty`/`crv`/`alg` of the selected JWK MUST be consistent with the header `alg` (§3 allowlist). No key found for `kid` in a successfully fetched **valid** document → `key_unknown`. **Duplicate `kid` entries.** The `keys` array MAY be malformed — two JWKs carrying the same `kid` give first-wins and last-wins implementations *opposite* answers for the same token (determinism broken across conformant receivers; dossier F5). A norm is required; two conformant resolutions exist and this record refuses to silently pick: **(A)** a document containing two JWKs with the same `kid` is an **invalid document** → routed with the Bounds row: fetched-but-invalid → `LCP-002`/401 `key_unknown` — this also covers the `use`/`key_ops` confusion class (a `use:"enc"` key selected for verification is likewise an invalid-document case); **(B)** selection is **pinned to first-match** in document order and the receiver MUST log a `duplicate_kid` warning — deterministic, more forgiving of honest issuer mistakes, but keeps serving tokens under a document the issuer shipped broken. Both options carry the same named-shape gate (`plat: test_attestation_jwks_duplicate_kid_rejected` asserts A; under B it becomes `…_first_match_pinned`). **Marked for the maintainer at Accept** (design decision, not a text fix); the reference implementation and Revision battery validate **A** (the stricter, closed-vocabulary-consistent option). |
| Bounds | JWKS document ≤ 16 KiB, ≤ 32 keys (format sanity, mirroring the LEP-0003 32-entry report bound); token ≤ 8192 bytes (§2 `maxLength`). **Outcome attribution (revision F4, §8 blame rule):** the bounds above are *format sanity*, not availability — a successfully fetched document that is oversized, key-count-exceeding, non-JSON, or otherwise invalid is a **sender/issuer-fixable** state and MUST fail the token: `LCP-002`/401 `key_unknown` (closed vocabulary preserved; a dedicated `jwks_oversize`-style reason was considered and rejected as vocabulary bloat for a state already named). `LCP-500`/500 `key_unavailable` is reserved for **availability** failure only: DNS/connect error, timeout, or a **5xx** response from the issuer (the server itself failed). A **4xx** (e.g. `404` on the `/.well-known/jwks.json` path) is the issuer's *configuration* — routed as fetched-but-invalid: `key_unknown`/401. An issuer serving garbage or pointing at a missing path must not convert its own fixable state into receiver-class 500s on every verified sender's request. |
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

Runs **once per newly accepted lead**, at a **pinned intake slot**: after
`auth.authenticate` and the per-sender rate limit (`router.py:279-295`,
LCP-011 at `router.py:295`) and after the idempotency dedup lookup
(`router.py:297`), but **before** `insert_lead` (`router.py:359`) — the same
ordering principle as §9's "Receivers MUST reject requests outside the
acceptable timestamp window before processing PII" (`SPEC.md:826-827`): a
failed verification MUST NOT persist PII. Placement relative to the two
early-return paths in the dedup block is normative: the LCP-005 mismatch
409 (`router.py:304-308`) and the DUPLICATE ack (`router.py:318`) both
short-circuit **before** this procedure runs, so a DUPLICATE ack never
re-verifies and never fetches keys (the bytes were verified at the original
accept, whose verification gated the row that now produces the ack); an
idempotency-key collision likewise fails on the idempotency axis without
touching the issuer. Verification happens exactly once per lead, at its
first acceptance attempt.
`source_type != "agent"` or an absent `provenance.agent` block → the
procedure does not run (presence semantics unchanged, `core.json:135`).

Steps 2–5 are **decode-time** steps: they run on parsed structure
and MUST complete before any key lookup, network fetch, or cryptographic
evaluation — no implementation may let a signature-verified token smuggle
a malformed claim set into the signature-time evaluation of steps 9–12, and
no implementation may do cryptographic work for a token that will fail on
shape alone.

```text
1  size ≤ 8192 and exactly 3 non-empty base64url segments  else FAIL format    (schema layer normally; a
   — non-compact serializations (JWS JSON, 1/2/4 segments, padded) are format failures here or at the schema)
2  header & claims parse as strict UTF-8 JSON objects;     else FAIL format
   duplicate member names REJECT AT PARSE — in EVERY
   JSON object of either serialization, at any nesting
   depth (parser rule below) — no last-wins fallback permitted
   §2's receiver-side header policy (`crit`/`typ`/unknown
   members) is likewise evaluated at this step (re-dossier R4)
3  header.alg exact-match in {EdDSA, ES256}               else FAIL algorithm
   (case-sensitive, no trimming/aliasing; checked BEFORE
   any key lookup — §4 resolution is never reached)
4  all §6 MUST claims present with declared types:        else FAIL format
   iss/aud/agent_id/acting_for non-empty strings;
   iat/exp present, integer NumericDate, > 0 (string or
   float or zero/absent timestamps → format, with the
   type step named; never coerced, never ignored).
   NumericDate is judged on the claims BYTES, not the
   parsed value: a base-10 integer literal, no fraction
   and no exponent part — `"exp":1e9`/`"exp":2e9` are
   `format` here even though IEEE-double parsers read
   `2e9` as integer-valued (re-dossier R3)
5  header.kid present                                    else FAIL key_unknown
6  iss policy-valid (HTTPS-only, no user-info, paired     else FAIL issuer_policy
   egress class §4 — resolve-aware, post-resolution
   private/loopback/link-local rejection)
7  resolve (iss → JWK[kid]) via cache/§4                 transport failure → key_unavailable (500, §8)
                                                         no such kid / invalid document → FAIL key_unknown
8  fixed-width signature precheck (EdDSA 64 B; ES256      else FAIL format
   P1363 r‖s 64 B — §3 encoding norm), then verify
   signature over header.claims bytes                     else FAIL signature
9  now ≥ claims.exp + skew → FAIL expired                 (reject AT equality; skew ≤ 30 s config cap is
                                                           a MUST, not advisory — §7 clock rule)
10 claims.aud == envelope lcp.message.receiver_id       else FAIL audience
11 claims.agent_id == provenance.agent.agent_id         else FAIL claims_mismatch
12 claims.acting_for == authenticated sender_id, OR
   sender scopes contain "act_for:" + claims.acting_for else FAIL claims_mismatch
13 all pass → ACCEPT (bind agent_id/acting_for/iss/kid into the lead record)
```

**Parser rule (duplicate claim names, normative).** Header and claims MUST
be parsed with RFC 8259 §5 duplicate-name rejection (e.g. Python
`json.loads(..., object_pairs_hook=…)` that refuses a repeated key;
equivalently any strict parser configured to reject, not last-wins). A
token whose claims object carries `"aud"` twice with different values is a
`format` failure at step 2 for **every** conformant receiver — an
implementation that last-wins would accept the token at one receiver's
`aud` and reject at another's (the divergence the dossier executed).
Step 4 must therefore never see a duplicate, and no claim evaluation may
define winner selection among duplicates.

**Depth (re-dossier R2).** The refusal applies to **every JSON object in the
header or claims serialization, at any nesting depth** — the `object_pairs_hook`
example fires per object, innermost first, so a repeated member inside a nested
claim value (`{"meta":{"tier":"gold","tier":"platinum"}}`, validly signed) is
the same `format` failure at step 2. This is the stricter of the two readings
the re-dossier executed (the whole-document scan of the two top-level objects
accepts it); both are deterministic, so the record chooses and states the rule
rather than leaving the depth to library defaults.

Reason vocabulary (normative, closed): `format`, `algorithm`,
`issuer_policy`, `key_unknown`, `key_unavailable`, `signature`, `expired`,
`audience`, `claims_mismatch`. Evaluation stops at the first failing step —
the order is deterministic so the same forged input always yields the same
reason (re-executed post-revision against the 13-step procedure: an
expired **and** claims-contradicting token still reports `expired`,
Revision battery).

Step 12 reuses the shipped scope machinery (`auth.py:187-189` `has_scope`,
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
| `acting_for` | MUST | string | Equal to the authenticated `sender_id`, or carried by its `act_for:` scope (§5 step 12). |
| `iat` | MUST | integer (NumericDate, RFC 7519 2) | Whole seconds. NOT enforced at intake in v1 (freshness = issuer policy; see Deferred). String timestamps are format-invalid — rejected at §5 step 4, decode-time, before any signature work; L3-001's string `iat`/`exp` is fixed at implementation (V8). |
| `exp` | MUST | integer (NumericDate) | Presence/type enforced at §5 step 4 (string/float/zero/absent → `format`); expiry enforced per §5 step 9. |
| `nbf`, `jti` | MAY | — | `nbf` SHOULD be honored if present (same skew rule); `jti` is recorded but MUST NOT be required or single-use-cached in v1 (§7 rationale; Deferred trigger). |
| others | MAY | — | Ignored (forward-compatibility mirrors §6 "unknown optional fields ignored", `SPEC.md:660-662` — claims are open by design inside a verified signature). |

Unknown-to-this-spec *required* claims are not a concept: absence of a MUST
claim above, or a type violation of one, is a decode-time **`format`**
failure at §5 step 4 — never `claims_mismatch` (that reason belongs to
post-signature contradictions with the envelope, steps 11–12), never
ignorable. The pre-revision text named both candidates; the revision pins
one (dossier F2: two candidates meant two conformant implementations
reporting different `details.reason` for the same bytes).

### 7. Replay, clock, and window rules (units-precise)

**Units (normative).**

| Quantity | Unit | Definition |
|---|---|---|
| one attestation | one signed assertion | exactly one (agent_id, acting_for, iss, aud) tuple per token; a token asserting two principals is not expressible (single claim set). |
| one verification | per lead acceptance | exactly once per lead, at the first acceptance attempt, at the pinned §5 slot; a retry of an already-accepted envelope returns the DUPLICATE ack **without re-verification** (the original accept verified the bytes that minted the row — §5 placement; `plat: test_attestation_duplicate_ack_never_refetches`); a retry that did not previously reach acceptance verifies normally (deterministic result, cached keys). |
| one failure | exactly one reason | first-failing step of §5 wins; no multi-reason payloads. |
| token validity | seconds | `exp` − `iat`, issuer-chosen; the protocol bounds only the *acceptance* window (§5 step 9's skew ≤ 30 s). The RECOMMENDED maximum lifetime is 900 s (advisory; normativizing it is Deferred with data). |
| replay window (transport) | 300 s default | unchanged §9 HMAC window (`SPEC.md:828`, `config.py:33`); the attestation rules are independent of it. |
| agent-evaluation window | 30 s default | §9 agent-as-consumer (`SPEC.md:960-967`), pinned "exactly 30" by the #195 lane (`implementations/reference-platform/tests/test_gap_security.py:491-517`; default `ping_timeout_seconds` 30, `messages.py:140`). Chosen as the **ceiling for clock-skew allowance** so no token accepted at intake can have been expired longer than one transaction window. |

**Clock rule.** `exp` is compared against the **receiver's** wall clock in
NumericDate seconds. Expiry boundary (revision F7): the check fails
`expired` when `now ≥ exp + skew` — **rejected at equality**; this is a
deliberate asymmetry with the shipped transport window, which accepts at
equality (`abs(Δ) ≤ max_skew_seconds`, `auth.py:70-71` — that boundary is
unassigned pending #143 per `docs/err-auth-triage.md` §2, and this record
does not silently harmonize it). An expired token carries no authority
even for one second, while a transport timestamp within the window is
still replay-protected by the idempotency machinery — so strictness on the
authority axis and laxity on the freshness axis are consistent, but the
conventions **differ** and both are now named. Skew allowance:
deployments MAY configure `skew ≤ 30 s` (default 0) and MUST NOT configure
more — the cap is a MUST enforced at configuration time, not advisory
(`plat: test_attestation_skew_config_capped_at_30`); its ceiling is the
agent-evaluation window row above, and the reason the allowance exists at
all is NumericDate **whole-second truncation**: a token minted anywhere
inside a second and verified anywhere in a later clock tick of the
receiver can legitimately appear up to one second early (plus honest
clock drift); the ≤ 30 s bound keeps the total slack under one transaction
window so no token accepted at intake can have been expired longer than
the platform will wait on an agent anyway. No `iat` freshness requirement
at intake — a 5-minute-old valid token is *expected* behavior (agents
batch many submissions), which is why
per-token single-use caching (`jti` replay cache) is deliberately absent:
the transport replay defenses (envelope HMAC over exact bytes +
idempotency-key equality + replay window — all shipped and pinned by the #195
lane's SEC-06 tests) already stop *request* replay; a *reused* token inside
its `exp` on new envelopes is the intended use, and cross-receiver reuse is
killed by `aud` (step 10). A `jti` cache would add unbounded state to defend
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
| decodable-shaped but malformed JWT: header/claims non-JSON or non-object, duplicate claim names, MUST-claim absent/wrong-type timestamps, wrong signature width | verifier (steps 2/4/8 precheck) | LCP-002 | 401 | `provenance.agent.attestation` | `format` |
| algorithm rejected | verifier | LCP-002 | 401 | `provenance.agent.attestation` | `algorithm` |
| `kid` missing; no `kid` match in a **valid** fetched document; fetched-but-invalid document (oversized / >32 keys / non-JSON / duplicate-`kid` under §4 option A / issuer 4xx) | verifier | LCP-002 | 401 | `provenance.agent.attestation` | `key_unknown` |
| `iss` policy-invalid | verifier | LCP-002 | 401 | `provenance.agent.attestation` | `issuer_policy` |
| signature invalid (correct width, wrong bytes) | verifier | LCP-002 | 401 | `provenance.agent.attestation` | `signature` |
| `exp` past (+skew; reject at equality, §7) | verifier | LCP-002 | 401 | `provenance.agent.attestation` | `expired` |
| `aud` mismatch | verifier | LCP-002 | 401 | `provenance.agent.attestation` | `audience` |
| claim↔envelope contradiction | verifier | LCP-002 | 401 | `provenance.agent.attestation` | `claims_mismatch` |
| JWKS **availability** failure only: DNS/connect error, fetch timeout, issuer 5xx | verifier I/O | LCP-500 | 500 | `provenance.agent.attestation` | `key_unavailable` |

Why LCP-002 for verification failures rather than new policy invention:
§5's taxonomy sentence names the axes — "these are different failure modes
(**identity** vs. replay protection)" (`SPEC.md:639-641`);
`docs/err-auth-triage.md` §3 ruled that credential failures on the *identity*
axis belong to LCP-002; the platform already stamps intake **binding**
failures LCP-002/401 — receiver-not-this-platform (`router.py:277-280`) and
authenticated-sender-mismatch (`router.py:287-290`) — and `LCP-002` is
`AuthenticationError`'s default code (`auth.py:28`). An unverifiable
attestation is precisely a failing assertion of *who is acting*, on a
message whose transport identity is fine — same axis, existing code,
`details.reason` disambiguates. The §5 sentence at `SPEC.md:638` ("covers an
unrecognized `sender_id`") is extended at implementation by one clause:
"…or a recognized sender whose agent attestation fails §9 verification."
LCP-500 for `key_unavailable`: the receiver cannot complete verification
because of *its* (or its dependency's) availability; a 4xx would blame the
sender for the receiver's infrastructure. The contrapositive is equally
normative (revision F4): a 5xx would blame the sender for the **issuer's**
fixable state — so a successfully fetched JWKS document that is invalid
(over-bounds, non-JSON, duplicate `kid` under §4 option A) and an issuer
answering 4xx join the sender-side class as `key_unknown`/401, while
`key_unavailable`/500 keeps exactly the availability set (transport error,
timeout, issuer 5xx — §4 Bounds row). `key_unavailable` is retryable and
idempotency
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
steps 1–13 PASS; the fixture JWK is

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
`auth.py:187-189`). Verifier step 12 accepts via the scope; the same token
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

(Even if MACs were allowed, step 8 would fail `signature` — the token fails
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

Today: `200 RECEIVED`. Under this proposal: step 9 fires first →
`401 LCP-002`, `details.reason: "expired"` (deterministic axis order —
Verification runs §4 proves the claims contradiction would *also* fail at
step 11 but the procedure stops at the first failure).

### Invalid example D — cross-receiver replay

`valid-a`'s token re-issued with `"aud": "platform_002"` and submitted to
platform_001 (`receiver_id: "platform_001"`) — a token minted for one
platform replayed at another:

```json
"attestation": "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCIsImtpZCI6ImF0dGVzdC1rZXktMSJ9.eyJpc3MiOiJodHRwczovL2FnZW50cy5wdWJsaXNoZXIuZXhhbXBsZS9hdHRlc3RhdGlvbiIsImF1ZCI6InBsYXRmb3JtXzAwMiIsImFnZW50X2lkIjoiYWktYXNzaXN0LTAwMSIsImFjdGluZ19mb3IiOiJwdWJfMTIzIiwiaWF0IjoxNzgwMjcyMDAwLCJleHAiOjIwODAwMDAwMDB9.ZoMSbChBjYlDWZibe8h1r6hMu5yGpmk7F-7ubMht819Jq1luCcUdKBIrFsJOni4qjfgJuENWu916TKYEl_q2Cg"
```

Verifier step 10 → `401 LCP-002`, `details.reason: "audience"`. (The
attestation itself verifies cryptographically — this is exactly why an
audience binding, not signature-checking alone, is what stops cross-pair
replay.)

### Invalid example E — claims contradict the envelope

Validly signed, unexpired, correct `aud`, but `agent_id` = `"someone-else"`
against `provenance.agent.agent_id` = `"ai-assist-001"`:

```json
"attestation": "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCIsImtpZCI6ImF0dGVzdC1rZXktMSJ9.eyJpc3MiOiJodHRwczovL2FnZW50cy5wdWJsaXNoZXIuZXhhbXBsZS9hdHRlc3RhdGlvbiIsImF1ZCI6InBsYXRmb3JtXzAwMSIsImFnZW50X2lkIjoic29tZW9uZS1lbHNlIiwiYWN0aW5nX2ZvciI6InB1Yl8xMjMiLCJpYXQiOjE3ODAyNzIwMDAsImV4cCI6MjA4MDAwMDAwMH0.9XoyrxTJpiy27YcseTbyrl8pr_zN6VcdcWakQg9h7pK3RcuqEH5MokV-OPR3CUEUxyX-ccYRiYJI849uzC1mBA"
```

Step 11 → `401 LCP-002`, `details.reason: "claims_mismatch"`. Same shape for
an `acting_for`/sender contradiction without the `act_for:` scope (executed:
Verification runs §4, `valid-b-without-scope`).

### Invalid example F — `alg: none` detached signature

```json
"attestation": "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJpc3MiOiJodHRwczovL2FnZW50cy5wdWJsaXNoZXIuZXhhbXBsZS9hdHRlc3RhdGlvbiIsImF1ZCI6InBsYXRmb3JtXzAwMSIsImFnZW50X2lkIjoiYWktYXNzaXN0LTAwMSIsImFjdGluZ19mb3IiOiJwdWJfMTIzIiwiaWF0IjoxNzgwMjcyMDAwLCJleHAiOjIwODAwMDAwMDB9."
```

Empty third segment → fails the §2 schema pattern (`400 LCP-100`) before any
header parsing; if it somehow reached the verifier, step 3 rejects `none`
(but step 1 rejects the empty segment first: `format`).
Belt and braces (executed both layers — Verification runs §3/§4).

### Invalid example G — tampered signature under a compliant header

`valid-a`'s token with the final six signature characters replaced
(signature flips under an *allowed* `EdDSA` header):

```json
"attestation": "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCIsImtpZCI6ImF0dGVzdC1rZXktMSJ9.eyJpc3MiOiJodHRwczovL2FnZW50cy5wdWJsaXNoZXIuZXhhbXBsZS9hdHRlc3RhdGlvbiIsImF1ZCI6InBsYXRmb3JtXzAwMSIsImFnZW50X2lkIjoiYWktYXNzaXN0LTAwMSIsImFjdGluZ19mb3IiOiJwdWJfMTIzIiwiaWF0IjoxNzgwMjcyMDAwLCJleHAiOjIwODAwMDAwMDB9.ndo1szT6NLNuZwaiEmvY4-4oxfrA_UUyuoFjTV_4hFzAfk9tsP4P9kcdEyLu1WsA3Qv6iw0pTa_YFMI_AAAAAA"
```

Step 8 → `401 LCP-002`, `details.reason: "signature"` — the pure forgery case
distinct from the algorithm-confusion case in example B.

### Invalid behavior example H — unknown `kid` (rejected) vs unreachable JWKS (500)

Same token, issuer's JWKS fetched successfully but containing no
`attest-key-1`: step 7 → `401 LCP-002`, `key_unknown` — the sender's/issuer's
fixable state. Issuer unreachable at all (timeout): `500 LCP-500`,
`details.reason: "key_unavailable"` — the receiver's infrastructure answer,
retryable, never fail-open in enforce mode. Between these two sits the
revised-F4 class: a document that is **successfully fetched but invalid**
(oversized, >32 keys, non-JSON, duplicate `kid` under §4 option A) is
issuer-fixable like unknown-`kid`, and routes `key_unknown`/401 — 500 is
availability only. None of these are schema-expressible; all are asserted
by named tests (V9/V10, plus V18/V19 post-revision).

## Privacy, security, and universal-core audit

**PII boundary.** `provenance.agent.*` sits in the provenance object that §3
defines as non-PII (`SPEC.md:181`). The proposal adds no PII and adds two
MUST-NOTs: the claims set MUST NOT embed consumer identifiers or contact data
(anything verifiable cross-pair via `aud`/`iss` is a correlation vector), and
logs/telemetry MUST NOT retain full attestation values (a token is replayable
bearer-grade material until `exp` — never the signature bytes). What audit
and telemetry rows DO store is now a frozen schema (revision F8, interlock
with LEP-0005, which makes `audit_events` interop-readable for the first
time — unspecified metadata would become public surface):
`{outcome, reason, iss_host, kid, tok_sha256_16}` — `outcome` ∈
`accept`/`reject`/`observe`, `reason` from the closed §5 vocabulary,
`iss_host` the issuer URL's host component only, `kid` the header claim,
and `tok_sha256_16` the first 16 hex characters of SHA-256 over the **full
compact token bytes** (input definition normative, so two deployments can
correlate incidents). No other attestation-derived field is admissible, and
the token (or any segment of it) MUST NOT appear in audit metadata,
metrics labels, or logs. Because `aud`/`iss` are per-deployment identifiers
and `tok_sha256_16` is truncated one-way, the schema is not a correlation
vector across receivers. LEP-0005's history projection keeps the same rule
from the read side ("never return raw envelope bodies"), and the joint
obligation is asserted in one place: `plat:
test_history_projection_never_emits_attestation_bytes` (V23). The lead
store already persists the block under §3's storage rules,
`SPEC.md:938-945` PII posture applies unchanged since the field is not PII.

**Abuse cases engaged:**

- *Forged agent provenance* (the #192 class): closed by verification +
  algorithm allowlist + asymmetric-only keys.
- *Cross-pair token transplant:* closed by `aud` + issuer-key binding
  (§7) — a token cannot be shown to a receiver it was not minted for, and
  other senders cannot mint issuer A's tokens.
- *Algorithm confusion:* closed by the allowlist derived from the resolved
  JWK (§3), not from attacker-chosen `alg` alone.
- *SSRF / fetch amplification via `iss`:* closed by auth-before-fetch, the
  **paired** egress controls (§4 Resolution: `validate_webhook_url`
  HTTPS-tightened + resolve-aware `validate_egress_host` post-resolution
  rejection, `follow_redirects=False`, `trust_env=False` — the shipped
  delivery-path pattern, `router.py:1019-1028`), size/count/TTL bounds, and
  a 2-second bounded fetch. The pre-revision text named only
  `validate_webhook_url` and over-claimed: the string check accepts
  `https://2130706433` / `https://0x7f000001` / `https://127.1` /
  `https://localhost`, all of which resolve to 127.0.0.1 (executed,
  Revision battery); the egress leg is what rejects them. DNS-rebinding
  TOCTOU between policy check and connect remains a named residual (§4),
  with the redirect/proxy legs removing the in-process escalation paths
  (revision F1).
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
| Reference platform | new `attestation.py` verification module + resolver; `ingest()` step pinned **after** the dedup early-return paths (`router.py:297`: lookup, `router.py:304-308`: LCP-005 409, `router.py:318`: DUPLICATE ack) and **before** `insert_lead` (`router.py:359`) — §5 placement (revision F6a); paired egress controls on every JWKS fetch (`router.py:1019-1028` pattern, §4 Resolution); mode config (`observe`/`enforce`, default `enforce` at ship); Phase 0 advertisement line (`router.py:1769`). |
| OpenAPI | capabilities shape unchanged (`api/lcp-openapi.yaml:839` already documents `conformance_level`); intake error examples gain the `details.reason` variants — documentation-level. |
| MCP | **zero logic** — HTTP client path inherits enforcement (`client.py:67`); `test_gap_mcp_surface.py` byte-for-byte assertions still pass (the adapter never normalizes); its stub-platform fixture's `"L3"` (line 128) is test-double data, not a platform claim, but the V12 pin ensures a real-platform advertisement change lands with matching docs. |
| SDKs (all languages, per implementations/sdk/) | signer/verify helper for the §2/§6 form (builders already accept an arbitrary string — unchanged); canonical NumericDate emission mirrors LEP-0002's whole-second discipline (`SPEC.md:832-845`); no transport change. |
| Vectors + runner | `test-vectors/attestation/fixture-jwks.json` (the record's pinned Ed25519 JWK + seed derivation documented, **plus the ES256 fixture keypair/JWK per §3 encoding note**, `kid` `attest-es256-1`); positive `lcp-pos-attestation-verified-001` (EdDSA) and `lcp-pos-attestation-es256-001` (canonical P1363) and negatives `-forged-`, `-expired-`, `-audience-`, `-claims-mismatch-`, `-free-text-`, `-alg-none-`, `-string-exp-` (V16), `-padded-` (V21, schema-tier), `-es256-der-` (platform-tier; schema-tier negatives via existing expect-fail form with `error_code` fields, precedent `l1-envelope.json:93`); L3-001 token re-issued. |
| Coverage matrix | `SEC-09` flips `gap → green` only with the verification tests shipping (V1–V10 and revision gates V13–V24); `CNF-03` note updated to name attestation verification tests, not shape-only (`docs/SPEC-COVERAGE.md:165`). |

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
| V8 | vectors: L3-001 re-issued verifiable under the fixture key + `attestation_present` check retained; positive/negative attestation vectors land and the runner consumes them; **ES256 positive `lcp-pos-attestation-es256-001` (canonical P1363 under `attest-es256-1`) joins this family (revision F3)**. |
| V9 | `plat: test_attestation_unknown_kid_rejected` (`key_unknown`) and `plat: test_act_for_scope_delegation` (valid-b accepted; same token + unscoped sender → `claims_mismatch`). |
| V10 | `plat: test_attestation_jwks_unavailable_fails_closed` — issuer **availability** failure (DNS/connect/timeout/5xx) → `500 key_unavailable`, **zero** lead rows; observe-mode logs only. Post-revision attribution: a fetched-but-invalid document is NOT this path — see V18. |
| V11 | `tool: test_lep_wire_examples` continues to pass on this record's `### Valid example` envelopes against the published tree (it does, today — Verification runs §5), and a `checks`-driven attestation case enters the vector lane. |
| V12 | `plat: test_capabilities_conformance_level_matches_behavior` — advertisement honesty pinned (§9 of this record); Phase 0 lands it first. |
| V13 | `plat: test_attestation_iss_obfuscated_loopback_rejected` — `iss` hosts `2130706433` / `0x7f000001` / `127.1` / `localhost` (all `getaddrinfo`→127.0.0.1) rejected by the resolve-aware egress leg at §5 step 6 → `issuer_policy`; zero fetch issued (revision F1). **Amended (re-dossier R1):** hosts `127.0.0.1` / `localhost` / `2130706433` / `::ffff:127.0.0.1` are ALSO rejected under a sandbox config (`test_mode=True` + `allow_insecure_webhooks=True`) — the resolve leg must not inherit the shipped class's skip; the battery re-executed the dossier's four shipped no-op `ACCEPT` rows and both revised readings reject all four. |
| V14 | `plat: test_attestation_jwks_fetch_no_redirects_no_proxy_env` — a 302 from a policy-valid `iss` is not followed (3xx → invalid-response path, never a second fetch of `Location`); `HTTPS_PROXY` in the environment does not reroute the JWKS fetch (`trust_env=False`) (revision F1). |
| V15 | `plat: test_attestation_decode_time_type_presence_format` — string/float/zero/absent `iat`/`exp`, absent `aud`/`agent_id`/`acting_for`, non-object claims, duplicate claim names: each fails at §5 step 2/4 with reason `format`, each with zero key-resolution and zero crypto work performed (revision F2). **Amended (re-dossier R2/R3):** two new rows — a duplicate member name at nesting depth 2 (`{"meta":{"tier":"gold","tier":"platinum"}}`) fails at step 2, and exponent-notation timestamps (`"iat":1e9,"exp":2e9`) fail at step 4 on the claims bytes, not the parsed values (the exponent negative rides V16's vector family as `lcp-neg-attestation-exp-form-exp-001`). |
| V16 | `vec: lcp-neg-attestation-string-exp-001` — vector-tier pin of the string-`exp` class (the L3-001 defect shape) as a decode-time `format` negative (revision F2). |
| V17 | `plat: test_attestation_es256_der_rejected` — validly produced ES256 signature re-encoded as DER → `format` at the step-8 width precheck; the same token's P1363 form verifies (`lcp-pos-attestation-es256-001` rides V8) (revision F3). |
| V18 | `plat: test_attestation_jwks_oversize_is_401` — fetched document > 16 KiB / > 32 keys / non-JSON → `401 key_unknown`, NOT 500; only DNS/connect/timeout/5xx keeps `key_unavailable`/500 (revision F4). |
| V19 | `plat: test_attestation_jwks_duplicate_kid_rejected` — same-`kid` JWKs in one document: asserts §4 option A (invalid document → `key_unknown`); becomes `…_first_match_pinned` if the maintainer selects option B at Accept (revision F5, decision-flagged). |
| V20 | `plat: test_attestation_duplicate_ack_never_refetches` — second submission of an accepted envelope returns the DUPLICATE ack with zero verifier work and zero JWKS fetches (revision F6a; §5 placement). |
| V21 | `vec: lcp-neg-attestation-padded-100` — `=`-padded signature segment fails at the schema layer (`LCP-100`), pinning §2 precedence (revision F6b). |
| V22 | `plat: test_attestation_skew_config_capped_at_30` — a deployment configuration with skew > 30 s is refused at startup; boundary probes at `exp + skew == now` (reject), `exp + skew − 1` (accept) pin §7 equality convention (revision F7). |
| V23 | `plat: test_history_projection_never_emits_attestation_bytes` — asserts LEP-0005's no-raw-envelope rule and this record's frozen audit schema (`{outcome, reason, iss_host, kid, tok_sha256_16}`) in one place: no projection, audit row, metric, or log line carries token bytes (revision F8). |
| V24 | `plat: test_attestation_header_member_policy` — §2 receiver-side header policy (re-dossier R4): `typ=JWT` and `typ=at+jwt` variants of identical claims produce the SAME verdict; an unknown non-`crit` header member (`x5u`) is ignored AND produces **zero** network fetches (no dereference — the fixture records the fetch count); a `crit` header is a step-2 `format` failure. |

**Operational:** metrics per ingest attempt `{verification outcome × reason}`
plus issuer-fetch latency/error counters; audit rows reuse
`store.insert_audit` (`router.py` pattern) with the frozen §Privacy schema
`{outcome, reason, iss_host, kid, tok_sha256_16}` — never the token bytes;
dashboards/alerts on `key_unavailable` rate (issuer outage is
a *visible* failure now, which is the point of LCP-500 vs today's silent
accept).

## Alternatives considered

Trust models (candidate set from #192's "key distribution undefined"):

| Candidate | Verdict | Reasoning |
|---|---|---|
| **A. Issuer-published JWKS (DID-URL/HTTPS `iss`)** | **RECOMMENDED — proposed** | Matches §9's `issuer` claim and "JWKS or pinned public key" text; per-issuer revocation via `kid` rotation; models one agent authority across many publishers (§10 roles); zero new protocol surface (URL rules + the existing paired egress-policy class). Costs: cache/SSRF-pair/timeout machinery (bounded above), issuer-availability dependency (fail-closed + observe rollout). |
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
(`{outcome, reason, iss_host, kid, tok_sha256_16}` — frozen §Privacy
schema, never the token), leads still accept,
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

## Revision 2026-11-03 (post-dossier)

Record-only revision resolving the ARB L-Security (combined-lane declared)
dossier on
[#205](https://github.com/SpearSystems/LCP/issues/205) — comment
[`6596818586`](https://github.com/SpearSystems/LCP/issues/205#issuecomment-6596818586),
a SELF-AUDIT-labeled L-Security+L-Spec run reviewed at SHA
`052970226f90cef220ea31f98946736f8fbaf563` (the record's own merge head, PR
#207). All three majors (F1 SSRF half-guard, F2 decode-time claim
validation, F3 ES256 encoding fork) and all six minors (F4–F9) are
dispositioned below; every disposition was re-validated by EXECUTION at the
post-revision text (reference verifier rebuilt from the revised §5, battery
transcript below). **Status stays Draft**: no implementation is authorized;
every `vec:`/`plat:` test named in this revision belongs to the
implementation PR (G5 tracking checklist posted on #205), not to this
record. The registry row and the coverage matrix are untouched. F8 was also
filed against the LEP-0005 lane (charter rule 3); the LEP-0005 side needs no
edit — this record now states its half of the interlock (frozen audit
metadata schema, §Privacy).

**Second re-dossier round (same day).** The re-review of this revision —
comment
[`6597921626`](https://github.com/SpearSystems/LCP/issues/205#issuecomment-6597921626),
also SELF-AUDIT-labeled (same controller, same model family; the standing
G2 block is unchanged by it) — re-attacked the post-#209 text at head
`e8ec36b87f12cd13a7fb63bc88bb53327a2d8b5f` and confirmed the three majors
resolved, breaking the text only in five new **minors** (R1–R4 cross-receiver
determinism/guard-scope gaps, R5 docs-drift). R1–R5 are folded here and in
the normative sections they touch (§2, §4, §5, §8); the Re-dossier addendum
below re-executes the battery against the pinned text. The verdict stands:
non-blocking for decision; the human co-sign and the non-author re-review
remain the open items.

### Findings → edits

| Dossier item | Disposition in record |
|---|---|
| **F1 (major)** — §4 cited `validate_webhook_url` alone as "SSRF guard" and the abuse list claimed SSRF "closed"; the shipped string check does not resolve DNS and accepts `https://2130706433` / `https://0x7f000001` / `https://127.1` / `https://localhost` (all `getaddrinfo`→127.0.0.1) | §4 **Resolution row rewritten** to the paired controls exactly as shipped on the delivery path (`router.py:1019-1028`): HTTPS-tightened `validate_webhook_url` (`security.py:16`, no user-info — already enforced at `security.py:20-21`), **resolve-aware `validate_egress_host`** (`security.py:33`; post-resolution `is_private`/`is_loopback`/`is_link_local`/`is_multicast`/`is_reserved`/`is_unspecified` rejection, `security.py:44-54`, `66-74`), `follow_redirects=False`, `trust_env=False` — all four as MUSTs on **every** JWKS fetch; §5 step 6 names the pair; abuse bullet reworded to name the pair and to **name** the DNS-rebinding TOCTOU residual instead of claiming it closed; Scope bullet updated; gates V13/V14 named. Battery §3 below executes both legs live. |
| **F2 (major)** — §5 had no decode-time step for §6 MUST-claim presence/type/objectness/duplicate names; §6 left `claims_mismatch`/`format` undecided | §5 rewritten: new **step 4** (all §6 MUST claims present, `iss`/`aud`/`agent_id`/`acting_for` non-empty strings, `iat`/`exp` positive integer NumericDate — string/float/zero/absent → `format`, before any key lookup or crypto work), step 2 pinned to strict parse of both segments as JSON **objects**, new **Parser rule** paragraph (RFC 8259 §5 duplicate-name rejection, no last-wins fallback — a duplicate claim name is `format` at step 2 for every conformant receiver), step 3 pinned **exact, case-sensitive** `alg` match **before any key lookup** (§3 paragraph added), §6 closing sentence now names the single reason (`format` at step 4; `claims_mismatch` reserved for post-signature envelope contradictions). Reason vocabulary unchanged (closed). Gates V15/V16 named. Battery §2 rows below execute the class. |
| **F3 (major)** — `ES256` MUST-supported with no signature-encoding norm; DER vs JWS fixed-width fork | §3 adds a normative **Signature encoding** paragraph: IEEE P1363 fixed-width `r ‖ s` (ES256 = exactly 64 octets) per RFC 7518 §3.4; DER and any variable-width encoding MUST NOT be accepted — width precheck at step 8 → `format`, crypto failure → `signature`; EdDSA fixed-width (64 B) named; ES256 fixture derivation note added (seed scalar `d = int.from_bytes(SHA-256("lep-0008-fixture-p256-seed"), "big")` on P-256, pinned `kid` `attest-es256-1`, public JWK published in the battery below). Gates: V17 (`test_attestation_es256_der_rejected`) + ES256 positive folded into V8; vectors row updated. Byte evidence: Battery §6. |
| **F4 (minor)** — fetched-but-invalid/oversized JWKS routed to `LCP-500`, violating §8's own blame rule | §4 Bounds row rewritten with the outcome split; §8 table now has `key_unknown`/401 for **fetched-but-invalid** documents (oversize / >32 keys / non-JSON / duplicate-`kid` under option A / issuer **4xx**) and reserves `key_unavailable`/500 for **availability only** (DNS/connect/timeout/**5xx**); §8 rationale states the contrapositive normatively; example H paragraph extended; V10 amended; V18 named. The `jwks_oversize`-style dedicated reason was considered and **rejected** — the closed vocabulary already names the state (decision recorded in the Bounds row). |
| **F5 (minor)** — duplicate `kid` entries unspecified; first-wins vs last-wins fork opposite answers | §4 Selection row states **both conformant options** — (A) duplicate-`kid` document is invalid → `key_unknown` per F4 split (also covering the `use`/`key_ops` confusion class); (B) selection pinned to first-match + MUST `duplicate_kid` warning log — and **marks the choice for the maintainer at Accept** rather than silently picking. The reference implementation and the Revision battery validate **A** (the stricter, determinism-preserving option); V19 carries the gate name for either outcome. |
| **F6 (minor)** — (a) §5 slot silent on DUPLICATE early-return/LCP-005 paths; (b) §2 padding prose vs pattern | (a) §5 placement pinned: slot runs **after** the dedup lookup (`router.py:297`) including both early returns — LCP-005 409 (`router.py:304-308`) and DUPLICATE ack (`router.py:318`) — and before `insert_lead` (`router.py:359`); norm "a DUPLICATE ack never re-verifies and never fetches"; §7 "one verification" row rewritten to match; V20 named; impact table row updated. (b) §2 gains the precedence clause: the schema pattern excludes `=`, receivers reject padded tokens with `400 LCP-100`; the "decodable by parsers" prose is scoped to generator discipline only; V21 named. |
| **F7 (minor)** — `exp` equality convention unstated and asymmetric with the transport window; skew cap advisory | §5 step 9 and the §7 **Clock rule** now state: fail `expired` when `now ≥ exp + skew` — **reject at equality** — with the deliberate asymmetry vs the shipped transport window (`auth.py:70-71`, accepts at equality; boundary unassigned per `docs/err-auth-triage.md` §2/#143, not silently harmonized); the ≤30 s skew cap is a configuration-time **MUST** (V22), with the rationale tied to NumericDate whole-second truncation (tolerance covers truncation + honest drift, ceiling = one agent-evaluation window so no accepted token can be expired longer than one transaction window). |
| **F8 (minor, cross-record)** — audit metadata shape (`iss`+`kid`+hash) unspecified while LEP-0005 makes `audit_events` interop-readable | §Privacy rewritten: frozen schema `{outcome, reason, iss_host, kid, tok_sha256_16}` — input = full compact token bytes, SHA-256, first 16 hex characters; nothing else attestation-derived admissible; NEVER the token. Rollout Phase 2 tuple and the Operational bullet updated to the same five keys; step 13 returns the frozen metadata (executed, Battery §7). V23 (`test_history_projection_never_emits_attestation_bytes`) asserts both records' rules in one place. LEP-0005 requires no edit for this interlock (its "never return raw envelope bodies" norm is the read-side half, confirmed by the dossier). |
| **F9 (minor, docs-drift)** — stale "matches only vendored dependency copies" quote | Problem-statement sentence rewritten: #192's quote preserved **as attribution**, with the re-execution at `0529702` stated (`grep -rn "jwks\|jwt.decode\|verify_attestation" implementations/` → **zero** matches; the current state is stronger than the quoted evidence). Two further trace items fixed in the same pass: the dangling "§10 of Verification runs" cross-reference (now "Revision battery", with the expired+mismatch outcome re-proved at the 13-step numbering), and the stale intake-range cites (`router.py:281-298` / `294-298`) replaced by the pinned `279-295`/`297-318` anchors. All step cross-references renumbered to the 13-step procedure (examples B/C/D/E/F/G/H, §6, §7, Valid examples). |

### Revision battery (executed 2026-11-03, post-edit)

Environment: repository venv (`python 3.12.12`), `cryptography 50.0.0` +
`jsonschema 4.26.0` (per `requirements-ci.txt`); working tree = `0529702`
+ this revision's diff (docs-only; code tree byte-identical to the reviewed
SHA). Harness: `/tmp/lep0008-revision-battery.py` (scratch, not committed —
charter "harness is published" conversion belongs to the implementation PR,
per the dossier's own G5 note). The verifier implements the **revised** §5
steps 1–13; tokens are re-extracted from this file (re-extraction checks at
the end). One simulation is named: `getaddrinfo` is stubbed **only** for
`*.example` fixture hosts (→ `93.184.216.34`, routable; TEST-NET ranges
would themselves fail the egress class on modern CPython — noted honestly);
the obfuscated-loopback rows resolve **live** through the shipped
`validate_egress_host`. 61 checks, all pass; key transcript:

```text
== 0. #192 trio against the live in-memory platform at HEAD ==
OK  #192 trio row1-freetext (persists pre-impl)   got=(200, 'RECEIVED') want=(200, 'RECEIVED')
OK  #192 trio row2-forged-hs256 (persists pre-impl) got=(200, 'RECEIVED') want=(200, 'RECEIVED')
OK  #192 trio row3-expired-mismatch (persists pre-impl) got=(200, 'RECEIVED') want=(200, 'RECEIVED')
OK  #192 trio row4 key-absent                     got=(400, 'ERR | LCP-100') want=(400, 'ERR | LCP-100')
OK  L3 still advertised at HEAD                   got=L3 want=L3
```

(The trio landing `200` at head is the **expected** state: this is a Draft
record; the fix ships only at implementation/Phase 1 — the persistence
proves nothing regressed and the problem statement still holds.)

```text
== 1. revised reference-verifier battery (the record's 14 pinned lines,
      re-run against the 13-step procedure; every invalid produces its
      pinned first-failing reason) ==
OK battery[valid-a]                                    PASS
OK battery[valid-b (delegated sender w/ act_for scope)] PASS
OK battery[valid-b-without-scope]                      reason=claims_mismatch step=12 http=401
OK battery[freetext]                                   reason=format step=1 http=401
OK battery[forged-192 HS256]                           reason=algorithm step=3 http=401
OK battery[expired]                                    reason=expired step=9 http=401
OK battery[wrong-aud (cross-receiver replay)]          reason=audience step=10 http=401
OK battery[claims-mismatch (agent_id)]                 reason=claims_mismatch step=11 http=401
OK battery[unknown-kid]                                reason=key_unknown step=7 http=401
OK battery[http-iss]                                   reason=issuer_policy step=6 http=401
OK battery[alg-none detached]                          reason=format step=1 http=401
OK battery[tampered-signature]                         reason=signature step=8 http=401
OK battery[jwks-unreachable]                           ('key_unavailable', 7, 500, 'LCP-500')
OK battery[expired+mismatch (#192 row-3)]              reason=expired step=9 http=401  ← first-failing-axis order proven
OK fail-closed: no fetch-less accept path
```

Note the ordering proof the F2 fix depends on: `forged-192 HS256` reports
`algorithm` (step 3) even though the token also **lacks** `aud` — a
decode-time presence step placed after `alg` but before any key work keeps
the record's pinned reasons intact.

```text
== 2/3. new revision-class rows (F2 decode-time, F1 paired egress) ==
OK string-exp / zero-exp / missing-exp / missing-acting_for / float-iat   reason=format step=4
OK non-object claims                                                      reason=format step=2
OK duplicate-aud names (raw-byte payload)                                 reason=format step=2  ← parser REJECTS, no last-wins
OK kid missing                                      reason=key_unknown step=5
OK alg 'edDSA' case                                 reason=algorithm step=3 (BEFORE any key work; zero fetches verified)
OK padded segment at verifier                       reason=format step=1 (schema layer separately pins 400 LCP-100, §8)
OK iss 2130706433   string-leg ACCEPTS, getaddrinfo=['127.0.0.1']  -> reason=issuer_policy step=6
OK iss 0x7f000001   string-leg ACCEPTS, getaddrinfo=['127.0.0.1']  -> reason=issuer_policy step=6
OK iss 127.1        string-leg ACCEPTS, getaddrinfo=['127.0.0.1']  -> reason=issuer_policy step=6
OK iss localhost    string-leg ACCEPTS, getaddrinfo=['127.0.0.1', '::1'] -> reason=issuer_policy step=6
OK client kwargs on the one fetch   {'follow_redirects': False, 'trust_env': False}
```

The four loopback rows are the dossier's F1 plants re-executed: the cited
string leg alone accepts every one; the resolve-aware leg is what rejects
them, so the record's pairing claim is now executed, not asserted.

```text
== 4/5/7. F4/F5 routing, F7 boundaries, F8 metadata ==
OK oversized/invalid doc -> key_unknown step=7 http=401 (NOT 500)
OK issuer 404            -> key_unknown step=7 http=401
OK issuer 5xx            -> key_unavailable 500 (availability class preserved)
OK duplicate-kid doc (option A) -> key_unknown step=7 http=401
OK >32-keys doc / use:enc malformed keyset -> key_unknown step=7 http=401
OK exp+30 == now -> reason=expired step=9 (reject AT equality)
OK exp+30 − 1 == now -> PASS (skew 30); exp−1 -> PASS (skew 0)
OK skew=31 config REFUSED at construction (§7 cap is a MUST)
OK accept-path metadata keys = {outcome, reason, iss_host, kid, tok_sha256_16}; token absent from serialization; tok_sha256_16 = SHA-256(compact token bytes)[:16] (recomputed equal)
```

```text
== 8. schema battery (published vs §2 overlay, repository SchemaValidator) ==
schema[valid-a]          published=PASS   overlay=PASS
schema[valid-b]          published=PASS   overlay=PASS
schema[invalid-freetext] published=PASS   overlay=REJ "'trust me, I am a real agent (free text)' does not match '^[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+$' at provenance/agent/attestation"
schema[invalid-none]     published=PASS   overlay=REJ (same pattern, empty third segment)
schema[padded-signature] published=PASS   overlay=REJ  ← F6b precedence executed
schema[oversize-8193]    published=PASS   overlay=REJ
```

### ES256 P1363 byte evidence (F3)

Fixture public JWK (seed scalar verified in range, `1 ≤ d < n_P-256`):

```text
{"kty": "EC", "crv": "P-256", "x": "LHhhgrv-4W6IbzTx11LUcQukfb_RwXGOV4l4HEcRBok",
 "y": "5EEm6hx5OK4SCIDDSYrd-Bm4Mt--lsAAItTb3ZZN3fQ", "kid": "attest-es256-1", "alg": "ES256"}
```

One sample run over the `valid-a` claim set with header
`{"alg":"ES256","typ":"JWT","kid":"attest-es256-1"}` (ECDSA signatures are
not byte-stable across runs — the fork is a length/encoding fact, not a
value fact; the implementation PR publishes the pinned vector bytes):

```text
DER          len=70  hex=304402205d47cfed008f6bb62636fc47b8922a2af090ba6c320ab46a499ae3f561c9fa190220286038ecf964d7ef22d11362067c2a33f1879dc6ae138ca7a72f63a6d92ab86a
P1363 r||s   len=64  sha256=9fdd8421da34232f1964f688f7561a23f6312e10f923d6db1433f4e5fbe698ba
             hex=5d47cfed008f6bb62636fc47b8922a2af090ba6c320ab46a499ae3f561c9fa19286038ecf964d7ef22d11362067c2a33f1879dc6ae138ca7a72f63a6d92ab86a
bytes differ: True
strict JWS verifier:  P1363 token -> PASS (verifies under the fixture public key)
                      DER-encoded token -> reason=format step=8 (width precheck)
naive DER verifier fed the canonical 64B bytes -> rejected  ← the mutual-fork the norm resolves
```

(Dossier §12 observed a 72-byte DER for its sample; DER length varies 70–72
with the top bits of `r`/`s`. The §3 norm names the class, not one length.)

### Gates and re-pins

Post-revision execution at this diff:
`tools/check_lep_registry.py` PASS (8 proposals; registry row untouched —
status stays `Draft`, matching this record's header);
`tools/check_doc_citations.py --strict` → **413 citations checked, 0
broken** (369 at the dossier head + this revision's new anchors, all
verified against the unchanged code tree);
`tools/check_reserved_fields.py` PASS; `tools/check_universal_core.py` PASS
(14 machine-checkable §12 audits); `test-vectors/conformance.py` 56 passed /
0 failed; `tools/tests/test_lep_wire_examples` OK — the two `### Valid
example` envelopes still validate through the **published** tree, and the
revision section adds no `json` fences, so the wire-example parser is
unchanged by this append. The re-extraction checks confirm this file is its
own evidence source: the `valid-a` literal extracted from the record
verifies end-to-end (PASS row above), `sha256_16` recomputation matched,
and exactly one record token carries the empty (detached) third segment.

Changed line-anchors re-pinned against `0529702`: §5 placement
(`router.py:279-295`, `297`, `304-308`, `318`, `359`), egress pair
(`router.py:1019-1028`, `security.py:16`/`20-21`/`33`/`44-54`/`66-74`),
transport-window boundary (`auth.py:70-71`), and every step number in
examples A–H, §6, §7, and the Valid examples (13-step procedure).
Historical transcripts under "Verification runs" are preserved verbatim
(their "steps 1–12 as drafted" phrasing refers to the pre-revision
procedure; the Revision battery re-proves the same outcomes at 1–13).

### Decision flags and G5

1. **F5 duplicate-`kid` norm** (options A/B stated in §4 Selection): the
   maintainer picks at Accept; V19's name branches on the choice. This
   record does not silently choose.
2. **Phase 0** remains the early ratification item (unchanged from Draft).
3. **G5**: all eleven dossier-named CI tests are now in the record's gate
   table as V13–V23 (plus the F3 ES256 positive folded into V8); the
   majors/minors → edited-vs-deferred-to-implementation checklist is posted
   as the tracking reply on #205.
4. The dossier's independence limits stand and are now also *executed*
   rather than inferred: the loopback plants were run through the shipped
   egress class live (F1); the ES256 fork was demonstrated with real
   signatures both ways (F3). What the revision does NOT do is implement:
   `follow_redirects`/`trust_env` assertions in this battery are pinned
   client-kwargs, not socket behavior — V14 is the implementation-tier
   test of that claim.
