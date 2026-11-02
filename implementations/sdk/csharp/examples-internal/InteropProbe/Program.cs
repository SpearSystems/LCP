// C# SDK interop probe for tools/sdk_interop_matrix.py (LEP-0002 step 1).
//
// Signs the fixed vector message through the SDK's own
// LcpSigning.CanonicalSigningBytes + LcpSigning.SignHmac, then formats the
// SDK's generation expression (LcpSdk.cs:
// DateTimeOffset.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")) at a frozen
// instant with non-zero sub-seconds — the fixed layout drops the fraction,
// so C# is compliant today. The probe never asserts; the matrix owns checks.

using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using LcpSdk;

static string ResolveVectorPath(string[] args)
{
    for (var i = 0; i + 1 < args.Length; i++)
        if (args[i] == "--vector") return args[i + 1];
    var fromEnv = Environment.GetEnvironmentVariable("LCP_VECTOR");
    if (!string.IsNullOrEmpty(fromEnv)) return fromEnv;
    var directory = new DirectoryInfo(Directory.GetCurrentDirectory());
    while (directory != null)
    {
        var candidate = Path.Combine(directory.FullName, "test-vectors", "sdk", "hmac.json");
        if (File.Exists(candidate)) return candidate;
        directory = directory.Parent;
    }
    throw new InvalidOperationException("hmac.json vector not found (pass --vector)");
}

using (var vectorStream = File.OpenRead(ResolveVectorPath(args)))
{
    var vector = JsonDocument.Parse(vectorStream).RootElement;
    var timestamp = vector.GetProperty("timestamp").GetString()!;
    var idempotencyKey = vector.GetProperty("idempotency_key").GetString()!;
    var secret = vector.GetProperty("secret").GetString()!;
    var body = Encoding.UTF8.GetBytes(vector.GetProperty("body_utf8").GetString()!);

    var canonical = LcpSigning.CanonicalSigningBytes(timestamp, idempotencyKey, body);
    // Frozen instant 2026-08-15T10:20:00.123456789Z (ticks keep the fraction)
    // through the SDK's own generation expression.
    var frozen = new DateTimeOffset(2026, 8, 15, 10, 20, 0, 123, TimeSpan.Zero).AddTicks(45678);
    var result = new SortedDictionary<string, string>
    {
        ["canonical_sha256"] = Convert.ToHexString(SHA256.HashData(canonical)).ToLowerInvariant(),
        ["emitted_timestamp"] = frozen.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'"),
        ["sdk"] = "csharp",
        ["signature_hex"] = LcpSigning.SignHmac(secret, timestamp, idempotencyKey, body),
    };
    var payload = JsonSerializer.Serialize(result);
    Console.WriteLine($"LCP_INTEROP {payload}");
    var outPath = Environment.GetEnvironmentVariable("LCP_INTEROP_OUT");
    if (!string.IsNullOrEmpty(outPath)) File.WriteAllText(outPath, payload + Environment.NewLine);
}
