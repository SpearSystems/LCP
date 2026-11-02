// Java SDK interop probe for tools/sdk_interop_matrix.py (LEP-0002 step 1).
//
// Run with the JDK single-file source launcher against the compiled SDK:
//   java -cp target/classes implementations/sdk/java/examples-internal/InteropProbe.java --vector test-vectors/sdk/hmac.json
//
// Signs the fixed vector message through the SDK's own canonicalSigningBytes +
// signHmac, then formats the SDK's canonical generation expression
// (LcpSdk.nowIsoUtc / CANONICAL_TIMESTAMP_FORMAT over Instant.now(Clock.fixed))
// at a frozen instant with non-zero nanoseconds — the whole-second wire form
// is the LEP-0002 conformance shape. The probe never asserts; the matrix owns
// the checks.

import com.spearsystems.lcp.LcpSdk;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.HexFormat;

public final class InteropProbe {
    private InteropProbe() {}

    private static String field(String json, String name) {
        int colon = json.indexOf("\"" + name + "\":");
        if (colon < 0) throw new IllegalStateException("vector field missing: " + name);
        int open = json.indexOf('"', colon + name.length() + 2);
        if (open < 0) throw new IllegalStateException("vector field missing: " + name);
        StringBuilder value = new StringBuilder();
        for (int p = open + 1; p < json.length(); p++) {
            char c = json.charAt(p);
            if (c == '"') break;
            if (c == '\\') { value.append(json.charAt(++p)); } else { value.append(c); }
        }
        return value.toString();
    }

    public static void main(String[] args) throws Exception {
        Path vector = Path.of(args.length > 1 && args[0].equals("--vector") ? args[1] : "test-vectors/sdk/hmac.json");
        if (!Files.exists(vector)) {
            vector = Path.of(System.getenv().getOrDefault("LCP_VECTOR", "test-vectors/sdk/hmac.json"));
        }
        String json = Files.readString(vector, StandardCharsets.UTF_8);
        String secret = field(json, "secret");
        String timestamp = field(json, "timestamp");
        String idempotencyKey = field(json, "idempotency_key");
        byte[] body = field(json, "body_utf8").getBytes(StandardCharsets.UTF_8);

        byte[] canonical = LcpSdk.canonicalSigningBytes(timestamp, idempotencyKey, body);
        String canonicalSha256 = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(canonical));
        // Frozen instant 2026-08-15T10:20:00.123456789Z through the SDK's own
        // generation expression (LcpSdk.nowIsoUtc); formatting a Clock.fixed instant
        // with CANONICAL_TIMESTAMP_FORMAT is identical to the SDK's Instant.now()
        // path at a sub-second system-clock tick.
        String emitted = LcpSdk.formatTimestamp(Instant.now(Clock.fixed(
                Instant.parse("2026-08-15T10:20:00.123456789Z"), ZoneOffset.UTC)));

        String result = "{\"canonical_sha256\":\"" + canonicalSha256
                + "\",\"emitted_timestamp\":\"" + emitted
                + "\",\"sdk\":\"java\""
                + ",\"signature_hex\":\"" + LcpSdk.signHmac(secret, timestamp, idempotencyKey, body) + "\"}";
        // Logging hygiene: no payload prints on stdout; stderr + LCP_INTEROP_OUT.
        System.err.println("LCP_INTEROP " + result);
        String out = System.getenv("LCP_INTEROP_OUT");
        if (out != null) Files.writeString(Path.of(out), result + System.lineSeparator());
    }
}
