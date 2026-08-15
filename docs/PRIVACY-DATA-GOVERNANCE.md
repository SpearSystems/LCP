# LCP Privacy and Data Governance

LCP carries consumer PII. Each platform, publisher, buyer, and downstream
processor remains responsible for the laws and contracts applicable to its
role, country, state, and sector. This document is operational guidance, not
legal advice.

## Data residency

Residency is deployment-specific. An operator should:

1. Classify the countries/states represented by each tenant and lead.
2. Route storage, backups, queues, logs, and support access according to the
   applicable residency policy.
3. Declare supported countries in capabilities only when the deployment can
   lawfully process them.
4. Record cross-border transfers and subprocessors.
5. Prevent a webhook or backup from silently moving data to an unapproved
   region.

Multi-region deployments should use explicit routing policies rather than
assuming that a global database is acceptable.

## Data lifecycle

For every field and message type, define:

- Collection purpose.
- Lawful basis/consent basis where applicable.
- Recipients and processors.
- Retention period.
- Encryption and access class.
- Deletion/erasure behavior.
- Audit requirements.

Full PII posts and attachments should have a shorter and more tightly
controlled retention policy than non-PII pings, hashes, or aggregate
operational metrics. Attachment storage, replicas, malware-scanning queues,
and download logs must follow the same country/state residency decision as the
lead they support.

## Required operational controls

- Encrypt database, queue, object storage, attachments, and backups. The
  production S3-compatible attachment adapter requires SSE-KMS and an explicit
  residency-bound object prefix.
- Scan uploaded files before releasing them to a buyer; keep the scan result
  and file hash as audit metadata without logging file contents.
- Treat `storage_ref` as an opaque capability identifier, never a public URL.
- Do not place raw PII in logs, URLs, metrics, traces, exception messages, or
  support tickets.
- Restrict production PII access to named roles and record every access.
- Redact administrative responses by default.
- Use the controlled reference operation `lcp-platform-admin privacy erase-lead`
  to redact persisted envelopes and cancel pending deliveries, then propagate
  erasure to downstream buyers and backups.
- Test backup deletion and restore behavior against retention requirements.
- Ensure failed webhook payloads, object-storage replicas, malware-scanning
  queues, and dead-letter queues follow the same PII retention and residency
  policy as successful deliveries.
- Use synthetic data in development and the sandbox.
- Keep production credentials and encryption keys outside the repository.

## Consumer rights and compliance events

Operators should map applicable legal requests to the LCP lifecycle model:

- `CONSENT_WITHDRAWN` updates suppression/contact preferences.
- `ERASURE_REQUEST` triggers deletion workflows and buyer acknowledgements.
- Dispute evidence should be access-controlled and retained only as long as
  necessary.

The platform must not treat a successful HTTP delivery as proof that a buyer
lawfully contacted a consumer. Delivery, acceptance, contact, conversion, and
consent are separate facts. The reference erasure operation preserves opaque
routing/audit identifiers while redacting persisted payloads; it does not erase
copies already delivered to a buyer or copies retained in backups, which must
be handled through the operator's downstream and backup processes.
