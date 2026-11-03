using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace LcpSdk;

public sealed record LcpMessage(
    string Id,
    string Type,
    string Timestamp,
    string SenderId,
    string ReceiverId,
    string? CorrelationId,
    string IdempotencyKey,
    bool Test = false);

public sealed record LcpEnvelope<T>(string Version, LcpMessage Message, T Payload)
{
    public object ToWire() => new { lcp = new { version = Version, message = new { id = Message.Id, type = Message.Type, timestamp = Message.Timestamp, sender_id = Message.SenderId, receiver_id = Message.ReceiverId, correlation_id = Message.CorrelationId, idempotency_key = Message.IdempotencyKey, test = Message.Test }, payload = Payload } };
}

public static class LcpEnvelope
{
    public static LcpEnvelope<T> Build<T>(string type, string senderId, string receiverId, T payload, bool test = false)
    {
        var id = Guid.NewGuid().ToString();
        var message = new LcpMessage(id, type, DateTimeOffset.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'"), senderId, receiverId, null, $"{senderId}-{type}-{Guid.NewGuid():N}", test);
        return new LcpEnvelope<T>("1.0.0", message, payload);
    }

    public static void Validate<T>(LcpEnvelope<T> envelope)
    {
        if (string.IsNullOrWhiteSpace(envelope.Version) || string.IsNullOrWhiteSpace(envelope.Message.Id) || string.IsNullOrWhiteSpace(envelope.Message.Type) || string.IsNullOrWhiteSpace(envelope.Message.Timestamp) || string.IsNullOrWhiteSpace(envelope.Message.SenderId) || string.IsNullOrWhiteSpace(envelope.Message.ReceiverId) || string.IsNullOrWhiteSpace(envelope.Message.IdempotencyKey))
            throw new ArgumentException("Invalid LCP envelope");
    }
}

public static class LcpSigning
{
    public static byte[] CanonicalSigningBytes(string timestamp, string? idempotencyKey, ReadOnlySpan<byte> body)
    {
        var prefix = Encoding.UTF8.GetBytes($"{timestamp}\n{idempotencyKey ?? string.Empty}\n");
        var output = new byte[prefix.Length + body.Length];
        prefix.CopyTo(output, 0);
        body.CopyTo(output.AsSpan(prefix.Length));
        return output;
    }

    public static string SignHmac(string secret, string timestamp, string? idempotencyKey, ReadOnlySpan<byte> body)
    {
        return HmacHex(Encoding.UTF8.GetBytes(secret), CanonicalSigningBytes(timestamp, idempotencyKey, body));
    }

    // HmacHex is the assembly's single HMAC-SHA256 → lowercase-hex construction
    // (SPEC.md §9). Signing and contact hashes share it; nothing else computes
    // a keyed digest.
    private static string HmacHex(byte[] key, byte[] data)
    {
        using var hmac = new HMACSHA256(key);
        return Convert.ToHexString(hmac.ComputeHash(data)).ToLowerInvariant();
    }

    public static string NormalizeContact(string value, string domain)
    {
        // SPEC.md §9 pinned preimages. Phone: the wire value verbatim,
        // byte-for-byte — no folding, stripping, or reformatting (the reference
        // platform's phone_hash builder is the executed baseline). Email: the
        // §9 pinned candidate canonical — trim surrounding whitespace,
        // ASCII-lowercase the domain, keep the local-part bytes verbatim,
        // plus-addresses distinct. Email-form contacted_hashes ENTRIES stay
        // emission-gated until #153 T9 executed vectors publish; this is the
        // pinned compute rule every contact-hash vector row reproduces under.
        switch (domain)
        {
            case "phone":
                return value;
            case "email":
                var trimmed = value.Trim();
                var separator = trimmed.LastIndexOf('@');
                if (separator < 0) throw new ArgumentException("email contact preimage must contain '@'");
                var local = trimmed.Substring(0, separator);
                var host = trimmed.Substring(separator + 1);
                Span<char> lower = stackalloc char[host.Length];
                for (var i = 0; i < host.Length; i++)
                {
                    var c = host[i];
                    lower[i] = c >= 'A' && c <= 'Z' ? (char)(c + 32) : c;
                }
                return local + "@" + new string(lower);
            default:
                throw new ArgumentException($"unknown contact domain '{domain}' (expected phone or email)");
        }
    }

    public static string ContactHash(string secret, string value, string domain = "phone")
    {
        // Per-pair contact hash (SPEC.md §9, LEP-0003 §3): lowercase-hex
        // HMAC-SHA256(pair_secret, normalized_preimage) keyed with the secret
        // shared with the message's receiver. phone_hash, email_hash, and
        // contacted_hashes all ride this one construction — never an unsalted
        // digest, never SHA-1.
        return HmacHex(Encoding.UTF8.GetBytes(secret), Encoding.UTF8.GetBytes(NormalizeContact(value, domain)));
    }

    public static void VerifyHmac(string secret, string signature, string timestamp, string? idempotencyKey, ReadOnlySpan<byte> body, TimeSpan? maxSkew = null, DateTimeOffset? now = null)
    {
        if (!DateTimeOffset.TryParse(timestamp, out var signedAt)) throw new ArgumentException("Invalid X-LCP-Timestamp");
        var skew = maxSkew ?? TimeSpan.FromMinutes(5);
        if (Math.Abs(((now ?? DateTimeOffset.UtcNow) - signedAt).TotalSeconds) > skew.TotalSeconds) throw new ArgumentException("X-LCP-Timestamp is outside the replay window");
        var expected = Convert.FromHexString(SignHmac(secret, timestamp, idempotencyKey, body));
        var actual = Convert.FromHexString(signature);
        if (!CryptographicOperations.FixedTimeEquals(expected, actual)) throw new ArgumentException("Invalid X-LCP-Signature");
    }

