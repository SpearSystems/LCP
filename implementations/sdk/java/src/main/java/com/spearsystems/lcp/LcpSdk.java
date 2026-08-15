package com.spearsystems.lcp;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.UUID;

public final class LcpSdk {
    private LcpSdk() {}

    public record Message(String id, String type, String timestamp, String senderId, String receiverId, String correlationId, String idempotencyKey, boolean test) {}
    public record Envelope(String json, Message message) {}

    public static Envelope buildEnvelope(String type, String senderId, String receiverId, String payloadJson, boolean test) {
        var message = new Message(UUID.randomUUID().toString(), type, Instant.now().toString(), senderId, receiverId, null, senderId + "-" + type + "-" + UUID.randomUUID(), test);
        var messageJson = "{\"id\":\"" + escape(message.id()) + "\",\"type\":\"" + escape(type) + "\",\"timestamp\":\"" + escape(message.timestamp()) + "\",\"sender_id\":\"" + escape(senderId) + "\",\"receiver_id\":\"" + escape(receiverId) + "\",\"correlation_id\":null,\"idempotency_key\":\"" + escape(message.idempotencyKey()) + "\",\"test\":" + test + "}";
        return new Envelope("{\"lcp\":{\"version\":\"1.0.0\",\"message\":" + messageJson + ",\"payload\":" + payloadJson + "}}", message);
    }

    private static String escape(String value) { return value.replace("\\", "\\\\").replace("\"", "\\\""); }

    public static byte[] canonicalSigningBytes(String timestamp, String idempotencyKey, byte[] body) {
        var prefix = (timestamp + "\n" + (idempotencyKey == null ? "" : idempotencyKey) + "\n").getBytes(StandardCharsets.UTF_8);
        var output = new byte[prefix.length + body.length];
        System.arraycopy(prefix, 0, output, 0, prefix.length); System.arraycopy(body, 0, output, prefix.length, body.length); return output;
    }

    public static String signHmac(String secret, String timestamp, String idempotencyKey, byte[] body) {
        try { var mac = Mac.getInstance("HmacSHA256"); mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256")); return HexFormat.of().formatHex(mac.doFinal(canonicalSigningBytes(timestamp, idempotencyKey, body))); }
        catch (Exception error) { throw new IllegalStateException(error); }
    }

    public static void verifyHmac(String secret, String signature, String timestamp, String idempotencyKey, byte[] body, Duration maxSkew, Instant now) {
        var signedAt = Instant.parse(timestamp); if (Math.abs(Duration.between(signedAt, now).toSeconds()) > maxSkew.toSeconds()) throw new IllegalArgumentException("timestamp outside replay window");
        var expected = signHmac(secret, timestamp, idempotencyKey, body).getBytes(StandardCharsets.UTF_8);
        var actual = signature.toLowerCase().getBytes(StandardCharsets.UTF_8);
        if (!MessageDigest.isEqual(expected, actual)) throw new IllegalArgumentException("invalid signature");
    }

    public static final class Client {
        private final HttpClient http; private final String endpoint; private final String senderId; private final String apiKey; private final String hmacSecret;
        public Client(String endpoint, String senderId, String apiKey, String hmacSecret) { this.http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(30)).build(); this.endpoint = endpoint.replaceAll("/$", ""); this.senderId = senderId; this.apiKey = apiKey; this.hmacSecret = hmacSecret; }
        public String request(String method, String path, String body, String idempotencyKey, boolean test) throws Exception {
            var raw = body == null ? new byte[0] : body.getBytes(StandardCharsets.UTF_8); var builder = HttpRequest.newBuilder(URI.create(endpoint + "/" + path.replaceFirst("^/", ""))).timeout(Duration.ofSeconds(30)).header("Accept", "application/json").header("Content-Type", "application/json");
            if (senderId != null) builder.header("X-LCP-Sender-Id", senderId); if (apiKey != null) builder.header("Authorization", "Bearer " + apiKey); if (idempotencyKey != null) builder.header("X-LCP-Idempotency-Key", idempotencyKey); if (test) builder.header("X-LCP-Test", "true");
            if (hmacSecret != null) { var timestamp = Instant.now().toString(); builder.header("X-LCP-Timestamp", timestamp); builder.header("X-LCP-Signature", signHmac(hmacSecret, timestamp, idempotencyKey, raw)); }
            builder.method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofByteArray(raw)); var response = http.send(builder.build(), HttpResponse.BodyHandlers.ofString()); if (response.statusCode() / 100 != 2) throw new IllegalStateException("LCP HTTP " + response.statusCode() + ": " + response.body()); return response.body();
        }
        public String submitLead(Envelope envelope) throws Exception { return request("POST", "/v1/lcp/leads", envelope.json(), envelope.message().idempotencyKey(), envelope.message().test()); }
        public String submitCall(Envelope envelope) throws Exception { return request("POST", "/v1/lcp/calls", envelope.json(), envelope.message().idempotencyKey(), envelope.message().test()); }
        public String submitBid(Envelope envelope) throws Exception { return request("POST", "/v1/lcp/bids", envelope.json(), envelope.message().idempotencyKey(), envelope.message().test()); }
        public String queryLeadStatus(String id) throws Exception { return request("GET", "/v1/lcp/leads/" + URI.create("https://lcp.invalid/" + id).getRawPath(), null, null, false); }
        public String getCapabilities() throws Exception { return request("GET", "/v1/lcp/capabilities", null, null, false); }
        public String listOffers(String vertical) throws Exception { return request("GET", "/v1/lcp/offers" + (vertical == null ? "" : "?vertical=" + URI.create("https://lcp.invalid/?v=" + vertical).getRawQuery().substring(2)), null, null, false); }
    }
}
