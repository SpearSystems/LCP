# LCP Security Policy

LCP is a protocol for exchanging consumer lead data (PII). Security
disclosures are handled with priority proportional to PII exposure risk.

## Reporting a Vulnerability

**Do not open a public issue for security vulnerabilities.** Use the private
GitHub Security Advisory form:

<https://github.com/SpearSystems/LCP/security/advisories/new>

That route is monitored by the maintainers and keeps reproductions, affected
versions, and any sensitive evidence out of the public issue tracker. If the
advisory form is unavailable, use the private maintainer contact linked from
[MAINTAINERS.md](../MAINTAINERS.md) rather than opening a public issue.

1. Include the affected component, release/version, impact, and a synthetic
   reproduction or proof of concept. Never include real consumer data.
2. You will receive an acknowledgment within 48 hours.
3. The maintainers will assess severity and coordinate a fix + disclosure
   timeline with you.

## Scope

In scope:

- The LCP specification (SPEC.md) — design flaws that compromise PII
  protection (e.g. ping/post split bypass, hash reversal, auth weakness).
- JSON Schemas in `schemas/` and `verticals/` — validation gaps that allow
  PII leakage or malformed payloads.
- The reference MCP server (`implementations/mcp-server/`) — code
  vulnerabilities (auth bypass, injection, SSRF).
- The reference platform (`implementations/reference-platform/`) — code,
  storage, authentication, authorization, encryption, webhook/egress, and
  tenant-isolation vulnerabilities.
- Kubernetes/Docker deployment examples — insecure defaults that could expose
  production credentials or consumer data.
- The conformance runner (`test-vectors/conformance.py`) — logic flaws that
  produce false pass results.

Out of scope:

- Vulnerabilities in third-party implementations of LCP (report to the
  implementer, not here).
- Vulnerabilities in dependencies (report upstream).
- Social engineering or physical attacks.

## PII-specific guidance

LCP's core security property is the ping/post PII split: ping messages
carry no PII, post messages carry full PII. A vulnerability that allows
PII to leak into a ping payload is **critical severity** — the ping is
explicitly designed to be safe to share with unvetted buyers.

For the reference platform, reports involving plaintext persisted PII,
application-encryption bypass, cross-tenant access, forged webhooks, SSRF,
credential exposure, or erasure bypass should include the affected deployment
profile and release version, but must not include real consumer data.

The `phone_hash` field uses per-pair HMAC-SHA256 (SPEC.md §9). A
vulnerability that enables reversal of phone_hash without the shared
secret is **high severity**.

## Disclosure Timeline

- **Critical** (PII leakage, auth bypass): fix within 7 days, coordinated
  disclosure immediately after fix is published.
- **High** (hash reversal, schema bypass): fix within 30 days, coordinated
  disclosure after fix.
- **Medium/Low**: fix in next regular release, public disclosure with the
  release.

## Safe Harbor

Good-faith security research is welcomed. Researchers who follow this
policy and do not access or expose real PII will not face legal action.

## Trust Model

The following behaviors are **by design**, not vulnerabilities:

- **Ping/post PII split.** Ping messages carry no PII by construction
  (strict allowlist, `additionalProperties: false`). This is a structural
  guarantee, not a filter. "Bypassing" the allowlist is not possible
  without modifying the schema — a report that "ping accepts field X"
  where X is in the allowlist is not a vulnerability.

- **Per-pair HMAC phone hash.** `phone_hash` uses HMAC-SHA256 with a
  per-pair shared secret. It is intentionally NOT a cross-buyer dedup
  mechanism. Each buyer sees a different hash for the same phone number.
  The hash is a dedup convenience within a single buyer's stream, not a
  cryptographic privacy guarantee against an attacker who has the shared
  secret. This is documented in SPEC.md §9.

- **Agent attestation is the trust boundary.** AI agents submitting leads
  must present a signed JWT attestation (`provenance.agent.attestation`).
  The trust boundary is the issuer's signing key — an attestation from
  an unknown issuer should be treated as untrusted. The spec does not
  mandate a specific issuer; that is a deployment decision.

- **Open enumerations.** Unknown `status`, `channel`, and `event` values
  are stored and passed through, not rejected (SPEC.md §6). This is
  forward-compatibility by design. A report that "the server accepts an
  unknown status" is not a vulnerability.

- **No built-in rate limiting in the spec.** Rate limiting is a
  deployment concern (SPEC.md §9 mentions per-sender limits, but the
  spec does not mandate specific limits or algorithms). An implementation
  that does not rate-limit is non-conformant at L2+ but this is not a
  spec-level security vulnerability.