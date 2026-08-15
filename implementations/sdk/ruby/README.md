# LCP Ruby SDK

Tier 2 reference SDK for Ruby 3.0+ publisher, buyer, and SaaS integrations.

```bash
gem build lcp-sdk.gemspec
ruby test/shared_vector_test.rb
```

The SDK provides generated payload models, `LcpSdk::SchemaValidator` full Draft 2020-12 validation, `LcpSdk.build_envelope`, envelope validation,
`LcpSdk::Signing` raw-body HMAC helpers, webhook verification, and
`LcpSdk::Client` operations for leads, calls, bids, status, capabilities, and
offers. See the shared [SDK contract](../../../docs/SDK-ROADMAP.md).
