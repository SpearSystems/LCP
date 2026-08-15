# LCP .NET SDK

Tier 1 reference SDK for .NET 8+ publishers, buyers, platforms, and webhook
receivers.

```bash
dotnet add package LcpSdk
```

From this repository:

```bash
dotnet run --project tests/LcpSdk.Tests.csproj
```

The SDK provides generated payload models, `LcpSchemaValidator` full Draft 2020-12 validation, `LcpEnvelope.Build`, `LcpEnvelope.Validate`,
`LcpSigning.SignHmac`, `LcpSigning.VerifyHmac`, raw-body request verification,
and `LcpClient` operations for leads, calls, bids, status, schemas,
capabilities, and offers. Use a configured `HttpClient` with normal platform
TLS and proxy policies. See the shared [SDK contract](../../../docs/SDK-ROADMAP.md).
