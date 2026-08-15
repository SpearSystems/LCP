# LCP Java SDK

Tier 2 reference SDK for Java 17+ services.

The SDK provides generated payload models and `SchemaValidator` full Draft 2020-12 validation. `LcpSdk.buildEnvelope` provides canonical HMAC signing and
verification, raw-body request verification primitives, and an HTTP client for
leads, calls, bids, status, capabilities, and offers. It deliberately avoids a
JSON dependency: applications can use Jackson, Gson, or another approved
serializer for payload objects and pass the exact JSON string to the client.

```bash
mvn test
# Or compile and run the dependency-free vector smoke test:
javac -d /tmp/lcp-java-sdk src/main/java/com/spearsystems/lcp/LcpSdk.java tests/SharedVector.java
java -cp /tmp/lcp-java-sdk com.spearsystems.lcp.SharedVector
```

See the shared [SDK contract](../../../docs/SDK-ROADMAP.md).
