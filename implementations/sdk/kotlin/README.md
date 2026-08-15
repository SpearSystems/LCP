# LCP Kotlin SDK

Tier 2 reference SDK for Kotlin/JVM 17+ services and Android-adjacent
applications.

```bash
./gradlew test
```

The SDK provides generated payload models and `LcpSchemaValidator` full Draft 2020-12 validation. The JDK-based SDK also provides envelope construction, canonical HMAC signing and
verification, raw-body verification primitives, and an HTTP client for leads,
calls, bids, capabilities, and offers. Use the application's approved JSON
serializer for complex payloads. See the shared [SDK contract](../../../docs/SDK-ROADMAP.md).
