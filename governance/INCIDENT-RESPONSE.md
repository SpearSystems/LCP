# Incident Response Policy

LCP deployments exchange consumer PII. Operators must maintain an incident
response process appropriate to their jurisdiction, contracts, tenant mix, and
security profile.

## Incident categories

- Credential compromise or unauthorized access.
- PII leakage or incorrect recipient delivery.
- Replay, spoofing, or signature bypass.
- Cross-tenant authorization failure.
- Malicious webhook/SSRF or egress policy bypass.
- Data loss, corruption, or unavailable delivery queue.
- Supply-chain or deployment compromise.

## Minimum response steps

1. Detect and assign an incident owner.
2. Contain affected credentials, tenants, offers, endpoints, or egress.
3. Preserve logs and audit evidence without copying raw PII unnecessarily.
4. Scope affected message IDs, lead IDs, tenants, recipients, and time range.
5. Rotate credentials/keys and block compromised routes.
6. Eradicate the cause and verify with security regression tests.
7. Notify affected parties and regulators when required by applicable law or
   contract.
8. Document the timeline, impact, root cause, remediation, and follow-up.

## Security reporting

Protocol or reference implementation vulnerabilities should be reported
privately according to [SECURITY.md](SECURITY.md), not through a public issue.
Operators of deployed LCP services must publish their own customer incident
contact and notification commitments.

This document is operational guidance and does not replace legal counsel,
regulatory notification obligations, or a tenant-specific incident-response
plan.
