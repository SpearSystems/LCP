# Release record — v1.2.0

> **Status: NOT RELEASED — prepared on 2026-11-04.** No `v1.2.0` tag exists in
> this repository, no registry carries a `1.2.0` package, no release workflow
> has been run for this version, and no container was published. Version
> preparation and a maintainer decision are approval to attempt the release,
> not the release itself: a release exists only once the tag resolves, every
> tag workflow is green, and each registry coordinate reads back its version.
>
> `v1.1.1` remains the current published release and stays immutable.

## 1. Release identity

- [x] `SDK_VERSION` and every package manifest that carries a version field:
      `1.2.0` (`python3 tools/check_sdk_versions.py` exits 0).
- [ ] Release tag: **not created.** No `v1.2.0` tag and no
      `implementations/sdk/go/v1.2.0` tag exist; the checklist that creates them
      is [RELEASE.md](RELEASE.md#v120-release-block-prepared-unpublished).
- [ ] Published commit: none — no commit has been tagged.
- [ ] GitHub release, signed assets, SBOMs, provenance, Sigstore bundles: none.
- [ ] Reference container at `1.2.0`: not built or pushed for this version.
- Shape: additive MINOR over `v1.1.1`. No envelope field is added or made
  required, no enum value is removed, and every new surface is advertised
  through `capabilities()`. The three stateful surfaces (batch, event
  subscriptions, negotiation) ship **off** in the reference platform
  (`LCP_FEATURE_BATCH`, `LCP_FEATURE_EVENTS`, `LCP_FEATURE_NEGOTIATION`, all
  default `off`), so a 1.0 or 1.1 peer sees an unchanged core block.

## 2. Scope

v1.2.0 is the MINOR that carries the additive wire items plus the platform-only
depth and developer-experience work of the
[v1.2 execution plan](V1.2-EXECUTION-PLAN.md). What is in this repository at
the prepared commit:

**Wire additions (opt-in, capability-gated)**

- Bounded batch submission — `POST /v1/lcp/leads/batch`, ≤ 100 items / 1 MiB,
  `207` with one per-item receipt in input order, per-item isolation, no
  cross-item transaction (W2).
- Event subscriptions — registration/read/deactivate plus
  `GET /v1/lcp/events?cursor=`, signed at-least-once delivery, retry ladder and
  dead-letter, cursor replay under a TTL, PII-free filter key set (W3).
- Agent-to-agent negotiation — `quote` and `counter` message types, bounded
  rounds, monotone counters, floor refusal instead of clamping, single-use
  binding token (W5, LEP-0009).
- `PROCESSED` acknowledgement — terminal-success `ACCEPTED → PROCESSED`, never
  payable-bearing (W1-01, D2).
- Multi-currency pricing — `currency`, `original_price_cents`, `fx_rate`,
  `fx_source`, `fx_timestamp`, `fx_expires_at`, with an auction comparability
  rule and ISO 4217 minor-unit conversion (W1-09, D7).
- `location.street_address` and `provenance.ip_address`, both post-only and
  `ping_safe: false` (W1-03, W1-04, D4, D5).
- Ping-side extension admission, gated on a registration's ping-safety
  declaration; `lcp.platform.assessment`, `lcp.buyer.criteria`,
  `lcp.intake.raw_form`, and `lcp.outbound.call_request` registered (W1-05…
  W1-08, D6, LEP-0007).

**Platform-only depth and DX (no wire change)**

- Versioned requirement-profile store with immutable `{profile_id, version}`
  rows, activation/supersede/rollback, and a per-match audit record (W4-01);
  the allowlisted predicate language over a typed path registry (W4-02); and
  versioned country/region/postal service-area datasets (W4-03).
- Retention windows for stored envelopes and for decision/audit history, with
  past-window history made unreadable before the sweep deletes it, envelope
  redaction through the erasure path, and `tools/archive_store.py`
  export/verify/import (W4-05, W4-06).
- PII sensitivity taxonomy with `x-lcp-pii` on every schema property and
  load-time refusal of unannotated, downgraded, or prohibited classes (W6-04,
  D9, LEP-0011).
- `core_schema_version` and `exclusivity_default` in `capabilities()` (W1-02,
  W1-10, D3).
- SPEC split into `spec/00…05` behind a normative index, with anchors preserved
  (W6-01, D10) and a generated specification site (W6-02).
- Self-hostable conformance runner (W6-03, D11).
- MCP transport work — one cached client, blocking upstream calls on worker
  threads — plus the batch/subscription/negotiation tools (W6-06).
- Batch helpers in all ten SDKs; schema bundles regenerated from the 20
  normative schemas (W1-11, W2-04, W3-05).

These platform items are deployment-scoped and add no field to the universal
core, so they are invisible to a peer that never reads them. The
[CHANGELOG](../CHANGELOG.md) names them at the same granularity.

## 3. Accepted LEPs and decisions

| LEP | Status (registry) | Surface in this release | Work item |
|---|---|---|---|
| LEP-0001 | Accepted 2026-11-04 (maintainer; D1 trigger waiver) | Batch submission and event subscriptions, both gated | W2, W3 |
| LEP-0007 | Accepted 2026-11-04 (maintainer; D1 trigger waiver) | `{org}.outbound.call_request` as a namespaced `event` value; the new-message-type alternative stays rejected | W1-08 |
| LEP-0009 | Accepted 2026-11-04 (maintainer; D8, D1) | `quote`/`counter` negotiation under `LCP_FEATURE_NEGOTIATION` | W5 |
| LEP-0010 | Accepted 2026-11-04 (maintainer; D7, D1) | Multi-currency pricing and the comparability rule | W1-09 |
| LEP-0011 | Accepted 2026-11-04 (maintainer; D9, D1) | PII classes annotated on every schema property | W6-04 |

The 2026-11-03 Deferrals of LEP-0001 and LEP-0007 were superseded by the dated
**D1 trigger waiver** in [DECISIONS-v1.2.md](DECISIONS-v1.2.md): the triggers
were not met, that is recorded as such, and the re-evaluation date is
**2027-05-04**. Every item built under the waiver is additive-only,
advertised, off by default, and carries deterministic evidence (vector or
test) in place of adopter signal.

Decisions carried by this release, each with the date it was signed:

| Decision | What it did here |
|---|---|
| D1 | Waived the v1.1 triggers for the enumerated items, with the three compensating constraints |
| D2 | Added `PROCESSED` as a terminal-success ack transition that never touches money semantics |
| D3 | Documented `lcp.version` as the core schema version and advertised `core_schema_version`; no envelope field |
| D4 | `location.street_address`, optional, never ping-safe |
| D5 | `provenance.ip_address`, post-only, with `ip_hash` unchanged as the ping-safe form |
| D6 | Registered `{org}.intake.raw_form` as a bounded, post-only, `restricted` extension |
| D7 | Multi-currency pricing, with non-comparable offers excluded rather than coerced |
| D8 | `quote`/`counter` as the negotiation surface, capability-gated and off by default |
| D9 | Five PII classes, `x-lcp-pii` on every property, no downgrade at load time |
| D10 | SPEC split into five normative documents plus an index |
| D11 | Self-hostable conformance runner; no certification claim |
| D12 | Second-maintainer preparation only — no claim of independent approval |

## 4. Version files changed

Every file below was `1.1.1` and is now `1.2.0`; nothing else in the tree claims
a package version.

| File | Field |
|---|---|
| `SDK_VERSION` | the coordinated version, read by the platform's capability advertisement |
| `implementations/sdk/python/pyproject.toml` | `[project] version` |
| `implementations/reference-platform/pyproject.toml` | `[project] version` |
| `implementations/mcp-server/pyproject.toml` | `[project] version` |
| `implementations/sdk/typescript/package.json` | `version` |
| `implementations/sdk/typescript/package-lock.json` | root `version` and `packages.""` `version` |
| `implementations/sdk/csharp/src/LcpSdk.csproj` | `<Version>` |
| `implementations/sdk/java/pom.xml` | project `<version>` |
| `implementations/sdk/kotlin/build.gradle.kts` | `version` |
| `implementations/sdk/rust/Cargo.toml` | `[package] version` |
| `implementations/sdk/rust/Cargo.lock` | the `lcp-sdk` package entry |
| `implementations/sdk/ruby/lcp_sdk.gemspec` | `spec.version` |

Two SDKs have no version field to bump, and their version is the tag:

- **Go** — `implementations/sdk/go/go.mod` carries no version; the module is
  versioned by the nested tag `implementations/sdk/go/v1.2.0`. A root tag alone
  yields only a `v0.0.0-…` pseudo-version for that module.
- **PHP** — `implementations/sdk/php/composer.json` carries no `version` key
  (Packagist derives it from the mirrored tag), and **Swift** —
  `implementations/sdk/swift/Package.swift` has no version either; both are
  versioned by the repository tag.

## 5. Gate evidence observed

Observed on the preparation host against the prepared working tree, with the
version files already at `1.2.0`:

| Check | Command | Result |
|---|---|---|
| Coordinated version | `python3 tools/check_sdk_versions.py` | exit 0 — `SDK version synchronization passed: 1.2.0` |
| Schema/SDK sync | `python3 tools/check_sdk_schema_sync.py --check` | exit 0 — `passed for 10 SDKs and 20 schemas` |
| Docs citations | `python3 tools/check_doc_citations.py --strict` | exit 0 |
| OpenAPI parses after the pre-tag correction in §6 | `python3 -c "import yaml;yaml.safe_load(open('api/lcp-openapi.yaml'))"` | 15 paths parsed |

**Not observed here, and not claimed.** This record does not restate a
conformance-vector count, a platform-test count, an SDK-interop result, a
coverage-matrix number, a security sweep, or a benchmark: those are the W7-02
gate sweep and their artifacts are the evidence, not a release note. A green
version/sync/citation triple means the tree is internally consistent; it says
nothing about behaviour, and this record treats it that way.

## 6. Pre-tag correction made during preparation

- **`api/lcp-openapi.yaml:273` — `X-LCP-Content-SHA256` pattern.** The
  specification said `^[a-f0-9]{63}$`, which is not a SHA-256 hex digest and
  contradicts both the platform (which validates a 64-hex digest) and the other
  three sha256 patterns in the same file. Restored to `^[a-f0-9]{64}$` before
  the tag, so the published specification matches the enforced behaviour. This
  is a documentation correction inside the version being prepared; it changes
  no wire shape and is not a new version.

## 7. Known limitations carried by this release

1. **No .NET toolchain on the build host.** `dotnet`, `mono`, `csc`, and `mcs`
   are all absent, so the C# SDK suite cannot be executed here and this record
   makes no local C# pass claim. The `sdk.yml` job is the executing gate. The
   audit's C# verifier item (a 9-digit fractional second under
   `DateTimeOffset.TryParse`) stays **INCONCLUSIVE** for the same reason, as
   recorded in [AUDIT-2026-11.md](AUDIT-2026-11.md) — a dated limit, not a
   confirmation.
