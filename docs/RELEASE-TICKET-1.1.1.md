# Release record — v1.1.1

> **Status: published on 2026-11-04.** Tag `v1.1.1` resolves to commit
> `fa290abebad16c53d27be9b486aedb03acccbd4d`; the GitHub release and its signed
> assets are at <https://github.com/SpearSystems/LCP/releases/tag/v1.1.1>.
> `v1.1.0` stays published and immutable.

## 1. Release identity

- [x] `SDK_VERSION` and every package manifest: `1.1.1`
      (`tools/check_sdk_versions.py --check` passes on the tagged commit).
- [x] Published commit: `fa290abebad16c53d27be9b486aedb03acccbd4d`.
- [x] GitHub release with manifest, release notes, SBOMs, provenance, and
      Sigstore bundles (79 assets).
- [x] Additive patch: no wire-shape change; one advisory capability field.

## 2. Included change

**#193 — the delivered-post half of the §9 response window.** A post whose
receiver never acknowledged it used to leave the lead `POSTED` indefinitely
(a lead without a payload expiry never expired at all). Now:

- the window runs from post delivery to acknowledgement
  (`LCP_POST_RESPONSE_TIMEOUT_SECONDS`, default **30 s**, SPEC §9);
- an unacknowledged delivery becomes `FAILED`, gets a `Post response timeout`
  dead-letter row and a `lead.post_response_timeout` audit event, and the lead
  transitions to `EXPIRED`;
- an acknowledged (2xx) post is never expired by the sweep — that
  acknowledgement is the payable trigger, so payables cannot race the timer;
- `GET /v1/lcp/capabilities` advertises `post_response_timeout_seconds`
  (matrix row `CAP-08`), and `SEC-12` now covers both the bid and post legs;
- re-offer stays MAY per §9 and is deliberately not performed (the expired copy
  consumed its slot under LEP-0003). Documented in `docs/OPERATIONS.md`.

## 3. Verified publication

Read back from each registry API after the tag workflows completed:

| Registry | Coordinate | Version |
|---|---|---|
| PyPI | `lcp-sdk`, `lcp-mcp-server`, `lcp-reference-platform` | 1.1.1 |
| npm | `@spear-systems/lcp-sdk` | 1.1.1 |
| NuGet | `LcpSdk` | 1.1.1 |
| crates.io | `lcp-sdk` | 1.1.1 |
| RubyGems | `lcp-sdk` | 1.1.1 |
| Maven Central | `systems.spear:lcp-sdk`, `systems.spear:lcp-sdk-kotlin` | 1.1.1 |
| Swift Package Manager | repository tag | v1.1.1 |
| Go module proxy | `…/implementations/sdk/go` | submodule tag `implementations/sdk/go/v1.1.1` (root tags only produce pseudo-versions for a nested module — see §5) |
| Packagist | `spearsystems/lcp-sdk` | mirror repository `SpearSystems/lcp-sdk-php` tagged `v1.1.1`; Packagist submission still pending |

## 4. Reference container

```text
ghcr.io/spearsystems/lcp-reference-platform@sha256:95e54691c8c2acb9f6201b986ebaaaf23c7ffba57617b1b96cd720a032dfe4a1
```

Anonymous pull token granted; `manifests/v1.1.1` and the digest both return
`200` without credentials. `container-release.yml` now asserts exactly this
before it writes release metadata, so a private image fails the release.

## 5. Follow-ups

- **Packagist** — the mirror repo `SpearSystems/lcp-sdk-php` exists, mirrors
  each tag, and is installable as a Composer VCS repository today. Registering
  it (`https://packagist.org/packages/submit` with that URL) is a one-time,
  account-authenticated action; no CI credential is involved.
- **Go module tags** — a module in a subdirectory needs a `subdir/vX.Y.Z` tag;
  root `vX.Y.Z` tags only yield `v0.0.0-…` pseudo-versions. `docs/RELEASE.md`
  now lists this as a release step.
