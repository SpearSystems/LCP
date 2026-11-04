> **Release page · Page 3 of 6**

> **Current release:** `v1.2.0` was published on 2026-11-05 from tag `v1.2.0`
> (merge commit `400c350b0e5ebf7ba3160e14b1fa78acb61ec57c`). The GitHub release
> and its signed evidence assets are at
> <https://github.com/SpearSystems/LCP/releases/tag/v1.2.0>; the record is
> [RELEASE-TICKET-1.2.0.md](RELEASE-TICKET-1.2.0.md).
>
> **Previous release:** `v1.1.1` (2026-11-04), tag `fa290ab` —
> <https://github.com/SpearSystems/LCP/releases/tag/v1.1.1>, record at
> [RELEASE-TICKET-1.1.1.md](RELEASE-TICKET-1.1.1.md).
>
> **Earlier release:** `v1.1.0` (2026-11-03), commit
> `68a22b5f7f36ea6321a21428267fbc5ab8bdf92c` —
> <https://github.com/SpearSystems/LCP/releases/tag/v1.1.0>, record at
> [RELEASE-TICKET-1.1.0.md](RELEASE-TICKET-1.1.0.md).
>
> This page explains how maintainers create a versioned LCP release and how
> adopters verify the release record before installing packages or deploying
> the reference container.

## What a version tag does

A tag such as `v1.0.0` starts the coordinated release process. The tag is
accepted only when it matches `SDK_VERSION` and the package metadata checked by
`tools/check_sdk_versions.py`.

Before a release, or when provisioning a new repository, configure the protected GitHub release environments
(`release`, `release-python-mcp`, and `release-python-reference`), branch rules,
and registry trusted publishers using the [maintainer release
setup](MAINTAINER-RELEASE-SETUP.md). The v1.0.1 release completed this flow
successfully; future releases repeat it with a new immutable version tag. The
final GitHub release record is created by a separate environment-protected job,
so package publication and release creation require explicit approval.

The tag runs these workflows for the same commit:

1. **Test** — conformance, SDK tests, reference-platform tests, and real
   PostgreSQL integration.
2. **Security and supply chain** — dependency audit, CodeQL, SBOM generation,
   hardened image build, and Trivy review.
3. **SDK compatibility** — schema synchronization and every language SDK test.
4. **Publish SDKs** — trusted or protected publication for the non-Python SDKs.
5. **Publish Python packages** — PyPI publication for `lcp-sdk`,
   `lcp-mcp-server`, and `lcp-reference-platform`.
6. **Publish, sign, and attest reference container** — GHCR image publication,
   Cosign signature, GitHub provenance, and SBOM attestations.
7. **Signed tagged release** — waits for all six upstream workflows to pass,
   then creates the GitHub release record.

If a required workflow fails or does not complete, the final release workflow
fails and does not create a GitHub release. Package registries may still retain
an artifact from a failed publication attempt; operators must use the registry's
immutable version rules and investigate before retrying. The non-Python SDK
publication jobs probe their immutable coordinates and skip packages already
published, so a corrected tag retry does not attempt to overwrite Java, Kotlin,
npm, crates.io, or RubyGems versions.

## Maintainer checklist

1. Update `SDK_VERSION` and every package's explicit version together.
2. Regenerate and check the canonical schema bundle:

   ```bash
   python3 tools/generate_sdk_models.py --write
   python3 tools/check_sdk_versions.py --check
   python3 tools/check_sdk_schema_sync.py --check
   python3 test-vectors/conformance.py
   ```

3. Run the complete local compatibility matrix where the language toolchains
   are available.
4. Push the release tag and, for the nested Go module, its submodule tag
   (a module in a subdirectory is versioned by `implementations/sdk/go/vX.Y.Z`;
   a root tag alone only yields `v0.0.0-…` pseudo-versions):

   ```bash
   git tag -a "implementations/sdk/go/v${SDK_VERSION}" -m "LCP Go SDK v${SDK_VERSION}"
   git push origin "refs/tags/implementations/sdk/go/v${SDK_VERSION}"
   ```

