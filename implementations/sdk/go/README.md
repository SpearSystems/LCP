# LCP Go SDK

Tier 1 reference SDK for Go 1.22+ gateways, infrastructure, and high-volume
publisher or buyer services.

```bash
go get github.com/SpearSystems/LCP/implementations/sdk/go
```

From this repository:

```bash
go test ./...
```

The package provides generated payload models, `SchemaValidator` full Draft 2020-12 validation, `BuildEnvelope`, canonical HMAC signing and verification,
raw `http.Header` verification, and a standard-library `Client` for leads,
calls, bids, status, schemas, capabilities, and offers. See the shared
[SDK contract](../../../docs/SDK-ROADMAP.md).
