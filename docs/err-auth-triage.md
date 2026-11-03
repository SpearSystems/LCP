# ERR-002 / ERR-012 auth-family error-code triage (ARB wave 1.5)

- Lane: `W15ErrCodeTriage`; branch `w15/errcode-triage` from `dd84f80`.
- Trigger: the coverage-ledger finding recorded under `docs/SPEC-COVERAGE.md`
  ERR-002 — `test_bearer_with_wrong_api_key_is_rejected` asserts LCP-012 for
  a wrong bearer key "where §5 maps identity failures to LCP-002".
- Scope of this report: the §5 auth family only (identity vs. replay
  protection). Files touched by this PR: this file. No production code, no
  tests, no matrix rows were changed here — every branch that needs a
  behavior change is routed to an issue with a repro (§7).

## 1. Verdicts

| # | Condition | Ruling | Action |
|---|---|---|---|
| V1 | Wrong bearer key for a recognized sender | **Code is wrong, not (just) the test.** `auth.py:137` stamps LCP-012; §5 confines LCP-012 to HMAC-signature/timestamp failures and assigns the identity axis to LCP-002. The shipped test passes only because it never reaches that branch. | Issue #142 (platform). Wire-visible error code on 401 = LEP-material; behavior NOT changed in this PR. |
| V2 | Expired timestamp (outside replay window) | **LCP-012 confirmed** — verbatim §5. LEP-0002's reading agrees; the negative-vector corpus and the platform agree with §5 as written. | None. |
| V3 | Bad/malformed timestamp, replay-window boundary, idempotency-key header conditions, scope reuse of LCP-002 | **Spec text silent or narrower than implemented behavior**; implementations are internally consistent but unassigned by §5. | Issue #143 (spec clarification; candidate §12 audit item). |

## 2. The definitive §5 auth-family table (verbatim basis)

Spec anchors (verbatim, `SPEC.md` at `dd84f80`):

> §5 (lines 515, 525): `| LCP-002 | UNKNOWN_SENDER | 401 |` / `| LCP-012 | SIGNATURE_INVALID | 401 |`

> §5 (lines 547–551): "`LCP-002 UNKNOWN_SENDER` covers an unrecognized `sender_id`. `LCP-012 SIGNATURE_INVALID` covers a recognized sender whose HMAC signature is invalid or timestamp is expired — these are different failure modes (identity vs. replay protection) and now have distinct codes."

> §14 review log (line 912): "| `LCP-002` conflates identity + signature | Split into `LCP-002` (identity) + `LCP-012` (signature) | §5 |"

> §9 Authentication (lines 689–691): "Two auth methods: 1. **API keys** (Bearer token) — for simple integrations. 2. **HMAC** — `X-LCP-Signature`, `X-LCP-Timestamp`, `X-LCP-Idempotency-Key` headers (HTTP binding)."

> §9 (lines 712–715): "`X-LCP-Idempotency-Key` MUST match `lcp.message.idempotency_key` on mutating messages. Receivers MUST reject requests outside the acceptable timestamp window before processing PII. The default replay window is five minutes; deployments MAY choose a shorter window."

Conditions → codes, with reference-platform reality (`implementations/reference-platform/lcp_platform/`):