2. **Swift is present on this host, contrary to the handoff assumption.**
   `swift --version` reports Apple Swift 6.4 and the package has a populated
   `.build` directory. This record still makes no local Swift pass claim: the
   suite was not run in this lane, and `sdk.yml` is the executing gate. The
   handoff note that both C# and Swift toolchains are absent is corrected here
   to C# only.
3. **History and audit writes use a process-local bounded queue.** The intake
   path does not block on them, which is the point, but the queue does not
   survive the process: a crash can lose queued rows while the lead record
   itself persists (documented in `docs/OPERATIONS.md`). Durable cross-process
   delivery is future work, not a property of this release.
4. **The three stateful surfaces are off by default.** A deployment that does
   not set `LCP_FEATURE_BATCH`, `LCP_FEATURE_EVENTS`, or
   `LCP_FEATURE_NEGOTIATION` gets a `404` and no advertisement — invisible
   rather than half-enforced. Enabling them is an operator decision.
5. **No batch-throughput baseline is recorded.** The performance workflow's
   measured rows cover the candidate index and Postgres intake/delivery paths;
   there is no batch-submission row in `docs/OPERATIONS.md`, so the changelog
   makes no throughput or adoption claim for it.
6. **The conformance runner makes no certification claim.** No second operator
   has run the image and published a matching report, which is D11's exact
   condition; a passing report from this repository's instance is evidence
   about one deployment, not a certification.
