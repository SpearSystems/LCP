# Adoption readiness

> **Maintainer page · post-v1.0 operating plan**

LCP is a public protocol, not a hosted service. Adoption work should improve
interoperability and contributor experience without adding telemetry, vendor
lock-in, or speculative fields to the universal core.

## Reading repository signals

GitHub clone counts are useful awareness signals, not adoption claims. A clone
may come from a CI job, mirror, package builder, automated evaluator, or a
person evaluating the repository. Review these signals together:

- clones, unique cloners, and views over the 14-day window GitHub exposes,
  snapshotted monthly so a longer series accumulates locally;
- forks, stars, issues, discussions, pull requests, and adopter entries;
- release-asset downloads and package-registry downloads;
- conformance-runner, sandbox, and SDK installation questions;
- repeated requests from independent organizations for the same capability;
- production evidence volunteered by adopters, without requesting consumer PII.

Do not add client tracking or telemetry to the protocol, schemas, SDKs, or
reference platform solely to improve these measurements. Use public GitHub,
registry, documentation, and voluntary adopter signals instead.

## Monthly signal review

This is a cadence commitment, not a measurement. Starting **2026-11** the
maintainer reviews the sources below on the **first working day of each
month**, covering the previous calendar month, and records the outcome in this
section. No review has been recorded yet: the log is deliberately empty rather
than backfilled with numbers nobody took.

### Sources and metrics

| Source | Metrics | Where |
|---|---|---|
| GitHub repository | issues opened and closed, pull requests opened and merged, clones and unique cloners over the 14-day window the Insights pages expose, views, forks, stars, release-asset downloads, discussions where the repository has them enabled | the repository's Insights pages and the issue/PR lists; snapshot the traffic numbers monthly, because GitHub keeps only a short window |
| Package registries (published coordinates) | download counts where the registry publishes them — `lcp-sdk` on PyPI, crates.io, and RubyGems, `@spear-systems/lcp-sdk` on npm, `LcpSdk` on NuGet, `spearsystems/lcp-sdk` on Packagist — plus `ghcr.io/spearsystems/lcp-reference-platform` pull counts; Maven Central publishes no per-artifact download count, so `systems.spear:lcp-sdk` is a resolved-version presence check only | each registry's own statistics page |
| Documentation | aggregate page views for the onboarding and integration pages, **if** the documentation host exposes them | the host's own reporting. The repository ships no client-side analytics, so this source may legitimately be empty every month |
| Adopter feedback | issue-template submissions (bug report, feature request, LEP proposal), pull requests to [`ADOPTERS.md`](../ADOPTERS.md), LEP discussions, and questions that recur across issues | the public tracker only. Security reports stay in the private advisory channel and are not roadmap input |

The registry coordinates are the ones the release records publish; a
coordinate that stops resolving or publishing is a packaging defect to fix,
not an adoption signal. Every metric above is a proxy: a clone may be a CI
job, a download may be a mirror, and silence may mean nobody is looking.

### What triggers a roadmap response

| Observed signal | Response |
|---|---|
| Two or more independent organizations ask for the same capability | draft or revive a LEP; do not add an ad-hoc field |
| One workflow reports material volume, latency, retry, or configuration pain | record the evidence against the LEP and size the smallest additive experiment |
| The same onboarding question appears in three or more issues or pull requests | fix the guide or example that failed the reader first; change the protocol only if the guide cannot answer the question |
| An independent operator publishes a conformance report that re-hashes to the runner's digest | the D11 hosted-conformance claim becomes revisitable; nothing changes until then |
| A second person takes release duties | revisit the prepare-not-claim state recorded under D12 in [`MAINTAINER-RELEASE-SETUP.md`](MAINTAINER-RELEASE-SETUP.md) |
| No source shows a change | record "no action" and stop. A quiet month is a valid outcome, not a prompt to add features |

### Where the review is recorded

Each review appends one dated row to this log, in this file:

| Review month | Reviewed on | Signals with movement | Action |
|---|---|---|---|
| — | — | — | first review covers 2026-11 and is recorded at the start of 2026-12 |

A row that produces a decision adds a dated decision record (the
[DECISIONS-v1.2.md](DECISIONS-v1.2.md) record covers v1.2; later decisions get
their own file) and, for a protocol change, updates the
[LEP registry](../governance/LEP-REGISTRY.md). The log states what was
observed; the decision record states what was decided.

## Feedback funnel

1. **Discover:** README role cards, quickstarts, schemas, OpenAPI, and the
   signed release record.
2. **Try:** examples, sandbox, SDKs, and the self-hosted conformance vectors.
3. **Integrate:** publisher mapping, buyer offers, requirement profiles, and
   reference-platform deployment guidance.
