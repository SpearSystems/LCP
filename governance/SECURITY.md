# LCP Security Policy

LCP is a protocol for exchanging consumer lead data (PII). Security
disclosures are handled with priority proportional to PII exposure risk.

## Reporting a Vulnerability

**Do not open a public issue for security vulnerabilities.**

Report vulnerabilities privately:

1. Email the maintainers with a description of the vulnerability, affected
   components, and a reproduction or proof of concept.
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