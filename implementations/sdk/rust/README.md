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