    public static void VerifyHttpRequest(string secret, HttpRequestMessage request, ReadOnlySpan<byte> rawBody, TimeSpan? maxSkew = null, DateTimeOffset? now = null)
    {
        if (!request.Headers.TryGetValues("X-LCP-Signature", out var signatureValues) || !request.Headers.TryGetValues("X-LCP-Timestamp", out var timestampValues) || !request.Headers.TryGetValues("X-LCP-Idempotency-Key", out var keyValues)) throw new ArgumentException("Missing required LCP authentication headers");
        VerifyHmac(secret, signatureValues.Single(), timestampValues.Single(), keyValues.Single(), rawBody, maxSkew, now);
    }
}

public sealed class LcpHttpException(int statusCode, string body) : Exception($"LCP HTTP {statusCode}")
{
    public int StatusCode { get; } = statusCode;
    public string ResponseBody { get; } = body;
}

public sealed class LcpClient(HttpClient httpClient, string endpoint, string? senderId = null, string? apiKey = null, string? hmacSecret = null, int maxRetries = 2)
{
    public async Task<JsonElement> RequestAsync(string method, string path, object? payload = null, bool test = false, CancellationToken cancellationToken = default)
    {
        var body = payload is null ? Array.Empty<byte>() : JsonSerializer.SerializeToUtf8Bytes(payload);
        var idempotencyKey = payload is null ? null : ExtractIdempotencyKey(payload);
        for (var attempt = 0; attempt <= Math.Max(maxRetries, 0); attempt++)
        {
            using var request = new HttpRequestMessage(new HttpMethod(method), $"{endpoint.TrimEnd('/')}/{path.TrimStart('/')}");
            request.Content = body.Length == 0 ? null : new ByteArrayContent(body);
            if (request.Content is not null) request.Content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
            request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
            if (!string.IsNullOrEmpty(senderId)) request.Headers.Add("X-LCP-Sender-Id", senderId);
            if (!string.IsNullOrEmpty(apiKey)) request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
            if (!string.IsNullOrEmpty(idempotencyKey)) request.Headers.Add("X-LCP-Idempotency-Key", idempotencyKey);
            if (test) request.Headers.Add("X-LCP-Test", "true");
            if (!string.IsNullOrEmpty(hmacSecret)) { var timestamp = DateTimeOffset.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'"); request.Headers.Add("X-LCP-Timestamp", timestamp); request.Headers.Add("X-LCP-Signature", LcpSigning.SignHmac(hmacSecret, timestamp, idempotencyKey, body)); }
            try
            {
                using var response = await httpClient.SendAsync(request, cancellationToken);
                var responseBody = await response.Content.ReadAsStringAsync(cancellationToken);
                if (response.IsSuccessStatusCode)
                {
                    using var document = JsonDocument.Parse(string.IsNullOrWhiteSpace(responseBody) ? "{}" : responseBody);
                    return document.RootElement.Clone();
                }
                if (!(response.StatusCode == HttpStatusCode.TooManyRequests || (int)response.StatusCode >= 500) || attempt == maxRetries) throw new LcpHttpException((int)response.StatusCode, responseBody);
            }
            catch (HttpRequestException) when (attempt < maxRetries) { }
            await Task.Delay(TimeSpan.FromSeconds(Math.Pow(2, attempt)), cancellationToken);
        }
        throw new InvalidOperationException("LCP request failed");
    }

    public Task<JsonElement> SubmitLeadAsync<T>(LcpEnvelope<T> envelope, bool test = false, CancellationToken cancellationToken = default) { LcpEnvelope.Validate(envelope); return RequestAsync("POST", "/v1/lcp/leads", envelope.ToWire(), test, cancellationToken); }
    public Task<JsonElement> SubmitCallAsync<T>(LcpEnvelope<T> envelope, bool test = false, CancellationToken cancellationToken = default) { LcpEnvelope.Validate(envelope); return RequestAsync("POST", "/v1/lcp/calls", envelope.ToWire(), test, cancellationToken); }
    public Task<JsonElement> SubmitBidAsync<T>(LcpEnvelope<T> envelope, bool test = false, CancellationToken cancellationToken = default) { LcpEnvelope.Validate(envelope); return RequestAsync("POST", "/v1/lcp/bids", envelope.ToWire(), test, cancellationToken); }
    public Task<JsonElement> QueryLeadStatusAsync(string id, CancellationToken cancellationToken = default) => RequestAsync("GET", $"/v1/lcp/leads/{Uri.EscapeDataString(id)}", cancellationToken: cancellationToken);
    public Task<JsonElement> GetSchemaAsync(string name, CancellationToken cancellationToken = default) => RequestAsync("GET", $"/v1/lcp/schemas/{name}", cancellationToken: cancellationToken);
    public Task<JsonElement> GetCapabilitiesAsync(CancellationToken cancellationToken = default) => RequestAsync("GET", "/v1/lcp/capabilities", cancellationToken: cancellationToken);
    public Task<JsonElement> ListOffersAsync(string? vertical = null, CancellationToken cancellationToken = default) => RequestAsync("GET", "/v1/lcp/offers" + (vertical is null ? "" : $"?vertical={Uri.EscapeDataString(vertical)}"), cancellationToken: cancellationToken);

    private static string? ExtractIdempotencyKey(object payload)
    {
        using var document = JsonDocument.Parse(JsonSerializer.Serialize(payload));
        return document.RootElement.TryGetProperty("lcp", out var lcp) && lcp.TryGetProperty("message", out var message) && message.TryGetProperty("idempotency_key", out var key) ? key.GetString() : null;
    }
}
