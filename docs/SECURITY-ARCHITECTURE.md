# LCP Security Architecture

> **Security baseline:** OWASP ASVS Level 2 with a NIST SP 800-53 Moderate-style
> control mapping. This document describes design intent; it is not a claim of
> certification or legal compliance.

## 1. Deployment model

LCP is a protocol plus deployable server profile. The protocol remains
transport- and vendor-neutral. A platform operator hosts the data plane and
may host a separate control plane for credentials, offers, tenants, and
operations.

```text
Publisher / buyer / agent
          │ TLS 1.3, HMAC or mTLS
          ▼
┌──────────────────────────────┐
│ Edge: WAF, rate limits, DDoS │
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ LCP API data plane            │
│ auth · schema · tenant ACLs   │
│ idempotency · intake          │
└───────┬───────────┬──────────┘
        │           │
        ▼           ▼
  Postgres     durable outbox
        │           │
        └─────┬─────┘
              ▼
┌──────────────────────────────┐
│ Worker / egress plane         │
│ match · auction · webhooks    │
│ retries · events · audit      │
└──────────────┬───────────────┘
               ▼
       Buyer endpoint / CRM
```

The reference implementation supports SQLite for local/single-node use and
Postgres through the `postgres` package extra for production. Production
operators should run the API behind a TLS-capable reverse proxy or ingress,
use a production WSGI server, and run delivery workers under a supervisor.

## 2. Trust boundaries

- **Consumer → publisher:** consent, collection, and source authenticity are
  publisher responsibilities; evidence is carried in LCP compliance fields.
- **Publisher → platform:** authenticated, schema-validated lead intake. The
  platform must treat publisher-declared quality fields as claims, not facts.
- **Platform → buyer:** pings are the PII-minimized boundary. Posts are full
  PII and require a separate bilateral credential and retention contract.
- **Control plane → data plane:** offers and credentials are privileged
  configuration. They must not be writable through public participant APIs.
- **Data plane → egress:** buyer webhook URLs are untrusted destinations and
  require SSRF/egress policy enforcement.
- **Application → database:** the database is private, encrypted, backed up,
  and never directly exposed to participants.

## 3. Required controls

### Identity and access

- Unique sender identity for every publisher, buyer, platform, and agent.
- Tenant ID and explicit scopes on every credential.
- Least privilege: `lead:submit`, `lead:read`, `bid:submit`, `offer:read`,
  `platform:admin` are examples, not implicit rights.
- Resource authorization for lead status, offers, credentials, and audit data.
- Separate operator/admin credentials from participant credentials.
- Credential activation, revocation, rotation, and audit history.

### Transport and message security

- TLS 1.3 at the edge; TLS between internal services where required by the
  deployment profile.
- Canonical HMAC signing with a five-minute default replay window.
- Optional mTLS for enterprise and regulated profiles.
- Stable idempotency keys and duplicate-content conflict detection.
- Strict content type, body-size, header-size, and timeout limits.
- Full envelope and payload validation before persistence or PII processing.

### PII handling

- No PII in pings, logs, metrics labels, traces, URLs, or exception messages.
- Encryption at rest and encrypted backups.
- External secret manager or KMS-backed key material in production.
- Application-level AES-GCM encryption for persisted envelopes, with a
  controlled re-encryption procedure for key rotation.
- Configurable retention, deletion, consent withdrawal, and erasure workflows.
- Auditable PII reads and administrative actions.
- Data residency and cross-border transfer controls per deployment.

### Availability and integrity

- Postgres with backups and tested restore procedures for multi-node use.
- Durable outbox/worker semantics and delivery leases.
- At-least-once delivery with receiver idempotency.
- Rate limits, quotas, circuit breakers, dead-letter handling, and alerting.
- Deterministic auction selection with an auditable decision record.

## 4. Security profiles

The protocol's L1-L3 conformance levels describe protocol capabilities. A
deployment should additionally declare a security profile:

| Profile | Intended use | Minimum controls |
|---|---|---|
| **Baseline** | Local or controlled single-node | TLS at edge, HMAC/API keys, schema validation, idempotency, encrypted backups |
| **Enterprise** | Multi-tenant commercial platform | Postgres, external secrets, tenant scopes, WAF/DDoS controls, audit logs, worker leases, DR tests |
| **Regulated** | High-scrutiny or government-adjacent deployments | Enterprise controls plus mTLS, HSM/KMS, private networking, data residency, independent testing, formal control evidence |

A security profile is a deployment claim and must not be presented as a
certification without an independent assessment.

## 5. Current reference defaults

- SQLite locally; Postgres production profile.
- Production startup requires a 32-byte application PII encryption key.
- HMAC baseline; mTLS optional.
- Five-minute replay window.
- Five-attempt webhook retry schedule.
- HTTP webhooks with redirect following disabled.
- HTTP webhooks and private destinations disabled outside sandbox mode.
- `X-LCP-Test` and envelope `test` markers for synthetic traffic.
- No public credential or offer administration API.

Operators must review every default before accepting live PII.
