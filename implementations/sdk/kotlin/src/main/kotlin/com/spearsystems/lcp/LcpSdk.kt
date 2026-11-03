package com.spearsystems.lcp

import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Duration
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.HexFormat
import java.util.UUID
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

data class Message(val id: String, val type: String, val timestamp: String, val senderId: String, val receiverId: String, val idempotencyKey: String, val test: Boolean = false)
data class Envelope(val json: String, val message: Message)

object LcpSdk {
    /**
     * Canonical whole-second UTC wire format for `X-LCP-Timestamp` and the mirrored
     * envelope field (LEP-0002). Signers MUST emit exactly this shape; the HMAC
     * covers the transmitted string verbatim (SPEC.md §9).
     */
    val CANONICAL_TIMESTAMP_FORMAT: DateTimeFormatter =
        DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'").withZone(ZoneOffset.UTC)

    /** Format one instant in the canonical whole-second UTC wire form. */
    fun formatTimestamp(value: Instant): String = CANONICAL_TIMESTAMP_FORMAT.format(value)

    /** Current wall clock in the canonical wire form (truncates sub-second noise). */
    fun nowIsoUtc(): String = CANONICAL_TIMESTAMP_FORMAT.format(Instant.now())

    fun buildEnvelope(type: String, senderId: String, receiverId: String, payloadJson: String, test: Boolean = false): Envelope {
        val message = Message(UUID.randomUUID().toString(), type, nowIsoUtc(), senderId, receiverId, "$senderId-$type-${UUID.randomUUID()}", test)
        val messageJson = "{\"id\":\"${message.id}\",\"type\":\"$type\",\"timestamp\":\"${message.timestamp}\",\"sender_id\":\"$senderId\",\"receiver_id\":\"$receiverId\",\"correlation_id\":null,\"idempotency_key\":\"${message.idempotencyKey}\",\"test\":$test}"
        return Envelope("{\"lcp\":{\"version\":\"1.0.0\",\"message\":$messageJson,\"payload\":$payloadJson}}", message)
    }

    fun canonicalSigningBytes(timestamp: String, idempotencyKey: String?, body: ByteArray): ByteArray = ("$timestamp\n${idempotencyKey ?: ""}\n".toByteArray() + body)
    fun signHmac(secret: String, timestamp: String, idempotencyKey: String?, body: ByteArray): String = hmacHex(secret.toByteArray(), canonicalSigningBytes(timestamp, idempotencyKey, body))

    /** The single HMAC-SHA256 → lowercase-hex construction in this SDK (SPEC.md §9). */
    private fun hmacHex(key: ByteArray, data: ByteArray): String { val mac = Mac.getInstance("HmacSHA256"); mac.init(SecretKeySpec(key, "HmacSHA256")); return HexFormat.of().formatHex(mac.doFinal(data)) }

    /**
     * SPEC.md §9 pinned contact preimage. Phone: the wire value verbatim,
     * byte-for-byte — no folding, stripping, or reformatting (the reference
     * platform's phone_hash builder is the executed baseline). Email: the §9
     * pinned candidate canonical — trim surrounding whitespace,
     * ASCII-lowercase the domain, keep the local-part bytes verbatim,
     * plus-addresses distinct. Email-form contacted_hashes ENTRIES stay
     * emission-gated until #153 T9 executed vectors publish; this is the
     * pinned compute rule every contact-hash vector row reproduces under.
     */
    fun normalizeContact(value: String, domain: String): String = when (domain) {
        "phone" -> value
        "email" -> {
            val trimmed = value.trim()
            val separator = trimmed.lastIndexOf('@')
            require(separator >= 0) { "email contact preimage must contain '@'" }
            val local = trimmed.substring(0, separator)
            val host = trimmed.substring(separator + 1).map { if (it in 'A'..'Z') (it.code + 32).toChar() else it }.joinToString("")
            "$local@$host"
        }
        else -> error("unknown contact domain '$domain' (expected phone or email)")
    }

    /**
     * Per-pair contact hash (SPEC.md §9, LEP-0003 §3): lowercase-hex
     * HMAC-SHA256(pair_secret, normalized_preimage) keyed with the secret
     * shared with the message's receiver. Same digest path as [signHmac] —
     * never an unsalted digest, never SHA-1.
     */
    fun contactHash(secret: String, value: String, domain: String = "phone"): String = hmacHex(secret.toByteArray(), normalizeContact(value, domain).toByteArray())
    fun verifyHmac(secret: String, signature: String, timestamp: String, idempotencyKey: String?, body: ByteArray, maxSkew: Duration = Duration.ofMinutes(5), now: Instant = Instant.now()) { val signedAt = Instant.parse(timestamp); require(kotlin.math.abs(Duration.between(signedAt, now).seconds) <= maxSkew.seconds) { "timestamp outside replay window" }; require(MessageDigest.isEqual(signHmac(secret, timestamp, idempotencyKey, body).toByteArray(), signature.lowercase().toByteArray())) { "invalid signature" } }

    class Client(private val endpoint: String, private val senderId: String? = null, private val apiKey: String? = null, private val hmacSecret: String? = null, private val http: HttpClient = HttpClient.newHttpClient()) {
        fun request(method: String, path: String, body: String? = null, idempotencyKey: String? = null, test: Boolean = false): String {
            val raw = body?.toByteArray() ?: ByteArray(0); val builder = HttpRequest.newBuilder(URI.create("${endpoint.trimEnd('/')}/${path.trimStart('/')}" )).timeout(Duration.ofSeconds(30)).header("Accept", "application/json").header("Content-Type", "application/json"); senderId?.let { builder.header("X-LCP-Sender-Id", it) }; apiKey?.let { builder.header("Authorization", "Bearer $it") }; idempotencyKey?.let { builder.header("X-LCP-Idempotency-Key", it) }; if (test) builder.header("X-LCP-Test", "true"); hmacSecret?.let { val timestamp = nowIsoUtc(); builder.header("X-LCP-Timestamp", timestamp).header("X-LCP-Signature", signHmac(it, timestamp, idempotencyKey, raw)) }; builder.method(method, if (body == null) HttpRequest.BodyPublishers.noBody() else HttpRequest.BodyPublishers.ofByteArray(raw)); val response = http.send(builder.build(), HttpResponse.BodyHandlers.ofString()); require(response.statusCode() in 200..299) { "LCP HTTP ${response.statusCode()}: ${response.body()}" }; return response.body()
        }
        fun submitLead(envelope: Envelope) = request("POST", "/v1/lcp/leads", envelope.json, envelope.message.idempotencyKey, envelope.message.test)
        fun submitCall(envelope: Envelope) = request("POST", "/v1/lcp/calls", envelope.json, envelope.message.idempotencyKey, envelope.message.test)
        fun submitBid(envelope: Envelope) = request("POST", "/v1/lcp/bids", envelope.json, envelope.message.idempotencyKey, envelope.message.test)
        fun getCapabilities() = request("GET", "/v1/lcp/capabilities")
        fun listOffers(vertical: String? = null) = request("GET", "/v1/lcp/offers${if (vertical == null) "" else "?vertical=$vertical"}")
    }
}
