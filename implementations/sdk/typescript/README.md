# LCP TypeScript/JavaScript SDK

Tier 1 reference SDK for Node.js 20+, browser-compatible Web Crypto runtimes,
web publishers, serverless functions, and ad-tech services.

```bash
npm install @spear-systems/lcp-sdk
```

From this repository:

```bash
npm install
npm test
```

The package provides typed envelope builders, full Draft 2020-12 JSON Schema validation via `LcpSchemaValidator`,
Web Crypto HMAC signing and verification, raw-body webhook verification,and an async `LcpClient` using `fetch` for leads, calls, bids, status, schemas,

capabilities, and offers. Mutating retries reuse the envelope idempotency key.
See the shared [SDK contract](../../../docs/SDK-ROADMAP.md).
## Shared lead slots and contact hashes (v1.1, LEP-0003)

The typed payload declarations gained the optional v1.1 fields
`slots_available` (ping), `slots_claimed` (bid), and `contacted_hashes`
(post). The SDK ships the only sanctioned contact-hash builder —
`HMAC-SHA256(pair_secret, normalized_preimage)` (lowercase hex), reusing the
package's existing HMAC-SHA256 signing primitive: phone preimages are the
verbatim wire bytes per SPEC.md §9; email preimages use the §9 pinned
candidate canonical (trim surrounding whitespace, ASCII-lowercase the domain,
local-part bytes verbatim, plus-addresses distinct), and email-form
`contacted_hashes` ENTRIES stay emission-gated until the #153 T9 executed
vectors publish. Pinned rows live in
[`test-vectors/sdk/contact-hash.json`](../../../test-vectors/sdk/contact-hash.json).
Slot fields are capability-gated, never ignore-gated (SPEC.md §8): senders
MUST only include them toward a receiver advertising a full
`major.minor.patch` element with `major == 1` and `minor >= 1`, compared as
parsed semver; the example below shows the gate and the SDK validator
rejecting slot fields toward a v1.0-only advertisement.

```ts
import { contactHash, receiverAdvertisesSlotFields, LcpSchemaValidator } from "@spear-systems/lcp-sdk";

const digest = await contactHash(buyerPairSecret, "+15125550147", "phone");
validator.validateEnvelope(pingEnvelope, { receiverAdvertisedLcpVersions: caps.lcp_versions });
```
