# LCP — Design Notes & Research

## Current documentation

- [PLATFORM-INTEGRATION.md](PLATFORM-INTEGRATION.md) — how common
  platforms (Facebook Lead Ads, Google Lead Forms, Twilio, HubSpot,
  Salesforce, TikTok) map to/from LCP fields.
- [IMPLEMENTATION-DECISIONS.md](IMPLEMENTATION-DECISIONS.md) — approved
  production reference profile: offer matching, auction selection, HMAC,
  webhook retries, SDK boundaries, and sandbox parity.
- [Reference platform](../implementations/reference-platform/README.md) —
  install, configure, operate, and deploy the persistent LCP router.
- [Sandbox](../examples/sandbox/README.md) — end-to-end synthetic
  publisher/buyer test using the same platform code path.

## Historical artifacts (kept for provenance)

These documents were part of the spec development process. Their
findings have been resolved — they do not reflect the current state of
the spec.

- [lcp-deep-research-prompt.md](lcp-deep-research-prompt.md) — the
  cross-LLM review prompt used to stress-test the v1.0 design.
- [lcp-deep-research-review.md](lcp-deep-research-review.md) — Sonnet 5
  adversarial review. All 5 blockers and 15 should-fixes resolved in
  SPEC.md (see §14 review-log appendix).
- [publishing-gap-analysis.md](publishing-gap-analysis.md) — pre-publish
  comparison against LEX and MCP repos. All high/medium gaps addressed.
  Lower-priority items tracked in SPEC.md §14 deferred tables.