4. **Report:** issue templates for bugs, feature proposals, LEPs, and security
   reports.
5. **Verify:** adopters add a public implementation or use-case entry to
   [`ADOPTERS.md`](../ADOPTERS.md) when they are comfortable doing so.

The verification step has a self-hostable, standard-library tool:
[`tools/conformance_runner.py`](../tools/conformance_runner.py) serves
`POST /run` and returns a `{report_id, generated_at, target, lcp_versions,
checks, summary, digest}` report whose digest is the SHA-256 of the canonical
body, so anyone can re-hash a published report and check it was not edited.
Per [DECISIONS-v1.2.md](DECISIONS-v1.2.md) D11 it is run locally by whoever
wants the evidence — there is no hosted service to depend on and no
certification or trademark claim attached to a report, until a second
independent operator runs the same runner and publishes a matching one.
See [OPERATIONS.md](OPERATIONS.md) for the run command and security model.

## Readiness milestones

### Now — compatibility and trust

- Keep v1.0 schemas and message semantics stable.
- Require schema, example, vector, SDK, and changelog updates for wire changes.
- Maintain the extension registry and the versioned requirement/service-area
  profiles without moving buyer-specific rules into the core.
- Establish the LEP process before external protocol proposals accumulate.
- Add a second maintainer/reviewer and independent release approval. D12 is
  **prepared, not claimed**: the ruleset change is recorded in
  [`MAINTAINER-RELEASE-SETUP.md`](MAINTAINER-RELEASE-SETUP.md), and independent
  release approval does not exist while one person holds release duties.

### Triggered scale work

The triggers below were **waived, not met**, for the v1.2 items enumerated in
[DECISIONS-v1.2.md](DECISIONS-v1.2.md) D1 (2026-11-04), with a re-evaluation
date of 2027-05-04 recorded per item in the
[LEP registry](../governance/LEP-REGISTRY.md). Both accepted tracks therefore
exist as off-by-default surfaces: batch submission behind `LCP_FEATURE_BATCH`
and event subscriptions behind `LCP_FEATURE_EVENTS`, each advertised only when
the deployment enables it. The triggers still govern any further scale work:

- batch intake beyond the accepted 100-item/1 MiB bound, when independent
  publishers demonstrate larger bulk intake or per-request overhead stays
  material;
- push delivery beyond the accepted subscription track, when polling or
  configured webhooks cannot meet integration latency or volume requirements;
- richer geography, when named exact service areas become too large or
  operationally fragile.

### Evidence to record

For each proposed scale feature, record:

- the independent adopters or workflows requesting it;
- current volume, latency, retry, or configuration pain;
- the compatibility and privacy boundary;
- the smallest additive extension or implementation experiment;
- the success metric and a rollback/deprecation plan.

This keeps early interest from turning into premature protocol complexity.

## Maintainer cadence

- Run the [monthly signal review](#monthly-signal-review) on the first working
  day of each month, starting 2026-11.
- Triage repeated requests into the roadmap or a LEP rather than adding ad-hoc
  fields.
- Publish a short decision record for every core, vertical, extension, or
  reference-platform change.
- Reassess support tiers and release independence when a second active adopter
  or maintainer appears.

## Licensing posture

**Decision (2026-11-04): one license, no dual licensing.** The repository stays
under **Apache-2.0** for everything it contains — specification text, schemas,
test vectors, reference platform, SDKs, and documentation — with the root
[`LICENSE`](../LICENSE) as the single grant. There are no per-artifact or
per-audience terms, and no separate documentation license.

**What would reopen it.** A standards body or community that LCP joins
requiring the normative specification and documentation to carry a separate
license (for example a Creative Commons documentation license) while
implementations stay under Apache-2.0. Absent such a requirement, the
roadmap line "consider dual licensing for normative specification text"
([v1.1 roadmap](V1.1-ROADMAP.md)) is closed as considered and declined.

**Where a change would be recorded.** A new dated decision record beside the
[v1.2 execution plan](V1.2-EXECUTION-PLAN.md) — the v1.2 record is not
extended after signing — plus the [`LICENSE`](../LICENSE) change, a
[`CHANGELOG.md`](../CHANGELOG.md) entry, and the release record of the version
that carries it. Licensing is separate from conformance: the
[trademark policy](../governance/TRADEMARK.md) governs claims, not copyright
permissions, and needs no change for a documentation license.

See the [v1.1 roadmap](V1.1-ROADMAP.md), [LEP process](../governance/LEP.md),
and [adopter registry](../ADOPTERS.md).

---

**Previous:** [Tagged releases and artifact verification](RELEASE.md) · **Next:** [Contributing](../governance/CONTRIBUTING.md)
