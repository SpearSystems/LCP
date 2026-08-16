# Adoption readiness

> **Maintainer page · post-v1.0 operating plan**

LCP is a public protocol, not a hosted service. Adoption work should improve
interoperability and contributor experience without adding telemetry, vendor
lock-in, or speculative fields to the universal core.

## Reading repository signals

GitHub clone counts are useful awareness signals, not adoption claims. A clone
may come from a CI job, mirror, package builder, automated evaluator, or a
person evaluating the repository. Review these signals together:

- unique and returning cloners over 7-, 30-, and 90-day windows;
- forks, stars, issues, discussions, pull requests, and adopter entries;
- release-asset downloads and package-registry downloads;
- conformance-runner, sandbox, and SDK installation questions;
- repeated requests from independent organizations for the same capability;
- production evidence volunteered by adopters, without requesting consumer PII.

Do not add client tracking or telemetry to the protocol, schemas, SDKs, or
reference platform solely to improve these measurements. Use public GitHub,
registry, documentation, and voluntary adopter signals instead.

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

## Readiness milestones

### Now — compatibility and trust

- Keep v1.0 schemas and message semantics stable.
- Require schema, example, vector, SDK, and changelog updates for wire changes.
- Maintain the extension registry and the versioned requirement/service-area
  profiles without moving buyer-specific rules into the core.
- Establish the LEP process before external protocol proposals accumulate.
- Add a second maintainer/reviewer and independent release approval.

### Triggered scale work

Start bounded batch submission when independent publishers demonstrate bulk
intake or single-item request overhead becomes material. Start event
subscriptions when polling or configured webhooks cannot meet integration
latency or volume requirements. Start richer geography when named exact service
areas become too large or operationally fragile.

### Evidence to record

For each proposed scale feature, record:

- the independent adopters or workflows requesting it;
- current volume, latency, retry, or configuration pain;
- the compatibility and privacy boundary;
- the smallest additive extension or implementation experiment;
- the success metric and a rollback/deprecation plan.

This keeps early interest from turning into premature protocol complexity.

## Maintainer cadence

- Review GitHub and registry signals monthly during the first release quarter.
- Triage repeated requests into the v1.1 roadmap rather than adding ad-hoc
  fields.
- Publish a short decision record for every core, vertical, extension, or
  reference-platform change.
- Reassess support tiers and release independence when a second active adopter
  or maintainer appears.

See the [v1.1 roadmap](V1.1-ROADMAP.md), [LEP process](../governance/LEP.md),
and [adopter registry](../ADOPTERS.md).

---

**Previous:** [Tagged releases and artifact verification](RELEASE.md) · **Next:** [Contributing](../governance/CONTRIBUTING.md)
