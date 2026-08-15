# LCP Threat Model
> **Security page · Page 5 of 6**

This is a living threat model for the LCP protocol and reference platform.
It should be reviewed before each major protocol or deployment change.

## Assets

- Consumer names, phone numbers, email addresses, dates of birth, and
  vertical-specific attributes.
- Consent evidence and compliance records.
- Publisher/buyer credentials and signing secrets.
- Offer definitions, floor prices, capacity, and routing policy.
- Bid history, winner selection, pricing, disputes, and conversion events.
- Audit records and backups.
- Uploaded contracts, medical records, call recordings, and attachment metadata.
- Publisher mapping definitions and normalization decisions.
- Platform availability and message integrity.

## Threat actors

| Actor | Motivation | Examples |
|---|---|---|
| Anonymous internet actor | Fraud, data theft, disruption | Forged posts, replay, DDoS |
| Compromised publisher | Cheap volume or PII exfiltration | Spam floods, false quality claims |
| Compromised buyer | Unauthorized access or offer manipulation | Bid spoofing, webhook abuse |
| Malicious tenant | Cross-tenant access | Lead-status enumeration, offer scraping |
| Insider/operator | Data access or configuration abuse | Bulk PII export, secret theft |
| Supply-chain attacker | Code execution or credential theft | Dependency/package compromise |
| Regulator/auditor | Evidence and control validation | Retention, access, deletion, breach review |

## Abuse cases and controls

### Forged or replayed messages

**Controls:** TLS, HMAC/mTLS, sender identity, timestamp freshness, canonical
signing, idempotency, duplicate-content conflict detection, rate limits.

### PII leakage in pings or telemetry

**Controls:** strict ping allowlist, `ping_safe` validation, schema validation,
PII-redacting logs/errors, payload tests, encrypted queues/backups, review of
traces and metrics.

### Cross-tenant data access

**Controls:** tenant-scoped credentials, explicit scopes, resource-level
authorization, non-enumerating 404s, admin separation, access audit logs,
negative authorization tests.

### Webhook SSRF or egress compromise

**Controls:** HTTPS-only production URLs, host allowlists, private-address
blocking, DNS resolution checks, redirect disabling, egress firewall rules,
connect/read timeouts, response-size limits.

### Auction manipulation

**Controls:** authenticated buyer bids, expiry checks, floor-price checks,
deterministic winner rules, stable correlation IDs, immutable match/bid audit
records, capacity enforcement.

### Delivery loss or duplication

**Controls:** durable delivery records, worker leases, at-least-once semantics,
stable message IDs, idempotency, retry schedule, dead-letter alerts, restore
and replay runbooks.

### Malicious or oversized attachments

**Controls:** authenticated upload, sender/lead ownership checks, strict size
and MIME allowlists, filename/path validation, SHA-256 integrity checks,
application encryption, malware scanning before downstream release, private
object storage, authenticated downloads, retention expiry, and erasure handling. The production S3-compatible adapter additionally
requires SSE-KMS, an immutable residency prefix, least-privilege object/KMS
permissions, and a fail-closed ClamAV scan before release. Binary content is
never accepted in a ping.

### Mapping injection or normalization drift

**Controls:** versioned mapping documents, allowlisted transforms, no executable
expressions, schema validation after normalization, source-record digests,
synthetic fixtures for each form version, staged activation, and rollback.

### Credential compromise

**Controls:** external secret manager, KMS/HSM, least-privilege scopes,
rotation, previous-key migration window, revocation, no secrets in logs or
source, independent audit of admin actions.

### Availability attacks

**Controls:** edge WAF/DDoS service, per-sender quotas, body/header limits,
backpressure, circuit breakers, queue isolation, Postgres capacity planning,
health/readiness probes, multi-zone deployment, tested recovery.

## Residual risks

- A trusted participant can still misuse PII they legitimately receive in a
  post. Contracts, retention controls, audit, and downstream governance are
  required; cryptography cannot solve authorized misuse.
- Publisher-declared quality and provenance signals may be false. The platform
  should preserve provenance and let buyers apply their own risk policy.
- Data residency and contact-law obligations vary by jurisdiction. Operators
  must obtain jurisdiction-specific legal/privacy advice.
- A reference implementation is not an independent security certification.

## Review triggers

Re-review this model when adding:

- New message types or PII fields.
- New transports or authentication methods.
- Cross-border routing.
- New storage or queue backends.
- Public admin APIs.
- Hosted/multi-region operation.
- Agent access to full posts.
- Attachment storage, upload/download, malware scanning, or object-store changes.
- New publisher mappings or buyer criteria that alter routing decisions.

---

**Previous:** [Security architecture](SECURITY-ARCHITECTURE.md) · **Next:** [Privacy and data governance](PRIVACY-DATA-GOVERNANCE.md)
