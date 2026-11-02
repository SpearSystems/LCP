package com.spearsystems.lcp

import java.io.File
import java.nio.file.Files
import java.security.MessageDigest
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import java.util.HexFormat
import kotlin.test.Test

/**
 * Kotlin SDK interop probe for tools/sdk_interop_matrix.py (LEP-0002 step 1).
 *
 * Signs the fixed vector message through the SDK's own canonicalSigningBytes +
 * signHmac, then formats the SDK's canonical generation expression
 * (LcpSdk.nowIsoUtc / CANONICAL_TIMESTAMP_FORMAT over Instant.now(Clock.fixed))
 * at a frozen instant with non-zero nanoseconds — the whole-second wire form is
 * the LEP-0002 conformance shape. Runs under `gradle test --tests '*InteropProbeTest*'; emits the
 * `LCP_INTEROP {json}` line on stderr and writes LCP_INTEROP_OUT when set.
 * Never asserts; the matrix owns the checks.
 */
class InteropProbeTest {
    @Test
    fun emitInteropProbe() {
        val root = generateSequence(File(".").absoluteFile) { it.parentFile }
            .first { File(it, "test-vectors/sdk/hmac.json").exists() }
        val jsonFile = File(root, "test-vectors/sdk/hmac.json")
        val vector = tools.jackson.databind.ObjectMapper().readTree(jsonFile)
        val secret = vector.get("secret").asText()
        val timestamp = vector.get("timestamp").asText()
        val idempotencyKey = vector.get("idempotency_key").asText()
        val body = vector.get("body_utf8").asText().toByteArray()

        val canonical = LcpSdk.canonicalSigningBytes(timestamp, idempotencyKey, body)
        val canonicalSha256 = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(canonical))
        val emitted = LcpSdk.formatTimestamp(Instant.now(Clock.fixed(Instant.parse("2026-08-15T10:20:00.123456789Z"), ZoneOffset.UTC)))

        val result = "{\"canonical_sha256\":\"$canonicalSha256\"" +
            ",\"emitted_timestamp\":\"$emitted\"" +
            ",\"sdk\":\"kotlin\"" +
            ",\"signature_hex\":\"${LcpSdk.signHmac(secret, timestamp, idempotencyKey, body)}\"}"
        // Logging hygiene: no payload prints on stdout; stderr + LCP_INTEROP_OUT.
        System.err.println("LCP_INTEROP $result")
        val out = System.getenv("LCP_INTEROP_OUT")?.takeIf { it.isNotEmpty() }
            ?: File(root, "implementations/sdk/kotlin/build/interop-probe.json").path
        File(out).let { path ->
            path.parentFile?.mkdirs()
            Files.writeString(path.toPath(), result + System.lineSeparator())
        }
    }
}