5. Push a tag whose name is exactly `v<SDK_VERSION>`:

   ```bash
   git tag -a "v${SDK_VERSION}" -m "LCP ${SDK_VERSION}"
   git push origin "v${SDK_VERSION}"
   ```

   A signed Git tag is recommended for maintainer provenance, but the release
   workflow also signs the release record with a GitHub OIDC-backed Sigstore
   identity.

6. Confirm that every tag workflow is green. Approve the protected package
   publication jobs and final release-record job in the `release` environment.
   The workflow then publishes `release-manifest.json`, the release notes,
   source SBOM, and their Sigstore bundles as GitHub release assets.
7. Record the released image digest and registry package URLs in the release
   ticket or deployment record. Do not deploy a mutable container tag.

Trusted registry configuration is an operator/maintainer prerequisite. Configure
protected `release` environments, branch/tag rules, and registry-specific
trusted publisher identities using the [maintainer release setup](MAINTAINER-RELEASE-SETUP.md)
and [SDK publication policy](SDK-ROADMAP.md#package-publication). The signed
release record itself is also created inside the protected environment. Do not
replace OIDC or protected environments with long-lived credentials just to make
a release pass.

## v1.2.0 release block (published 2026-11-05)

**State: released 2026-11-05.** The version files carry `1.2.0`; the tag,
the registry publications, the container, and the GitHub release do not exist
yet. Nothing in this section has been executed for `v1.2.0`, and an approved
checklist is not a release — only a pushed tag and read-back registry
coordinates are.

`v1.2.0` is an additive MINOR over `v1.1.1`: batch submission, event
subscriptions, negotiation, the `PROCESSED` acknowledgement, multi-currency
pricing, `location.street_address`, `provenance.ip_address`, the PII taxonomy,
and ping-side extension admission. The reference container ships the three
stateful surfaces **off** (`LCP_FEATURE_BATCH`, `LCP_FEATURE_EVENTS`,
`LCP_FEATURE_NEGOTIATION`); enabling them is an operator decision, not a
release step.

### Maintainer checklist

1. Confirm the coordinated version and the gates, from the exact candidate
   commit:

   ```bash
   python3 tools/check_sdk_versions.py --check
   python3 tools/check_sdk_schema_sync.py --check
   python3 tools/check_doc_citations.py --strict
   python3 tools/check_lep_registry.py
   python3 tools/check_pii_annotations.py
   ```

2. Regenerate and check the generated artifacts — they are committed, and a
   stale tree fails CI:

   ```bash
   python3 tools/generate_sdk_models.py --write
   python3 tools/build_spec_site.py --check
   python3 test-vectors/conformance.py
   ```

3. Run the local compatibility matrix for every toolchain present on the build
   host, and record which ones were absent. On the 2026-11-04 host there is no
   .NET and no Swift toolchain, so the C# and Swift suites are executed by
   their CI jobs only; a release record MUST say that rather than implying a
   local pass.

4. Push the **nested Go module tag** as well as the root tag. A module in a
   subdirectory is versioned by its own tag; a root `v1.2.0` alone only yields
   a `v0.0.0-…` pseudo-version for `implementations/sdk/go`:

   ```bash
   git tag -a "implementations/sdk/go/v1.2.0" -m "LCP Go SDK v1.2.0"
   git push origin "refs/tags/implementations/sdk/go/v1.2.0"
   ```

5. Push the root tag, whose name is exactly `v<SDK_VERSION>`:

   ```bash
   git tag -a "v1.2.0" -m "LCP 1.2.0"
   git push origin "v1.2.0"
   ```

6. **Container public-pull pre-flight — before approving the release record,
   not after announcing it.** A new container package is created private even
   when the repository is public, and an organization that restricts public
   packages blocks the visibility change outright. `container-release.yml`
   asserts the pull token before it writes release metadata; verify it
   independently as well:

   ```bash
   curl -s "https://ghcr.io/token?scope=repository:spearsystems/lcp-reference-platform:pull" \
     | grep -q '"token"' && echo "anonymous pull OK"
   gh api /orgs/SpearSystems/packages/container/lcp-reference-platform --jq .visibility
   docker pull ghcr.io/spearsystems/lcp-reference-platform@sha256:<digest>
   ```

   The package already exists, so this release changes its visibility only if
   someone changed it; a failure here blocks the release rather than being
   worked around after publication.

7. **Packagist mirror step.** `spearsystems/lcp-sdk` is registered from the
   `SpearSystems/lcp-sdk-php` mirror, which carries `composer.json` at the
   repository root. Push the `v1.2.0` tag to that mirror (its workflow mirrors
   each LCP tag) and confirm Packagist indexed it; the initial crawl after a
   submission can miss an already-pushed tag, and only a later push event or
   the package page's **Update** button indexes it:

   ```bash
   composer show -a spearsystems/lcp-sdk | grep -A2 versions
   ```

   `1.2.0` missing from that output is a failed release step, not a timing
   detail to be discovered by an adopter.

8. Approve the protected package-publication jobs and the final release-record
   job in the `release` environment once every upstream workflow is green.

9. Read back each registry coordinate, resolve the container tag to its
   digest, and write both into
   [RELEASE-TICKET-1.2.0.md](RELEASE-TICKET-1.2.0.md): PyPI (`lcp-sdk`,
   `lcp-mcp-server`, `lcp-reference-platform`), npm
   (`@spear-systems/lcp-sdk`), NuGet (`LcpSdk`), crates.io (`lcp-sdk`),
   RubyGems (`lcp-sdk`), Maven Central (`systems.spear:lcp-sdk`,
   `systems.spear:lcp-sdk-kotlin`), Packagist, the Go module at both tag
   coordinates, Swift Package Manager, and the GHCR digest. Deploy only the
   digest.

A failed step is resolved before the tag is pushed, or the tag is abandoned for
a new patch version; an occupied package version is never overwritten.

## v1.0.2 release evidence

The published patch release carried the repository-audit remediation
(attachment authorization, lifecycle transition enforcement, equivalent
ten-SDK validators, sensitive-vertical ping policy, MCP adapter hardening,
and more) plus the Kotlin SDK Jackson 3 compile fix, dependency pins, and
load-benchmark fixes, without changing schemas, message types, or the
universal wire contract. Its immutable tag and commit are recorded above.
Download the release manifest and evidence assets before consuming packages
or the reference image:

```bash
mkdir -p release-assets
gh release download v1.0.2 \
  --repo SpearSystems/LCP \
  --dir release-assets \
  --clobber
python3 tools/verify_release_evidence.py release-assets
```

The published release assets are the evidence for `v1.0.2`; do not rerun a
release dry run with an already-occupied version. Use the non-publishing dry
run below only for a new version after updating `SDK_VERSION` and package
metadata.

## Non-publishing release dry run

For the next patch, first update `SDK_VERSION` and every package manifest to
`1.0.3`, then run the workflow against the exact candidate commit:

```bash
gh workflow run release.yml \
  --repo SpearSystems/LCP \
  --ref main \
  -f tag=v1.0.3 \
  -f target_sha="$(git rev-parse HEAD)"
```

The dry run does not publish packages, push a container, or create a GitHub
release, and it never enters the protected final release-record job. It does
perform the coordinated version/schema/conformance checks, checks that the
proposed version is not already occupied in PyPI, npm, NuGet, Maven Central,
crates.io, RubyGems, or Packagist, generates source evidence for
every published package, signs each source archive/SBOM/provenance statement
with the release workflow's OIDC-backed Sigstore identity, verifies those
signatures, and uploads a 30-day `lcp-release-dry-run-*` workflow artifact.

For a dry-run signature, the expected identity is the branch ref used to start
the workflow, for example:

```bash
export WORKFLOW_IDENTITY="https://github.com/SpearSystems/LCP/.github/workflows/release.yml@refs/heads/main"
```

A registry outage or an occupied version fails closed. Resolve the issue or
choose a new patch version before creating the real tag.

## Verify downloaded release evidence offline

Download the release assets (or the `lcp-release-dry-run-*` artifact) and run
the offline verifier before approving a real tag or consuming a release. The
verifier does not need GitHub or a network: it checks the manifest structure,
that every referenced source archive, SBOM, provenance statement, and Sigstore
bundle is present, that the SHA-256 digests in the manifest and provenance
statements match the downloaded files, and that the SBOMs and provenance
statements are valid CycloneDX and SLSA v1 JSON.

```bash
python3 tools/verify_release_evidence.py ./release-assets
```

Pass `--identity` and `--issuer` to also verify every Sigstore bundle with
Cosign. The identity depends on how the evidence was produced:

```bash
# Dry-run evidence generated from refs/heads/main:
python3 tools/verify_release_evidence.py ./release-assets \
  --identity "https://github.com/SpearSystems/LCP/.github/workflows/release.yml@refs/heads/main" \
  --issuer "https://token.actions.githubusercontent.com"

# Tagged release evidence generated from refs/tags/v1.0.1:
python3 tools/verify_release_evidence.py ./release-assets \
  --identity "https://github.com/SpearSystems/LCP/.github/workflows/release.yml@refs/tags/v1.0.1" \
  --issuer "https://token.actions.githubusercontent.com"
```

The tool fails closed: a missing file, digest mismatch, malformed JSON, or a
Cosign rejection aborts the release review. It is the same structural check the
release workflow runs on its own evidence before uploading.

## Published SDK coordinates

| Registry | Package or module | Publication mechanism |
|---|---|---|
| PyPI | `lcp-sdk`, `lcp-mcp-server`, `lcp-reference-platform` | PyPI Trusted Publishing |
| npm | `@spear-systems/lcp-sdk` | npm trusted publisher and provenance |
| NuGet | `LcpSdk` (owner `SpearSystems`) | NuGet Trusted Publishing via `NuGet/login` |
| Maven Central | `systems.spear:lcp-sdk`, `systems.spear:lcp-sdk-kotlin` | Central Portal protected credentials and DNS-verified `spear.systems` namespace |
| crates.io | `lcp-sdk` | crates.io OIDC trusted login; the manually published bootstrap is not the v1 release |
| RubyGems | `lcp-sdk` | Active RubyGems OIDC trusted publisher, activated by v1.0.0 |
| Packagist | `spearsystems/lcp-sdk` | Registered from `SpearSystems/lcp-sdk-php`, which mirrors each LCP tag (Packagist needs `composer.json` at the repository root). Packagist's push webhook keeps it current; a newly added mirror may need one manual **Update** before its first tag is indexed. |
| Go module proxy | `github.com/SpearSystems/LCP/implementations/sdk/go` | Signed tag indexing |
| Swift Package Manager | `https://github.com/SpearSystems/LCP.git` | Signed repository tag |

For Kotlin, the Gradle compatibility upload publishes sources, Javadocs,
detached PGP signatures, SCM metadata, and developer metadata before it is
explicitly submitted to Central Portal with `publishing_type=automatic` by the
same tagged workflow job. This is required because Gradle's built-in
`maven-publish` task does not close or submit the compatibility repository on
its own. If Central validation closes a failed compatibility repository, drop
that repository before retrying the publication. The Rust bootstrap version is
not part of the v1 release; the `1.0.0` publication came through the configured crates.io OIDC publisher.
RubyGems now uses its active OIDC publisher for subsequent versions.

The `v1.1.1` release is the current published LCP release; its signed tag and
release manifest identify the exact source commit and package evidence. The
release manifest is the authoritative list for a specific tag. Package
registries can take time to index a new version; verify the package's version
and checksum through the registry before using it in a production lockfile.

For every package, the GitHub release also contains a deterministic tagged
source archive, a CycloneDX SBOM, a SLSA provenance statement, and a Sigstore
bundle for each of those files. These are release-evidence assets for the exact
source commit; the registry workflows separately build and publish the native
wheel, npm tarball, NuGet package, Maven artifact, crate, gem, or source-indexed
module. The manifest binds the published package coordinate and source path to
the same commit, while the evidence signatures make review independent of the
GitHub release page.

## Verify the signed release record

Download `release-manifest.json`, `release-notes.md`, and their corresponding
`.sigstore.json` assets from the GitHub release. Install Cosign from its
official, verified distribution and set the expected release workflow identity:

```bash
export VERSION='1.0.2'
export REPOSITORY='SpearSystems/LCP'
export TAG="v${VERSION}"
export WORKFLOW_IDENTITY="https://github.com/${REPOSITORY}/.github/workflows/release.yml@refs/tags/${TAG}"
export OIDC_ISSUER='https://token.actions.githubusercontent.com'

cosign verify-blob \
  --bundle release-manifest.json.sigstore.json \
  --certificate-identity "${WORKFLOW_IDENTITY}" \
  --certificate-oidc-issuer "${OIDC_ISSUER}" \
  release-manifest.json

cosign verify-blob \
  --bundle release-notes.md.sigstore.json \
  --certificate-identity "${WORKFLOW_IDENTITY}" \
  --certificate-oidc-issuer "${OIDC_ISSUER}" \
  release-notes.md

cosign verify-blob \
  --bundle lcp-source-sbom.cdx.json.sigstore.json \
  --certificate-identity "${WORKFLOW_IDENTITY}" \
  --certificate-oidc-issuer "${OIDC_ISSUER}" \
  lcp-source-sbom.cdx.json
```

The identity must match the exact repository, workflow path, and tag. Do not
accept a valid Sigstore signature from an unrelated workflow or repository.
Inspect the manifest's commit, schema-manifest digest, and container object
before consuming the release.

## Verify the reference container

The container workflow publishes a digest-addressed GHCR image, attaches
GitHub provenance and CycloneDX SBOM attestations, and uploads
`lcp-container-release.json` binding the image, mutable tag, immutable digest,
commit, and workflow run. The release workflow downloads that verified
metadata and records it as the manifest's `container` object; the offline
verifier rejects a manifest whose container reference, digest, commit, or
metadata file does not match. Deploy only the digest recorded there.

> **Pre-flight (learned 2026-11-03):** a new container package is created
> **private** even when the linked repository is public — inheritance covers
> access permissions, not visibility — and an organization that restricts
> public packages blocks the visibility change entirely. Before announcing a
> release, confirm `docker pull` works without credentials:
>
> ```bash
> curl -s "https://ghcr.io/token?scope=repository:spearsystems/lcp-reference-platform:pull" \
>   | grep -q '"token"' && echo "anonymous pull OK"
> gh api /orgs/SpearSystems/packages/container/lcp-reference-platform --jq .visibility
> ```
>
> Making a package public is irreversible on GitHub; treat the first release
> that publishes a new package as the decision point.

Resolve the tag to a digest, then verify all controls before admission:

```bash
export IMAGE='ghcr.io/spearsystems/lcp-reference-platform@sha256:<digest>'

cosign verify "${IMAGE}" \
  --certificate-identity-regexp \
  'https://github.com/SpearSystems/LCP/.github/workflows/container-release.yml@refs/tags/v.*' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com'

gh attestation verify "oci://${IMAGE}" --repo SpearSystems/LCP

gh attestation verify "oci://${IMAGE}" \
  --predicate-type 'https://cyclonedx.org/bom' \
  --repo SpearSystems/LCP
```

Deploy the digest, not the tag. The signed manifest's `container.digest` and
`container.reference` are the authoritative immutable coordinates; do not infer
the image from its mutable tag. For Kubernetes enforcement, use the
[Kyverno example](../implementations/reference-platform/kubernetes/verify-images-kyverno.example.yaml)
and follow the [container supply-chain guide](CONTAINER-SUPPLY-CHAIN.md).

## Release status and rollback

A release record is not a hosted service guarantee. Operators remain
responsible for package lockfiles, dependency review, data-residency controls,
backup/recovery, and deployment approval. If a release must be withdrawn:

1. Stop new deployments of the affected tag or package version.
2. Revoke or quarantine the affected container digest in the registry.
3. Record the incident and affected PII/data flows.
4. Publish a corrective patch release; do not mutate an existing package
   version or reuse a container digest.
5. Preserve the signed manifest, SBOM, scan reports, and decision record.

---

**Previous:** [Field dictionary](FIELD-DICTIONARY.md) · **Next:** [Release ticket](RELEASE-TICKET.md)

**Maintainer:** [Branch protection and release environment](MAINTAINER-RELEASE-SETUP.md)
