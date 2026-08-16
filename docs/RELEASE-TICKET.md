# Release ticket — v1.0.0

> **Maintainer page · Release approval gate · Page 3 of 6**

Copy this ticket into the release tracker (or an issue) and complete every
item before creating the real `v1.0.0` tag. The ticket turns the non-publishing
dry run into an approvable gate: the tag must not be pushed until the dry-run
evidence, package publication configuration, and release approvals all pass.

## 1. Prerequisites

- [ ] `main` is green: Test, Security and supply chain, SDK compatibility, and
      container publication all passed for the exact candidate SHA.
- [ ] Branch protection and the protected `release` environment are configured
      per [MAINTAINER-RELEASE-SETUP.md](MAINTAINER-RELEASE-SETUP.md).
- [ ] Registry trusted publishers / protected credentials are configured and
      tested for PyPI, npm, NuGet, Maven Central, crates.io, RubyGems,
      Packagist, Go, and SwiftPM.
- [ ] `SDK_VERSION` is `1.0.0` and every package's explicit version matches.

## 2. Dry-run release rehearsal

- [ ] Started the non-publishing rehearsal:

      ```bash
      gh workflow run release.yml \
        --repo SpearSystems/LCP \
        --ref main \
        -f tag=v1.0.0 \
        -f target_sha=<candidate-sha>
      ```

- [ ] The run completed successfully (registry availability check, schema and
      conformance gates, source/package evidence generation, signing, and
      verification).
- [ ] Downloaded the `lcp-release-dry-run-v1.0.0` workflow artifact and
      extracted it.

## 3. Offline evidence verification

- [ ] Structural verification passed:

      ```bash
      python3 tools/verify_release_evidence.py ./release-assets
      ```

- [ ] Signature verification passed with the dry-run identity:

      ```bash
      python3 tools/verify_release_evidence.py ./release-assets \
        --identity "https://github.com/SpearSystems/LCP/.github/workflows/release.yml@refs/heads/main" \
        --issuer "https://token.actions.githubusercontent.com"
      ```

- [ ] `release-manifest.json` contains all 12 package evidence records.
- [ ] The manifest's `commit` equals the candidate SHA, and the
      `schema_manifest_sha256` matches `implementations/sdk/typescript/src/generated/schema-manifest.json`.
- [ ] Every source archive's SHA-256 in the manifest matches the downloaded
      file, and each provenance statement's subject binds the same digest.
- [ ] Every package SBOM parses as CycloneDX and every provenance statement as
      SLSA v1.
- [ ] The release notes and manifest Sigstore bundles verify against the
      expected workflow identity.

## 4. Package publication readiness

- [ ] Each published coordinate is absent from its registry for `1.0.0` (the
      dry-run gate already fails otherwise):
      PyPI `lcp-sdk` / `lcp-mcp-server` / `lcp-reference-platform`,
      npm `@spear-systems/lcp-sdk`, NuGet `SpearSystems:LcpSdk`,
      Maven `systems.spear:lcp-sdk` / `:lcp-sdk-kotlin`,
      crates.io `lcp-sdk`, RubyGems `lcp-sdk`, Packagist `spearsystems/lcp-sdk`,
      Go `github.com/SpearSystems/LCP/implementations/sdk/go`,
      SwiftPM `https://github.com/SpearSystems/LCP.git`.
- [ ] The Kotlin Maven coordinate is distinct (`systems.spear:lcp-sdk-kotlin`)
      and the release manifest references it.
- [ ] Container publication will target `ghcr.io/spearsystems/lcp-reference-platform:v1.0.0`
      with signature, provenance, and SBOM attestations.

## 5. Release approval

- [ ] Two independent reviewers approved the release ticket (release and
      security roles; the person who initiated the release does not approve
      their own deployment).
- [ ] Reviewers confirmed the dry-run evidence digest, scanner reports,
      SBOMs, and the candidate SHA.
- [ ] Any HIGH/CRITICAL findings are resolved or accepted in the
      [vulnerability exception register](VULNERABILITY-EXCEPTIONS.md) with an
      owner and expiry.
- [ ] No secrets, real PII, credentials, or private configuration are present
      in the source, SBOMs, image, or release assets.

## 6. Create the tag

- [ ] Push the signed tag only after all items above are checked:

      ```bash
      git tag -s v1.0.0 -m "LCP v1.0.0"
      git push origin v1.0.0
      ```

- [ ] Confirm the tag runs Test, Security, SDK compatibility, package, and
      container workflows, and approve the protected publication jobs.
- [ ] Verify the signed release record with
      [RELEASE.md](RELEASE.md#verify-the-signed-release-record) and record the
      image digest and registry package URLs in this ticket.

## 7. Post-release verification

- [ ] Ran the scheduled post-release probe
      ([release-verify.yml](../.github/workflows/release-verify.yml)) against
      the published `v1.0.0` and it passed.
- [ ] Recorded the signed manifest, SBOMs, provenance, scanner reports,
      reviewer identities, approvals, and deployment digest in the release
      record.

---

**Previous:** [Release guide](RELEASE.md) · **Next:** [Implementation decisions](IMPLEMENTATION-DECISIONS.md)
