using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;
using LcpSdk;

// C# SDK must refuse every attack in test-vectors/sdk/negative/.
// Python reference: implementations/sdk/python/tests/test_negative_vectors.py
internal static class NegativeVectors
{
    // Returns the number of vectors actually executed; throws on any mismatch.
    public static int Run(string repositoryRoot)
    {
        var dir = Path.Combine(repositoryRoot, "test-vectors", "sdk", "negative");
        var files = Directory.EnumerateFiles(dir, "*.json")
            .OrderBy(path => path, StringComparer.Ordinal)
            .ToList();
        if (files.Count == 0)
            throw new Exception($"no vector files found under {dir}");

        var validator = LcpSchemaValidator.FromDirectory(Path.Combine(repositoryRoot, "schemas"));
        var mismatches = new List<string>();
        var gaps = new List<string>();
        var ran = 0;
        foreach (var path in files)
        {
            var suite = JsonNode.Parse(File.ReadAllText(path))!.AsObject();
            foreach (var node in suite["vectors"]!.AsArray())
            {
                var vector = node!.AsObject();
                var id = vector["id"]!.GetValue<string>();
                var check = vector["check"]!.GetValue<string>();
                var expected = vector["expect"]!["outcome"]!.GetValue<string>();
                if (!vector["sdk_exercisable"]!.GetValue<bool>())
                {
                    gaps.Add($"{id}: {vector["gap_note"]!.GetValue<string>()}");
                    continue;
                }
                var (outcome, detail) = Execute(validator, vector["inputs"]!.AsObject(), check);
                ran++;
                if (outcome != expected)
                    mismatches.Add($"{id} ({check}): expected {expected}, got {outcome} — {detail} — {vector["description"]!.GetValue<string>()}");
            }
        }
        foreach (var gap in gaps)
            Console.Error.WriteLine($"GAP (reported, never silently passed): {gap}");
        if (mismatches.Count > 0)
            throw new Exception("Negative vector mismatches: " + string.Join("; ", mismatches));
        if (ran == 0)
            throw new Exception("no negative vector was actually executed");
        return ran;
    }

    private static (string Outcome, string Detail) Execute(LcpSchemaValidator validator, JsonObject inputs, string check)
    {
        try
        {
            switch (check)
            {
                case "verify_hmac":
                case "verify_raw_body":
                {
                    var body = Encoding.UTF8.GetBytes((check == "verify_hmac" ? inputs["body"] : inputs["verify_body"])!.GetValue<string>());
                    LcpSigning.VerifyHmac(
                        inputs["secret"]!.GetValue<string>(),
                        inputs["signature"]!.GetValue<string>(),
                        inputs["timestamp"]!.GetValue<string>(),
                        inputs["idempotency_key"]?.GetValue<string>(),
                        body,
                        TimeSpan.FromSeconds(inputs["replay_window_seconds"]!.GetValue<int>()),
                        DateTimeOffset.Parse(inputs["now"]!.GetValue<string>(), CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind));
                    return ("accept", "");
                }
                case "envelope_validate":
                    validator.ValidateEnvelope(inputs["envelope"]!);
                    return ("accept", "");
                case "offer_validate":
                    validator.ValidateOffer(inputs["offer"]!);
                    return ("accept", "");
                default:
                    // Exercisable but unmapped check: fail loudly, never skip.
                    throw new NotSupportedException($"no runner adapter for check '{check}'");
            }
        }
        catch (Exception ex) when (ex is not NotSupportedException)
        {
            return ("reject", $"{ex.GetType().Name}: {ex.Message}");
        }
    }
}