| Condition | §5/§9 assigned code (textual basis) | Platform emits | Pinned by | Verdict |
|---|---|---|---|---|
| Unrecognized `sender_id` | LCP-002 (547 verbatim) | `LCP-002` "Unknown sender" (`auth.py:128`) | role/identity assertions `test_platform.py:371`, `test_adversarial_e2e.py:323` | ✅ conforms |
| Envelope `sender_id` ≠ `X-LCP-Sender-Id` header | LCP-002 (identity axis: 549–551 + 912) | `LCP-002` (`auth.py:115`) | `test_adversarial_e2e.py` identity probes | ✅ conforms |
| No sender identity at all | LCP-002 (default of `AuthenticationError`, `auth.py:27`; identity axis) | `LCP-002` (`auth.py:121`) | — (no direct assertion; gap noted) | ✅ conforms (unasserted) |
| **Wrong bearer key, recognized sender** | **LCP-002** — see ruling §3. LCP-012's verbatim scope is "HMAC signature is invalid or timestamp is expired"; a bearer mismatch is neither (§9 lists API keys and HMAC as two *different* methods) | `LCP-012` "Invalid bearer credential" (`auth.py:137`) | **no test reaches this branch** (§5 proof C: the "bearer" test is short-circuited by `service.py:115`) | ❌ **code violates §5** → issue #142 |
| HMAC signature invalid (recognized sender) | LCP-012 (548 verbatim) | `LCP-012` (`auth.py:162`) | `test_adversarial_e2e.py:353,366`; vectors `lcp-neg-sig-001` | ✅ conforms |
| Timestamp expired (outside replay window) | **LCP-012** (549 verbatim: "…or timestamp is expired") | `LCP-012` (`auth.py:145`) | `test_adversarial_e2e.py:208` (360 s stale); vector `lcp-neg-replay-001` | ✅ conforms — settles the LEP-0002 adjacent case |
| Timestamp malformed / unparseable / naive | Not verbatim assigned (549 names "expired", not "invalid"); failure axis is replay protection → LCP-012 family | `LCP-012` (rejected at `auth.py:145` via `_timestamp_is_fresh` → `False`) | vectors `lcp-neg-replay-002/003/004`; SDK/corpus agree | ⚠️ consistent behavior, spec wording gap → issue #143 |
| Replay-window boundary (Δ exactly = window) | Unassigned — §9 says reject "outside" but never says whether Δ == 300 s is inside or outside | `<=` at `auth.py:67` → nominally **inside/accept**; integer-second truncation makes the exact instant untestable on a wall clock | no vector/test pins it (corpus uses 4801 s) | ⚠️ implementation choice; pin it → issue #143 |
| `X-LCP-Idempotency-Key` header missing on mutating route | Unassigned — §9 states the MUST; §5 assigns no code for the violation | `LCP-012` 401 (`service.py:115-120`; `auth.py:147`) | `test_http_requires_json_and_idempotency_headers` (asserts LCP-012 via the pre-check) | ⚠️ unassigned condition → issue #143 |
| Header ≠ envelope idempotency key | Unassigned (§9 MUST; no §5 code) | `LCP-012` 401 (`auth.py:149`) | — | ⚠️ unassigned condition → issue #143 |
| Missing `X-LCP-Timestamp`/`X-LCP-Signature` headers | Arguably replay-protection axis, but verbatim 548 requires "a recognized sender whose HMAC signature is invalid" — a *missing* header is neither invalid-signature nor expired-timestamp | `LCP-012` (`auth.py:143`) | **uncovered** — `test_http_requires_json_and_idempotency_headers` asserts LCP-012, but its request short-circuits at the `service.py:115` pre-check and never reaches `auth.py:143` | ⚠️ wording gap → issue #143 |
| Scope violation (recognized sender, wrong scope) | Unassigned — LCP-002's name ("UNKNOWN_SENDER") misnames an authorization failure | `LCP-002` (`auth.py:165`) | `test_platform.py:371`, `test_adversarial_e2e.py:323` | ⚠️ reuse unratified → issue #143 |

## 3. Ruling on the ledger finding (wrong bearer key): CODE, verbatim

The ledger note said "§5 *implies* LCP-002". After reading the text line by line, the
ruling is stronger than implication — it is what the verbatim text **excludes**:

1. `LCP-012 SIGNATURE_INVALID` is defined as covering "**a recognized sender whose
   HMAC signature is invalid or timestamp is expired**". In the bearer branch
   (`auth.py:131-137`) there is no HMAC signature and no timestamp check at all —
   §9 explicitly enumerates "Two auth methods", API keys *versus* HMAC. A failed
   API-key comparison is definitionally outside the LCP-012 text.
2. §5's own taxonomy sentence names the axis: "these are different failure modes
   (**identity** vs. replay protection)". A credential that fails to validate the
   sender is an identity failure; nothing replay-related was attempted.
