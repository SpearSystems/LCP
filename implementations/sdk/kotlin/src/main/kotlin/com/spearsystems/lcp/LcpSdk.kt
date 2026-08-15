package com.spearsystems.lcp

import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Duration
import java.time.Instant
import java.util.HexFormat
import java.util.UUID
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

data class Message(val id: String, val type: String, val timestamp: String, val senderId: String, val receiverId: String, val idempotencyKey: String, val test: Boolean = false)
data class Envelope(val json: String, val message: Message)

object LcpSdk {
    fun buildEnvelope(type: String, senderId: String, receiverId: String, payloadJson: String, test: Boolean = false): Envelope {
        val message = Message(UUID.randomUUID().toString(), type, Instant.now().toString(), senderId, receiverId, "$senderId-$type-${UUID.randomUUID()}", test)
        val messageJson = "{\"id\":\"${message.id}\",\"type\":\"$type\",\"timestamp\":\"${message.timestamp}\",\"sender_id\":\"$senderId\",\"receiver_id\":\"$receiverId\",\"correlation_id\":null,\"idempotency_key\":\"${message.idempotencyKey}\",\"test\":$test}"
        return Envelope("{\"lcp\":{\"version\":\"1.0.0\",\"message\":$messageJson,\"payload\":$payloadJson}}", message)
    }

    fun canonicalSigningBytes(timestamp: String, idempotencyKey: String?, body: ByteArray): ByteArray = ("$timestamp\n${idempotencyKey ?: ""}\n".toByteArray() + body)
    fun signHmac(secret: String, timestamp: String, idempotencyKey: String?, body: ByteArray): String { val mac = Mac.getInstance("HmacSHA256"); mac.init(SecretKeySpec(secret.toByteArray(), "HmacSHA256")); return HexFormat.of().formatHex(mac.doFinal(canonicalSigningBytes(timestamp, idempotencyKey, body))) }
    fun verifyHmac(secret: String, signature: String, timestamp: String, idempotencyKey: String?, body: ByteArray, maxSkew: Duration = Duration.ofMinutes(5), now: Instant = Instant.now()) { val signedAt = Instant.parse(timestamp); require(kotlin.math.abs(Duration.between(signedAt, now).seconds) <= maxSkew.seconds) { "timestamp outside replay window" }; require(MessageDigest.isEqual(signHmac(secret, timestamp, idempotencyKey, body).toByteArray(), signature.lowercase().toByteArray())) { "invalid signature" } }

    class Client(private val endpoint: String, private val senderId: String? = null, private val apiKey: String? = null, private val hmacSecret: String? = null, private val http: HttpClient = HttpClient.newHttpClient()) {
        fun request(method: String, path: String, body: String? = null, idempotencyKey: String? = null, test: Boolean = false): String {
            val raw = body?.toByteArray() ?: ByteArray(0); val builder = HttpRequest.newBuilder(URI.create("${endpoint.trimEnd('/')}/${path.trimStart('/')}" )).timeout(Duration.ofSeconds(30)).header("Accept", "application/json").header("Content-Type", "application/json"); senderId?.let { builder.header("X-LCP-Sender-Id", it) }; apiKey?.let { builder.header("Authorization", "Bearer $it") }; idempotencyKey?.let { builder.header("X-LCP-Idempotency-Key", it) }; if (test) builder.header("X-LCP-Test", "true"); hmacSecret?.let { val timestamp = Instant.now().toString(); builder.header("X-LCP-Timestamp", timestamp).header("X-LCP-Signature", signHmac(it, timestamp, idempotencyKey, raw)) }; builder.method(method, if (body == null) HttpRequest.BodyPublishers.noBody() else HttpRequest.BodyPublishers.ofByteArray(raw)); val response = http.send(builder.build(), HttpResponse.BodyHandlers.ofString()); require(response.statusCode() in 200..299) { "LCP HTTP ${response.statusCode()}: ${response.body()}" }; return response.body()
        }
        fun submitLead(envelope: Envelope) = request("POST", "/v1/lcp/leads", envelope.json, envelope.message.idempotencyKey, envelope.message.test)
        fun submitCall(envelope: Envelope) = request("POST", "/v1/lcp/calls", envelope.json, envelope.message.idempotencyKey, envelope.message.test)
        fun submitBid(envelope: Envelope) = request("POST", "/v1/lcp/bids", envelope.json, envelope.message.idempotencyKey, envelope.message.test)
        fun getCapabilities() = request("GET", "/v1/lcp/capabilities")
        fun listOffers(vertical: String? = null) = request("GET", "/v1/lcp/offers${if (vertical == null) "" else "?vertical=$vertical"}")
    }
}
