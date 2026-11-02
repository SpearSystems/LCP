# LCP SDK Negative Security Vectors

Machine-readable "must-fail" fixtures for the SDK security invariants
([`docs/SDK-ROADMAP.md`](../../../docs/SDK-ROADMAP.md) §Security invariants) and
the negative half of the SPEC.md contracts (error taxonomy §5, open/closed
extensibility §6, ping allowlist §4, replay window §9). Sibling files in
`test-vectors/sdk/`: `hmac.json` is the single **positive** signing fixture and
`validation-corpus.json` is the **boolean** schema corpus; this directory is the
canonical negative suite — every vector says exactly what a conforming
verifier/validator must *refuse* (and carries accept controls so a runner that
always rejects cannot pass).

Every SDK lane implements a runner over these same files (Python reference:
[`implementations/sdk/python/tests/test_negative_vectors.py`](../../../implementations/sdk/python/tests/test_negative_vectors.py)).
CI job: [`.github/workflows/negative-vectors.yml`](../../../.github/workflows/negative-vectors.yml).

## Layout

- One JSON **suite file per vector group**: `signature.json`,
  `replay-window.json`, `message-type.json`, `field-allowlist.json`,
  `ping-pii.json`, `test-mode.json`.
- A runner loads **every `*.json` in this directory** (all files are vectors —
  none are optional). Non-vector material (like another lane's notes) uses a
  different extension or a leading underscore and is ignored.
- Determinism rules: no wall clock (fixed `inputs.now`), no network, schemas
  from the repo `schemas/` + `verticals/` trees, secrets inline (fixtures are
  synthetic; never reuse real keys), bodies are exact strings, signatures are
  lowercase hex per SPEC.md §9.

## Suite file

```json
{
  "suite": "lcp-sdk-negative",
  "version": 1,
  "group": "signature",
  "description": "what this group locks and why",
  "vectors": [ ... ]
}
```

## Vector (all fields required unless noted)

| Field | Meaning |
|---|---|
| `id` | `lcp-neg-<group>-<nnn>`, unique across the whole directory. |
| `invariant` | Integer 1–8 = the SDK-ROADMAP security invariant under test, or `null` for SPEC-level vectors (§5/§6 rules with no invariant number). Never fake a mapping. |
| `spec_ref` | Normative citation (`"SPEC.md §9 …; SPEC.md §5 LCP-012"`). Required even when `invariant` is set. |
| `description` | The attack/edge case and what a broken implementation would do instead. |
| `applies_to` | Message kinds the inputs are valid for: `["*"]` (transport/schema-agnostic) or a list from the closed `message.type` enum (`lead`, `call`, `ping`, `post`, `ack`, `event`, `bid`). |
| `check` | Dispatch name, see table below. Unknown check ⇒ runner MUST fail, never skip. |
| `inputs` | Check-specific, JSON-safe; see table. |
| `expect` | `{ "outcome": "reject"\|"accept", "error_code": "LCP-…" \| null, "error_name": … \| null }`. `reject` ⇒ non-null code from SPEC.md §5; `accept` ⇒ both null. The code is the **protocol-level** error a conforming platform emits; SDK runners assert the outcome and assert the code only if their primitive surfaces structured errors. |
| `sdk_exercisable` | `true` normally. `false` = no SDK primitive exists for this check (see Gap policy). |
| `gap_note` | Required exactly when `sdk_exercisable: false`: one sentence naming the missing SDK surface and where enforcement actually lives. |

## Checks

| `check` | `inputs` | Contract (per SDK) | reject code(s) |
|---|---|---|---|
| `verify_hmac` | `secret`, `signature`, `timestamp`, `idempotency_key`, `body`, `now`, `replay_window_seconds` | Run the SDK's request-verification over the raw UTF-8 `body` with a verifier clock fixed to `now`. | `LCP-012` |
| `verify_raw_body` | same, but `signed_body` + `verify_body` | `signature` covers `<timestamp>\n<idempotency_key>\n<signed_body>`; verification MUST run over `verify_body` bytes. Proves invariant 2 (no re-serialization). | `LCP-012` |
| `envelope_validate` | `envelope` | Full schema validation (envelope + dispatched payload + vertical + ping-safe). Reject = non-empty error set. | `LCP-006`, `LCP-008`, `LCP-100` |
| `offer_validate` | `offer` | Buyer-offer schema validation. | `LCP-100` (accept vectors: null) |
| `production_mode_verify` | `mode`, `envelope` | A verifier created in `mode` refuses traffic that violates the test/production policy. | `LCP-013` |

## Gap policy (how `sdk_exercisable: false` is handled)

A runner MUST NOT silently pass a non-exercisable vector. It prints the
`gap_note` to CI output (e.g. Python `unittest` `skipTest`, which shows as
SKIPPED) so the gap is visible in every run. If an SDK **does** expose the
primitive, that lane sets `sdk_exercisable: true` in a follow-up PR (the shared
files here are the source of truth; per-SDK overrides belong in the SDK's own
runner only when unavoidable).

**Invariant 8 finding (verified at 3d4bc9ab, Python SDK 1.0.2):** the Python SDK
has no verifier-side mode concept — `webhook.verify_http_request` is
mode-agnostic and `X-LCP-Test` is emitted send-side only (`client.py`).
Therefore `test-mode.json` ships as `sdk_exercisable: false`: **invariant 8 is
not exercisable by the SDK — it is a platform concern** (the reference platform
enforces LCP-013 per SPEC.md §5). SDK lanes: inspect your verifier for mode
state; if none exists, keep the vector skipped-with-note; if one exists, report
the lane override.

## Invariant coverage map

| Invariant | Vectors / owner |
|---|---|
| 1 exact-bytes HMAC | `signature.json` (sig-001/002) + positive `hmac.json` |
| 2 raw-body verify | `signature.json` (sig-003/004) |
| 3 constant-time compare | not vectorizable by outcome — code-level audit / timing tests per SDK lane |
| 4 TLS verify default-on | per-SDK client construction tests (not outcome vectors) |
| 5 replay window before processing | `replay-window.json` |
| 6 idempotency reuse | platform concern (cached outcome on retry) — reference-platform suite |
| 7 no PII in logs | static checker `tools/check_logging_hygiene.py` + runtime capture tests (log-lane) |
| 8 test≠prod isolation | `test-mode.json` (gap-skipped at SDK level; platform LCP-013) |
| SPEC §6 unknown message type | `message-type.json` |
| SPEC §6 open/closed fields | `field-allowlist.json` |
| SPEC §4 ping allowlist / LCP-008 | `ping-pii.json` (+ existing corpus `sdk-002`, `sdk-012`, `conformance.py check_ping_safe` — not duplicated here) |

## Adding vectors (any lane)

1. Pick the group file (create `<group>.json` with the suite header if new).
2. Build the input from a known-good base (corpus fixtures `sdk-001` lead
   envelope, `sdk-002` ping envelope) with **exactly one** violation so the
   rejection is attributable to the rule under test; reject vectors pair with
   an accept control differing only in the attacked part.
3. Run the Python runner — it lints format (required fields, unique ids, code
   sets, gap rules) before executing anything. Format violations fail CI.
4. Signatures for new `verify_*` inputs: `HMAC-SHA256(secret,
   "<timestamp>\\n<key>\\n<body>")` lowercase hex — generate, never guess.

## SDK runner exercisability (added per lane; batch A)

Each runner loads every `*.json` in this directory, lints the shared format,
then dispatches each vector against that SDK's real primitives. These SDKs
surface no protocol error **codes** from their verify/validate entrypoints (they
raise/return a bare failure), so per the contract above each runner asserts the
`outcome` only and never the code. `production_mode_verify` is a loud gap-skip
(stderr line, not counted as executed) because none of these SDKs has a
verifier-side test/prod mode — invariant 8 is a platform concern (§ Gap policy).

| SDK | Runner file | verify_hmac | verify_raw_body | envelope_validate | offer_validate | production_mode_verify |
|---|---|---|---|---|---|---|
| Java | `implementations/sdk/java/src/test/java/com/spearsystems/lcp/NegativeVectorsTest.java` | ✅ `LcpSdk.verifyHmac` (throw ⇒ reject; malformed-timestamp family rejects via `Instant.parse`) | ✅ verify over raw `verify_body` bytes | ✅ `SchemaValidator.validateEnvelope` (unknown type / closed-header extra / non-allowlisted ping field all throw) | ✅ `SchemaValidator.validateOffer` | ⚠️ GAP (`sdk_exercisable:false`) |
| Kotlin | `implementations/sdk/kotlin/src/test/kotlin/com/spearsystems/lcp/NegativeVectorsTest.kt` | ✅ `LcpSdk.verifyHmac` (`Instant.parse` + `MessageDigest.isEqual`) | ✅ verify over raw `verify_body` bytes | ✅ `LcpSchemaValidator.validateEnvelope` | ✅ `LcpSchemaValidator.validateOffer` | ⚠️ GAP (`sdk_exercisable:false`) |
| PHP | `implementations/sdk/php/tests/negative_vectors.php` | ✅ `Lcp\Signing::verify` (`hash_equals`; malformed ts rejects via constructor throw or signature mismatch — outcome-only, deterministic) | ✅ verify over raw `verify_body` bytes | ✅ `Lcp\SchemaValidator::validateEnvelope` | ✅ `Lcp\SchemaValidator::validateOffer` | ⚠️ GAP (`sdk_exercisable:false`) |

Per-SDK totals with the current suite (14 vectors): **13 executed, 1 gap-skip**
(`lcp-neg-mode-001`).

CI discovery: **Java** (`mvn test` → surefire auto-discovers `src/test/**/*Test.java`)
and **Kotlin** (`gradle test`) run these runners with no workflow change. **PHP**
is not yet discovered: the `php` job in `.github/workflows/sdk.yml` invokes only
`php tests/shared_vector.php`; wiring this runner needs that one line extended to
`… && php tests/shared_vector.php && php tests/negative_vectors.php` (deferred to
the parent merge window; no workflow edit made here).
