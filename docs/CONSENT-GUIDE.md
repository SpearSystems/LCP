# LCP Consent and Evidence Guide

> **Privacy page · Page 5 of 6**
>
> **Advisory only.** This guide explains how to represent consent evidence in
> LCP. It is not legal advice, does not certify compliance in any jurisdiction,
> and does not replace a party's legal review, contracts, or operational privacy
> program. The normative model is defined by `schemas/core.json` and SPEC.md.

## 1. Consent model

LCP records what a publisher captured and what a buyer may rely on; it does not
make a legal determination that consent is valid. The universal core stays
jurisdiction-neutral by using generic evidence entries rather than vendor- or
law-specific required fields.

| Field | Purpose |
|---|---|
| `compliance.consent_timestamp` | When the consent interaction occurred. |
| `compliance.consent_source_url` | Page or source context where consent was captured. |
| `compliance.consent_text_version` | Version or identifier of the displayed language. |
| `compliance.consent_purposes[]` | Purposes the person agreed to, such as calls, SMS, email, or sharing with partners. |
| `compliance.consent_evidence[]` | References that allow an authorized party to verify the capture. |
| `compliance.consent_expires_at` | Optional end of the consent validity window. |
| `compliance.otp` | Optional proof that a contact value was verified; never include the OTP itself. |
| `compliance.scrubs[]` | DNC, litigator, blacklist, or other screening results. |

A consent record should be sufficiently specific for the receiving party to
understand the purpose, channel, time, source, and evidence reference without
putting raw recordings, form captures, or secret tokens into an LCP ping.

## 2. Evidence entries

Every entry has the same shape:

```json
{
  "type": "gdpr_opt_in",
  "provider": "publisher-consent-service",
  "token_or_url": "opaque-evidence-reference"
}
```

- `type` is an open value describing the framework or evidence kind.
- `provider` identifies the publisher, vendor, or internal system that can
  verify the evidence.
- `token_or_url` is an opaque reference or controlled URL. It is not a place to
  put a bearer secret, raw form submission, recording, or consumer PII.

The provider and receiver should agree how references are authenticated,
retained, redacted, and made available for disputes or lawful requests. A URL
alone is not proof if the receiver cannot verify its integrity and access
controls.

## 3. Recommended purpose vocabulary

These values are practical interoperability hints, not a closed legal enum:

| Purpose | Meaning |
|---|---|
| `calls` | Permission or contractual basis for voice calls. |
| `sms` | Permission or contractual basis for SMS messages. |
| `email` | Permission or contractual basis for email. |
| `share_with_partners` | Permission or contractual basis for sharing with named or defined partner classes. |
| `service_fulfillment` | Contact required to provide the requested service. |
| `marketing` | Marketing contact where the applicable rules permit it. |
| `agent_assistance` | Processing by an automated or AI agent on the person's behalf. |

Publishers should avoid claiming consent for a purpose that was not actually
presented or accepted. Buyers should treat a missing or ambiguous purpose as a
contract/compliance decision, not silently infer a broader purpose.

## 4. Jurisdictional examples

The open `type` field can carry local terminology without adding local law or
vendor fields to the universal core. The examples below are starting points
for mapping and require jurisdiction-specific review.

| Example type | Typical context | Implementation note |
|---|---|---|
| `tcpa_consent` | United States telephone outreach | Preserve the exact consent text/version and channel scope; do not treat the label as proof by itself. |
| `gdpr_opt_in` | EU/EEA consent-based processing | Record purpose granularity, withdrawal handling, and the applicable controller/processor roles. |
| `uk_gdpr_opt_in` | United Kingdom processing | Keep UK-specific policy and retention decisions with the responsible organization. |
| `ccpa_notice_opt_out` | California notice/choice workflows | Model notice, sale/share choice, and opt-out evidence according to the actual flow; do not collapse them into generic consent. |
| `lgpd_consent` | Brazil consent-based processing | Record the purpose and evidence reference used by the publisher. |
| `pdpa_consent` | A market using a PDPA-style consent model | Include the country/market in the deployment record; the label alone is not a legal conclusion. |
| `verified_consent` | Internal or bilateral consent evidence | Use when the parties have defined the evidence semantics outside the protocol. |

This table is intentionally non-exhaustive. New values belong in vertical or
organization extensions when they carry domain-specific data, not as new
universal core fields.

## 5. Ping versus post handling

A `ping` contains only non-PII signals and presence indicators:

- `compliance_flags.consent` can indicate that evidence exists.
- `compliance_flags.consent_valid` can indicate that the publisher considers
  the evidence within its validity window.
- A ping must not contain `consent_evidence[].token_or_url`, consent text,
  recordings, raw addresses, or other PII.

The authorized `post` may carry the full compliance block when the receiving
buyer is entitled to it. A buyer should verify the evidence before contacting
the consumer and should apply its own DNC, suppression, and purpose controls.

## 6. Withdrawal, expiry, and erasure

Consent is not permanent merely because a message contains a timestamp.
Implementations should:

1. Treat `consent_expires_at` as a hard boundary when the contract or applicable
   rules define an expiry.
2. Process withdrawal or erasure events through the deployment's authenticated
   lifecycle path and stop future contact promptly.
3. Propagate suppression state to downstream CRM, dialer, webhook, and analytics
   systems.
4. Retain only the evidence needed for the agreed purpose, dispute window, and
   lawful recordkeeping obligation.
5. Avoid putting raw consent captures in logs, test vectors, telemetry, or pings.

LCP's `CONSENT_WITHDRAWN` and `ERASURE_REQUEST` event patterns provide useful
interoperability shapes; deployment contracts still define authorization,
retention, and response deadlines.

## 7. Publisher and buyer checklist

### Publisher

- [ ] Capture the timestamp, source, text version, channel, and purpose.
- [ ] Store evidence behind an authenticated, access-controlled reference.
- [ ] Use an open `type` value that describes the actual flow.
- [ ] Include only presence flags in pings and protect full evidence in posts.
- [ ] Define expiry, withdrawal, erasure, and dispute retention behavior.

### Buyer or platform

- [ ] Confirm that the evidence reference is verifiable before relying on it.
- [ ] Match purposes and channels to the intended contact activity.
- [ ] Apply local suppression and DNC controls independently.
- [ ] Keep evidence access and audit records separate from general application logs.
- [ ] Obtain legal/privacy review for every market and vertical in production.

## 8. Normative references

- [Canonical specification §3 and §9](../SPEC.md)
- [Core schema](../schemas/core.json)
- [Ping schema](../schemas/ping.json)
- [Post schema](../schemas/post.json)
- [Privacy and data governance](PRIVACY-DATA-GOVERNANCE.md)
- [Security policy and trust model](../governance/SECURITY.md)

---

**Previous:** [Privacy and data governance](PRIVACY-DATA-GOVERNANCE.md) · **Next:** [Security profiles](SECURITY-PROFILES.md)
