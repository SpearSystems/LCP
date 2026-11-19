# Release record — v1.4.0

> **Status: NOT RELEASED — prepared on 2026-11-19.** No `v1.4.0` tag exists
> yet, no registry carries a `1.4.0` package, and no container was published
> for this version. A release exists only once the tag resolves, every tag
> workflow is green, and each registry coordinate reads back its version.
>
> `v1.3.0` remains the current published release and stays immutable.

## 1. Release identity

- [x] `SDK_VERSION` and every package manifest that carries a version field:
      `1.4.0` (`python3 tools/check_sdk_versions.py --check` exits 0).
- [ ] Release tag `v1.4.0` and Go module tag `implementations/sdk/go/v1.4.0`:
      not created.
- [ ] Published commit: none.
- [ ] GitHub release, signed assets, SBOMs, provenance: none.
- [ ] Reference container at `1.4.0`: not published.
- Shape: additive MINOR over `v1.3.0`. No envelope field is made required, no
  enum value is removed, and no error code is added. No new `lcp.version`:
  neither LEP gates a wire field on one, so `lcp_versions` still ends at
  `1.3.0`.

## 2. Scope

Decisions: [DECISIONS-v1.4.md](DECISIONS-v1.4.md). Implementation: PR
[#251](https://github.com/SpearSystems/LCP/pull/251).

- **LEP-0015, buyer delivery mapping:** `schemas/buyer-mapping.json`
  (`format_version` `1.0`), the normative rules in
  [BUYER-MAPPING.md](BUYER-MAPPING.md), the `BMAP` vectors, the native
  1.3.0 buyer ack read, one bounded counter re-post, and `buyer_unconfirmed`
  as a known `rejection_reason`. The reference executor ships off
  (`LCP_FEATURE_BUYER_MAPPING`).
- **LEP-0016, `mortgage` 2.0.0:** whole-unit amounts with
  `amount_currency`, 1.0.0 frozen under `verticals/archive/`, and the rule
  that a receiver validates each message against the
  `attributes.schema_version` it names (suite `VER`).
- **Not in scope** (recorded in DECISIONS-v1.4 D4): per-vertical typed SDK
  builders, and LEP-0016's optional 1.0.0 usage metric and normalisation.

## 3. Verification evidence

**SELF-AUDIT.** The authoring lane ran this matrix on the build host
(macOS, arm64) on 2026-11-19. It ran at the release candidate on branch
`lep/0015-0016-buyer-mapping-mortgage`, after `main` was merged in. D2 of
the decision record relies on this evidence. It is not an independent
review.

| Suite | Command | Result |
|---|---|---|
| Reference platform | `PYTHONPATH=implementations/reference-platform python -m unittest discover -s implementations/reference-platform/tests` | 407 tests OK, 5 skipped (PostgreSQL integration runs in CI). Includes `test_buyer_mapping.py`, which runs every `BMAP` render, decode and refusal vector against the reference executor, and `test_vertical_versions.py` |
| Repository tools | `python -m unittest discover -s tools/tests` | 263 tests OK |
| Conformance | `python test-vectors/conformance.py` | 175 passed, 0 failed (includes the `VER` and `BMAP` suites) |
| Registry and policy checks | `check_lep_registry`, `check_logging_hygiene`, `check_universal_core`, `check_pii_annotations`, `check_reserved_fields`, `check_doc_citations --strict`, `check_coverage_matrix`, `check_mermaid_blocks`, `check_exception_expiry` | all pass (16 LEPs; 660 PII definitions annotated; 487 citations; 183 coverage clauses, 172 green, 11 exempt) |
| SDK sync | `check_sdk_versions --check`, `check_sdk_schema_sync --check`, `check_sdk_surface` | 1.4.0; 10 SDKs and 22 schemas in sync; 130 of 130 surface cells present |
| Release registries | `check_release_registries --version 1.4.0 --expect-absent` | no registry carries 1.4.0 |
| Spec site | `build_spec_site.py --check` | up to date |
| MCP server | `PYTHONPATH=implementations/mcp-server:implementations/sdk/python python -m unittest discover -s implementations/mcp-server/tests` | 19 tests OK |
| Python SDK | `unittest discover -s implementations/sdk/python/tests` | 35 tests OK |
| Go SDK | `go test -count=1 ./...` | ok |
| TypeScript SDK | `npm test` | pass; corpus 41 fixtures; negative vectors 23 executed, 1 gap reported |
| Rust SDK | `cargo test --locked` | 16 passed, on eight consecutive runs after the `find_key` fix below |
| PHP SDK | `php tests/shared_vector.php && php tests/negative_vectors.php` | pass; negative vectors 23 executed, 1 gap reported |
| Ruby SDK | three test files, with and without `LANG` set | pass in both locales |
| Java SDK | `mvn -Dgpg.skip=true verify` | 21 tests, BUILD SUCCESS |
| Swift SDK | `swift build` | builds; `swift test` needs XCTest (see below) |

**Not run on the build host:**

- **C#:** there is no .NET SDK on the host.
- **Kotlin:** Gradle finds no JDK 17 toolchain.
- **Swift tests:** XCTest is not available without Xcode.
- **`check_action_pins`:** this network sweep of GitHub refs was stopped after 11 minutes. No workflow changes in this release.

These run in CI only, and this record makes no local pass claim for them.

**Found and fixed during the audit.** The Rust SDK's `find_key` accepted any schema whose name ended with the requested name, searching a `HashMap` in random order. With `schemas/buyer-mapping.json` present, a ping could be validated against the buyer-mapping schema, so Rust suites failed on some runs. The lookup is now exact first, with a suffix accepted only on a `/` boundary, as in the Swift SDK. A unit test pins the case. Published bundles exclude `buyer-mapping.json`, so only `from_directory` on a repository checkout was exposed.

**Environment note.** The MCP suite must have the worktree's `implementations/sdk/python` on `PYTHONPATH`. Without it, the virtualenv's installed `lcp_sdk` (1.3.0) refuses the `mortgage` 2.0.0 example, and two tests fail.

The reported gap is `lcp-neg-mode-001`. It is a platform invariant (`LCP-013`) that an SDK cannot exercise, and it is reported the same way in v1.3.0.

**CI:** the full matrix on PR #251 must be green at the merge commit before the tag is pushed. The matrix covers Test, Security and supply chain, SDK compatibility for all ten SDKs, Negative vectors, Spec Coverage Matrix, Performance, Repo hygiene and Attribution policy.

## 4. Release steps

Follow the [v1.4.0 release block](RELEASE.md#v140-release-block). Record
the tag commit, the GitHub release, and each read-back registry coordinate
here once published.
