# Release record — v1.3.0

> **Status: RELEASED on 2026-11-19** from tag `v1.3.0` at merge commit
> `52a11fb46c75b8aeec6c67f5eac9336dbdbb66a8` (PR
> [#249](https://github.com/SpearSystems/LCP/pull/249)). Every registry
> coordinate in [§5](#5-verified-publication) was read back after publication.
>
> `v1.2.0` remains published and immutable.

## 1. Release identity

- [x] `SDK_VERSION` and every package manifest that carries a version field:
      `1.3.0` (`python3 tools/check_sdk_versions.py --check` exits 0).
- [x] Release tag `v1.3.0` and Go module tag `implementations/sdk/go/v1.3.0`,
      both annotated, both on the published commit.
- [x] Published commit: `52a11fb46c75b8aeec6c67f5eac9336dbdbb66a8`, the merge
      of PR #249. Its tree is identical to the PR head `abdaf02`, on which the
      full CI matrix passed.
- [x] GitHub release with signed assets, SBOMs and provenance:
      <https://github.com/SpearSystems/LCP/releases/tag/v1.3.0>.
- [x] Reference container at `1.3.0`: published; digest in §5.
- Shape: additive MINOR over `v1.2.0`. No envelope field is made required, no
  enum value is removed, and no error code is added. Every new surface is
  advertised through `capabilities()`.

## 2. Scope

Decisions: [DECISIONS-v1.3.md](DECISIONS-v1.3.md). Implementation: PR
[#249](https://github.com/SpearSystems/LCP/pull/249).

- **LEP-0012, sale lifecycle:** `ack.disposition`, money-bearing
  `ACCEPTED`/`REJECTED`/`REFUNDED` details, `url_template` postbacks, and
  the `lcp.affiliate.tracking` and `lcp.test.outcome` extensions. Off by
  default in the reference platform (`LCP_FEATURE_SALE_DISPOSITION`,
  `LCP_FEATURE_URL_POSTBACKS`, `LCP_FEATURE_TEST_OUTCOMES`).
- **LEP-0013, regulated identifiers:** the `x-lcp-regulated` annotation and
  its gate. The reference platform implements only the refusing side and
  never advertises `features.regulated_identifiers`.
- **LEP-0014, `personal_loans` 2.0.0.**
- **Fixes since v1.2.0:** SDK verify and retry parity across all ten SDKs,
  the Ruby SDK's UTF-8 schema loading, `__version__` in the Python packages,
  the reference image's `krb5-libs` patch, and dependency updates.

## 3. Verification evidence

Self-audit run on the build host (macOS, arm64) on 2026-11-19, at the
release candidate on branch `lep/0012-0013-sale-lifecycle`. This is the
evidence D2 of the decision record relies on. It is not an independent
review.

| Suite | Command | Result |
|---|---|---|
| Reference platform | `PYTHONPATH=implementations/reference-platform python -m unittest discover -s implementations/reference-platform/tests` | 381 tests OK, 5 skipped (PostgreSQL integration runs in CI) |
| Repository tools | `python -m unittest discover -s tools/tests` | 263 tests OK |
| Conformance | `python test-vectors/conformance.py` | 139 passed, 0 failed |
| Registry and policy checks | `check_lep_registry`, `check_logging_hygiene`, `check_universal_core`, `check_pii_annotations`, `check_reserved_fields`, `check_doc_citations --strict`, `check_coverage_matrix`, `check_mermaid_blocks` | all pass |
| SDK sync | `check_sdk_versions --check`, `check_sdk_schema_sync --check`, `check_sdk_surface` | 1.3.0; 10 SDKs and 21 schemas in sync; 130 of 130 surface cells present |
| Spec site | `build_spec_site.py --check` | up to date |
| Python SDK | `unittest discover -s implementations/sdk/python/tests` | 35 tests OK |
| Go SDK | `go test ./...` | ok |
| TypeScript SDK | `npm test` | pass; negative vectors 23 executed, 1 gap reported |
| Rust SDK | `cargo test --locked` | 15 passed |
| PHP SDK | `php tests/shared_vector.php && php tests/negative_vectors.php` | pass; negative vectors 23 executed, 1 gap reported |
| Ruby SDK | three test files, with and without `LANG` set | pass in both locales |
| Java SDK | `mvn -Dgpg.skip=true verify` | 21 tests, BUILD SUCCESS |

**Not run on the build host:** C# (no .NET SDK), Kotlin (no JDK 17
toolchain for Gradle) and Swift (no XCTest without Xcode). These suites run
in the `SDK compatibility` CI workflow only, and this record makes no local
pass claim for them.

The reported gap is `lcp-neg-mode-001`, which is a platform invariant
(`LCP-013`) that an SDK cannot exercise. It is reported the same way in
v1.2.0.

**CI:** the full matrix on PR #249 (Test, Security and supply chain, SDK
compatibility for all ten SDKs, Negative vectors, Spec Coverage Matrix,
Performance, Repo hygiene, Attribution policy) must be green at the merge
commit before the tag is pushed.

## 4. Release steps

Followed the [v1.3.0 release block](RELEASE.md#v130-release-block-published-2026-11-19):
gates at the merge commit, the Go module tag before the root tag, the
container public-pull pre-flight (anonymous pull token issued, package
visibility `public`), the Packagist mirror dispatch, and approval of the
protected publication jobs after every tag workflow passed.
