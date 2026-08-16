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