7. **Independent release approval does not exist.** There is one human with
   release duties; `CODEOWNERS` and the ruleset change are prepared, not
   claimed as applied (D12).

## 8. Approval and publication state

**Approved to build, not executed — therefore not released.**

- The maintainer decisions in [DECISIONS-v1.2.md](DECISIONS-v1.2.md) authorize
  the work and `v1.2.0` as a MINOR release containing D2–D11.
- The single LEP/publish gate that remains is a human one: the tag and the
  protected publication jobs.
- Nothing was tagged, published, mirrored, or announced in the preparation of
  this record. No registry was written to; no container was built or pushed; no
  release workflow was dispatched.

Still to do, in order, from [RELEASE.md](RELEASE.md#v120-release-block-prepared-unpublished):
the W7-02 gate sweep, the nested Go tag, the root tag, the container
public-pull pre-flight, the Packagist mirror update, the protected publication
approvals, and the registry read-back that replaces the empty table below.

## 9. Verified publication

Not applicable yet — the release has not been executed. This table is filled
from registry read-back after the tag workflows complete; an entry here is a
measurement, never an intention.

| Registry | Coordinate | Version |
|---|---|---|
| PyPI | `lcp-sdk`, `lcp-mcp-server`, `lcp-reference-platform` | not published |
| npm | `@spear-systems/lcp-sdk` | not published |
| NuGet | `LcpSdk` | not published |
| crates.io | `lcp-sdk` | not published |
| RubyGems | `lcp-sdk` | not published |
| Maven Central | `systems.spear:lcp-sdk`, `systems.spear:lcp-sdk-kotlin` | not published |
| Swift Package Manager | repository tag | not published |
| Go module | `…/implementations/sdk/go` | not published (needs `implementations/sdk/go/v1.2.0`) |
| Packagist | `spearsystems/lcp-sdk` | not published (mirror not updated) |
| GHCR | `ghcr.io/spearsystems/lcp-reference-platform` | no `1.2.0` image |
