# LCP Rust SDK

Tier 2 reference SDK for Rust services that need explicit signing and
transport control.

```bash
cargo test
```

The crate provides generated payload models, `SchemaValidator` full Draft 2020-12 validation, envelope construction, canonical HMAC-SHA256 signing and
verification, raw-body verification, and a blocking HTTP client with retry
handling for leads, calls, bids, and discovery operations. TLS is provided by
`reqwest` with rustls. See the shared [SDK contract](../../../docs/SDK-ROADMAP.md).

`X-LCP-Timestamp` and every envelope `lcp.message.timestamp` default are
emitted in the canonical whole-second UTC wire form `YYYY-MM-DDTHH:MM:SSZ`
(SPEC.md §9, LEP-0002); the HMAC signs the exact transmitted string.
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

```rust
let digest = lcp_sdk::contact_hash(buyer_pair_secret, "+15125550147", "phone")?;
validator.validate_envelope_for_receiver(&ping_envelope, &advertised_lcp_versions)?;
```
