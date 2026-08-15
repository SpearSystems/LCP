# Publisher mapping and normalization

> **Integration page · Page 2 of 6**
>
> **Previous:** [Publisher onboarding](PUBLISHER-ONBOARDING.md) · **Next:** [Call and telephony flow](CALLS-AND-TELEPHONY.md)

LCP is intentionally strict at the network boundary, while publisher source
systems are often inconsistent. The reference platform therefore includes a
declarative mapping layer that turns versioned source forms into canonical LCP
`lead` or `call` messages.

## Recommended nine-brand topology

Use one publisher adapter service, with separate sender identities and mapping
records for each brand and form version:

```text
brand_01 form/call ─┐
brand_02 form/call ─┤
...                 ├─ publisher normalizer ─ LCP outbox ─ SpearPointX
brand_09 form/call ─┘
```

Each brand should have its own sender credential. This gives operations a
small blast radius when a single brand or form is disabled, while the adapter
code and deployment remain shared.

Use these identifiers consistently:

| Concern | LCP location |
|---|---|
| Brand identity | `message.sender_id` plus `provenance.brand_id` |
| Form/flow identity | `provenance.form_id`, `provenance.flow_key`, and mapping `form_key` |
| Source record identity | `payload.external_id` |
| Stable LCP identity | `payload.lead_id` and `message.idempotency_key` |
| Campaign attribution | `provenance.campaign_id` and `creative_id` |

## Mapping document

A mapping is data, not executable code. The reusable template is
[`examples/templates/publisher-mapping.json`](../examples/templates/publisher-mapping.json).
It supports:

- source paths such as `contact.phone` and `answers.primary_service`;
- a controlled transform set: trim, case conversion, string, integer, boolean,
  E.164 phone normalization, and UTC date-time normalization;
- explicit value maps for legacy answer labels;
- constants for stable protocol fields;
- OTP verification paths; and
- a versioned mapping key per publisher/form.

A mapping must identify its publisher, form key, version, channel, and vertical.
Unknown transforms are rejected. Source-specific questions must not be copied
blindly into an LCP extension.

The local/reference administration command is:

```bash
lcp-platform-admin mapping upsert --file publisher-mapping.json
```

The mapping registry can contain multiple versions at once. Deploy a new
version, send a synthetic test sample through it, compare the normalized output,
and only then mark the old mapping inactive.

## OTP variation

OTP is a source quality signal, not a universal intake requirement.

For an OTP-enabled form, map:

```json
{
  "compliance": {
    "otp_verified": true,
    "otp": {
      "channel": "sms",
      "verified_at": "2026-08-15T10:00:00Z"
    }
  },
  "lead_quality": {
    "verified_phone": true,
    "verification": {
      "phone_method": "otp",
      "phone_verified_at": "2026-08-15T10:00:00Z"
    }
  }
}
```

Never map the actual OTP value. For a form without OTP, omit the OTP object and
leave the phone unverified. A buyer offer can use
`require_verified_phone: true` when it needs that guarantee; other offers can
accept non-OTP leads.

## Questions and taxonomy

Map common questions into the controlled vertical schema. For home services,
use `service_type` and `service_subtype` from
[`verticals/home_services.json`](../verticals/home_services.json). Gutter,
downspout, roofing, and other brand labels should be normalized through
`value_maps`, not left as competing spellings.

Questions that are:

- common across buyers belong in a versioned vertical schema;
- buyer-specific but safe for pricing can use `attribute_equals` or
  `attribute_in` on an offer; and
- sensitive, free-text, or source-only belong in the post or the publisher's
  retained source record, never in a ping.

The matcher uses only declarative equality/allowlist criteria. It never
executes expressions supplied by a buyer.

## Audit behavior

When a mapping is applied, the reference platform records:

- mapping ID, publisher, form key, and version;
- normalized lead ID and a SHA-256 hash of the source record ID; and
- a SHA-256 digest of the source record.

It does **not** store the source form body in the mapping audit table. The
publisher's source archive remains the system of record for the original form,
subject to its own retention and residency policy.

## Operational checklist

- [ ] One credential exists per brand and environment.
- [ ] Every production form has a versioned mapping and a rollback mapping.
- [ ] Mapping fixtures cover valid, incomplete, OTP, non-OTP, duplicate, and
      unexpected-answer cases.
- [ ] Source IDs and LCP idempotency keys survive retries unchanged.
- [ ] Mapping failures enter a durable dead-letter queue rather than becoming
      partially normalized leads.
- [ ] Mapping audit records contain no consumer PII.
- [ ] New vertical fields are reviewed for ping safety before release.
- [ ] Each mapping is exercised in the sandbox before production activation.

**Previous:** [Publisher onboarding](PUBLISHER-ONBOARDING.md) · **Next:** [Calls and telephony](CALLS-AND-TELEPHONY.md)