3. §14 records the split rationale: "`LCP-002` (identity) + `LCP-012` (signature)".
4. The platform's *own* convention agrees: the header/envelope mismatch
   (`auth.py:115`) and unknown sender (`auth.py:128`) — same identity axis — are
   LCP-002, and `AuthenticationError`'s default code is LCP-002 (`auth.py:27`).
   Line 137 is the sole identity failure stamped with the signature code.

**Which side is wrong:** the *code* (`auth.py:137`), and the test
(`test_api_keys.py::test_bearer_with_wrong_api_key_is_rejected`) faithfully asserts
the wrong code. A test-only citation fix is therefore impossible: asserting LCP-002
against the current platform **fails** (proof B below). Because the fix changes a
wire-visible 401 error code, it is a "change to … error taxonomy … security" and an
"authentication rule" per `governance/LEP.md` ("When an LEP is required") — LEP
material. This PR files the issue with the repro and the pre-validated one-line fix
plan; it does not change behavior. (Counter-consideration, stated for the record:
`governance/LEP.md` also allows a plain PR for "a bug that restores the published
behavior"; maintainers may rule that §5:547–551 + §14:912 *is* the published
behavior and fast-track. That call belongs to maintainers, not to this lane.)

**Additional defect discovered (coverage honesty):** the named test never exercises
the bearer branch. It sends no `X-LCP-Idempotency-Key`, so `service.py:115` rejects
it first with the idempotency pre-check's own LCP-012 (`proof A2` output: message
`X-LCP-Idempotency-Key must match the envelope`, not `Invalid bearer credential`).
Its pass/fail is blind to `auth.py:137` — the bearer mismatch branch has **zero**
test coverage today. The issue asks for the test to be strengthened (send the
matching header, assert the message and the code together) as part of any fix.

## 4. Adjacent case confirmed: expired timestamp = LCP-012

§5 line 549 assigns "timestamp is expired" to LCP-012 **verbatim**; the LEP-0002
evidence reading is correct and W1NegVectors' confirmation stands. LEP-0002 itself
proposes only §9 clarifications (canonical timestamp string), reassigns no §5 code,
and its replay-window rejection discussion is consistent with LCP-012
(`governance/LEPs/LEP-0002…md`, "Verifier asymmetry" evidence item). Platform
`auth.py:145` + `test_adversarial_e2e.py:208` (offset `window+60`) + corpus
`lcp-neg-replay-001` (4801 s) all agree. **No conflict; no action.**

## 5. Planted-failure proofs (recorded transcripts)

All runs on macOS, repo venv (`python 3.14.7`/CI-equivalent suite), worktree at
`dd84f80`, `PYTHONPATH=implementations/reference-platform python -m unittest discover -s implementations/reference-platform/tests`.

- **Baseline (unmodified tree):** `Ran 63 tests … OK (skipped=2)` — includes
  `test_bearer_with_wrong_api_key_is_rejected` (green, pinning LCP-012).
- **Repro (unmodified tree):** recognized sender `pub_123`, `Bearer api-key-wrong`:
  - with matching idempotency header (reaches `auth.py:137`):
    `wrong bearer key   -> 401 {"code": "LCP-012", "message": "Invalid bearer credential"}`
  - without the header (what the shipped test does — short-circuit at `service.py:115`):
    `wrong key no idem  -> 401 {"code": "LCP-012", "message": "X-LCP-Idempotency-Key must match the envelope"}`
  - unknown sender for contrast (same identity axis, correct code):
    `unknown sender     -> 401 {"code": "LCP-002", "message": "Unknown sender"}`
  - valid key control: `valid bearer key   -> 200 RECEIVED` (rejection fires only on violation).
- **Proof A — spec-correct assertion fails on the real violation.** Plant: strengthen
  the test (add `X-LCP-Idempotency-Key: bearer-test-001`, assert `LCP-002`) against
  the **unmodified** code → `FAILED (failures=1)` with
  `+ LCP-002` (actual `LCP-012`). The mismatch is genuine code behavior, not a
  citation typo.
- **Proof B — candidate fix preserves rejection, is surgical.** Plant: `auth.py:137`
  `LCP-012` → `LCP-002`. Repro becomes
  `wrong bearer key   -> 401 {"code": "LCP-002", "message": "Invalid bearer credential"}`
  (still refused, still 401, message unchanged; idempotency pre-check and unknown-
  sender rows untouched). Full suite with the strengthened test: `Ran 63 tests … OK`.
