# Secure attachments

> **Integration page · Page 2 of 6**
>
> **Previous:** [Calls and telephony](CALLS-AND-TELEPHONY.md) · **Next:** [Monthly quotas](MONTHLY-QUOTAS.md)

Attachments carry document evidence — a signed contract, a consent capture, a
photo, a proof of install — without putting bytes or free text in a lead
payload. The capability is **vertical-neutral**: any lead or post may reference
uploaded files by metadata, and a vertical schema decides only which purposes
apply to it. Documents, free text, and any detail a vertical keeps out of
`attributes` travel as attachment metadata, never inline (see
[Vertical schemas](VERTICALS.md)).

## Upload model

The reference platform exposes:

```text
POST /v1/lcp/attachments
GET  /v1/lcp/attachments/{attachment_id}
```

The upload is authenticated with the same HMAC canonical input as other HTTP
requests, using the raw binary body:

```text
<timestamp>\n<idempotency-key>\n<raw-file-bytes>
```

Required upload headers include:

```text
X-LCP-Sender-Id: publisher_acme
X-LCP-Lead-Id: lead-001
X-LCP-Attachment-Id: att_contract_001
X-LCP-Attachment-Purpose: signed_contract
X-LCP-Filename: synthetic-contract.pdf
X-LCP-Content-SHA256: <lowercase-sha256>
X-LCP-Idempotency-Key: publisher-attachment-001
Content-Type: application/pdf
```

The platform checks size, content type, filename safety, hash integrity,
idempotency, sender ownership, residency policy, and test/production
separation. Uploads must pass the configured malware scanner before they become
`AVAILABLE` to downstream buyers. The reference filesystem backend encrypts
bytes with the application AES-GCM key and writes files with restrictive
permissions.

For production, select the built-in S3-compatible object-storage adapter. It
uses an opaque `lcp-object://...` reference, stores objects under an explicit
residency prefix, requires provider-side SSE-KMS with the configured KMS key,
and records the scanner result without storing file contents in metadata. The
same adapter works with AWS S3 and compatible private/object-storage services
through the injected S3 API surface. It does not grant public URLs; downloads
remain authenticated through LCP.

Required production settings include:

```bash
export LCP_ATTACHMENT_BACKEND=s3
export LCP_ATTACHMENT_OBJECT_BUCKET=lcp-attachments
export LCP_ATTACHMENT_OBJECT_PREFIX=lcp/attachments
export LCP_ATTACHMENT_OBJECT_REGION=<region>
export LCP_ATTACHMENT_OBJECT_RESIDENCY=<COUNTRY>
export LCP_ATTACHMENT_ALLOWED_RESIDENCIES=<COUNTRY>
export LCP_ATTACHMENT_OBJECT_KMS_KEY_ID='<KMS key ARN or provider key id>'
export LCP_ATTACHMENT_SCANNER=clamav
export LCP_ATTACHMENT_SCAN_REQUIRED=true
export LCP_ATTACHMENT_CLAMAV_HOST=clamav.internal
export LCP_ATTACHMENT_CLAMAV_PORT=3310
```

The process identity needs only object `PutObject`, `GetObject`, `HeadObject`,
and `DeleteObject` for the configured bucket/prefix plus permission to use the
specific KMS key. Configure bucket policies, KMS grants, scanner network
access, backups, replicas, and object lifecycle rules for the same country or
state residency decision as the lead. A missing scanner, KMS key, residency,
or object-store write fails closed; `LCP_ATTACHMENT_SCANNER=none` is only
appropriate for synthetic sandbox tests.

## Referencing an uploaded file

The lead or call payload includes only metadata:

```json
{
  "attachments": [
    {
      "attachment_id": "att_contract_001",
      "purpose": "signed_contract",
      "filename": "synthetic-contract.pdf",
      "content_type": "application/pdf",
      "size_bytes": 24576,
      "sha256": "<sha256>",
      "storage_ref": "lcp://attachments/att_contract_001",
      "created_at": "2026-08-15T10:00:00Z",
      "residency": "AU",
      "malware_scan": {
        "status": "clean",
        "engine": "clamav",
        "scanned_at": "2026-08-15T10:00:01Z"
      },
      "encryption": "application_encrypted"
    }
  ]
}
```

The platform verifies that the attachment belongs to the publisher and lead
before accepting the lead. When the lead becomes a post, the metadata is
copied to the buyer. The buyer downloads the bytes with its authenticated
sender credential only if it received the post.

`storage_ref` is opaque. It is not a public URL and must not be logged as a
bearer token.

## Allowed content and retention

The reference configuration allows common documents and images, including
PDF, PNG, JPEG, WebP, plain text, DOC, and DOCX. Operators should narrow this
list to their actual use case and set an appropriate maximum size.

For each deployment, define:

- allowed MIME types and malware scanning;
- maximum file size and number of attachments;
- retention and expiry per purpose and jurisdiction;
- object-store residency and replication boundaries;
- downstream buyer access duration;
- legal hold behavior; and
- deletion/erasure evidence.

Erasing a lead removes the reference-platform attachment bytes, marks the
attachment metadata redacted, cancels undelivered payloads, and records an
audit event. Downstream buyers must honor the corresponding erasure event for
copies they already received.

**Previous:** [Calls and telephony](CALLS-AND-TELEPHONY.md) · **Next:** [Monthly payable quotas](MONTHLY-QUOTAS.md)
