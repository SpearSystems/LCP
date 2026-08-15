# LCP Deployment Security Profiles

LCP protocol conformance and deployment security are separate declarations.
An implementation may be LCP-L3 without being suitable for regulated data.

## Baseline

For local, development, or controlled single-node deployments:

- TLS at the public edge.
- HMAC or Bearer authentication.
- Strict schema and ping-safe validation.
- Idempotency and replay-window checks.
- Request/body/header limits.
- Encrypted database and backups.
- Application-level encryption for persisted consumer envelopes.
- No PII in logs.
- Basic rate limiting.
- Synthetic sandbox test path.

## Enterprise

For a multi-tenant commercial platform:

- All Baseline controls.
- Postgres or equivalent production database.
- External secret manager/KMS.
- Tenant-scoped credentials and scopes.
- Resource-level authorization.
- WAF/DDoS protection and private database network.
- Durable outbox and leased delivery workers.
- Dead-letter queue and delivery alerting.
- Audit logs for PII reads, admin actions, offers, bids, and credentials.
- Backup/restore and disaster-recovery tests.
- Dependency scanning, SBOMs, signed releases, and penetration testing.

## Regulated

For high-scrutiny, government-adjacent, or regulated deployments:

- All Enterprise controls.
- mTLS or equivalent strong bilateral identity where required.
- HSM/KMS-backed key hierarchy and documented rotation.
- Private networking and controlled egress.
- Data residency and jurisdiction routing controls.
- KMS/HSM-controlled application encryption key and tested re-encryption.
- Field-level PII encryption/tokenization where required by the jurisdiction or risk assessment.
- Formal retention, deletion, erasure, and consent-withdrawal workflows.
- Independent penetration test and security architecture review.
- NIST SP 800-53 Moderate-style control matrix.
- OWASP ASVS Level 2 minimum, with Level 3 controls for high-risk paths.
- Privacy impact assessment and records-of-processing documentation.
- Tested incident response, business continuity, RTO/RPO, and breach
  notification procedures.

These profiles are implementation guidance, not certifications. Operators must
obtain qualified security, privacy, and legal review before making compliance
claims.