- **Proof C — the coverage gap.** With only the code plant (test unmodified), the
  full suite is still `OK` — confirming no current test observes `auth.py:137`.
- **Boundary probe (unmodified code):** `_timestamp_is_fresh(x, 300)`: `299s→True`,
  `300s→False` (wall-clock ε artifact of the `<=` at `auth.py:67` on truncated
  integer-second strings), `301s→False`; `'0000-00-00T00:00:00Z'→False`,
  `'not-a-timestamp'→False`, `'2026-08-15T10:20:00'` (naive)`→False`. The nominal
  `Δ == window` instant is accepted; no spec text, vector, or test pins this.

## 6. Recommended matrix edits (coverage lane; not applied here)

The matrix (`docs/SPEC-COVERAGE.md`) is outside this lane's grant; the exact rows
to apply once the issues land:

- **ERR-002** stays `green` via the role/identity assertions; replace the finding
  note with: `bearer-mismatch branch emits LCP-012 contrary to §5 identity axis — pending #142; auth.py:137 untested` .
- **ERR-012** keep `test_bearer_with_wrong_api_key_is_rejected` and
  `test_http_requires_json_and_idempotency_headers` listed only with the
  annotation `(both assert the LCP-012 emitted by the service.py:115 pre-check; the
  bearer branch auth.py:137 and the missing-HMAC-headers branch auth.py:143 are
  unreachable from these requests)`; when #142 lands, move the strengthened bearer
  test to ERR-002.
- If #143 clarifies the idempotency/missing-header/malformed-timestamp/scope
  conditions, no row change is needed (current rows stay green); if §5 assigns new
  codes, add rows per the checker's format.

## 7. Issues filed

1. **#142 (platform — bearer mismatch code).** Wrong bearer key for a recognized
   sender is rejected `401 LCP-012 SIGNATURE_INVALID` (`auth.py:137`); §5 assigns the
   identity axis to `LCP-002 UNKNOWN_SENDER` (§2 ruling; §5 proofs A2/B/C repros
   above); fix requires LEP maintainer ruling (LEP-material per `governance/LEP.md`),
   fix plan attached; bearer branch currently has zero test coverage.
2. **#143 (spec clarification; candidate §12 audit item).** §5/§9 leave unassigned:
   credential-validation failures under LCP-002's "unrecognized `sender_id`"
   wording; malformed timestamps (implemented LCP-012); replay-window boundary
   inclusivity at Δ == window (implemented accept); missing/mismatched
   `X-LCP-Idempotency-Key` (implemented LCP-012 401); scope violations reusing
   LCP-002's name. Clarification-only; no wire change proposed by this issue.

## Zero silent choices

Every branch of the triage is stated with its textual basis and disposition:
V1 is a code violation proven by repro, routed to an issue because the behavior
change is LEP-material (not because the evidence is thin); V2 confirms LEP-0002's
reading verbatim, so no fix or issue; V3 items are consistency-plus-wording-gaps,
filed as clarification rather than silently re-labeled "green". The tempting cheap
win — editing only the test's asserted code — was executed as Proof A specifically
to show it fails: the test cannot be fixed without the platform. No files other
than this report were touched by this lane.

## 4. Resolution (2026-11-03)

The §5 text gap the V3 row named is closed. SPEC §5 now assigns the
replay-protection axis explicitly (missing timestamp/signature headers,
malformed or timezone-naive timestamps, missing or mismatched idempotency
headers, inclusive and symmetric replay boundary) and states that the
identity/authorization axis — unknown sender, sender/header disagreement, a
failed API-key check, and a missing route scope — is `LCP-002`, with no
re-coding between families.

Behaviour is unchanged; only the normative assignment was added. Each
condition is pinned by a named test in
`implementations/reference-platform/tests/test_auth_code_assignment.py` and
mapped in `docs/SPEC-COVERAGE.md` rows `ERR-014`…`ERR-019`, so the clauses
cannot drift back to being implementation-only.
