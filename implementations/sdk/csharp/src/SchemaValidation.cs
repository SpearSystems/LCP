using System.Text.Json.Nodes;
using Json.Schema;

namespace LcpSdk;

public sealed class LcpSchemaValidationException(string schemaName, EvaluationResults results)
    : Exception($"LCP schema validation failed for {schemaName}")
{
    public string SchemaName { get; } = schemaName;
    public EvaluationResults Results { get; } = results;
}

/// <summary>Validates LCP documents against a complete schemas/ and verticals/ bundle.</summary>
public sealed class LcpSchemaValidator
{
    private readonly Dictionary<string, JsonSchema> schemas = new(StringComparer.OrdinalIgnoreCase);

    public LcpSchemaValidator(IReadOnlyDictionary<string, string> schemaTexts)
    {
        // JsonSchema.Net builds each document into its global registry. Build
        // core first so message schemas can resolve their external $refs.
        foreach (var (name, text) in schemaTexts.OrderBy(pair =>
                     pair.Key.Equals("core.json", StringComparison.OrdinalIgnoreCase) ? 0 : 1))
        {
            var schema = JsonSchema.FromText(text);
            schemas[Normalize(name)] = schema;
            if (name.EndsWith(".json", StringComparison.OrdinalIgnoreCase))
                schemas[Normalize(Path.GetFileNameWithoutExtension(name))] = schema;
        }
    }

    public static LcpSchemaValidator FromDirectory(string root)
    {
        var files = Directory.EnumerateFiles(root, "*.json", SearchOption.AllDirectories)
            .ToDictionary(path => Path.GetRelativePath(root, path).Replace('\\', '/'), File.ReadAllText);
        return new LcpSchemaValidator(files);
    }

    public void Validate(string schemaName, JsonNode instance)
    {
        if (!schemas.TryGetValue(Normalize(schemaName), out var schema))
            throw new KeyNotFoundException($"Unknown LCP schema: {schemaName}");
        var results = schema.Evaluate(instance, new EvaluationOptions { OutputFormat = OutputFormat.List });
        if (!results.IsValid)
            throw new LcpSchemaValidationException(schemaName, results);
    }

    public void ValidateEnvelope(JsonNode envelope)
    {
        Validate("schemas/envelope.json", envelope);
        var type = envelope["lcp"]?["message"]?["type"]?.GetValue<string>()
            ?? throw new ArgumentException("LCP envelope is missing lcp.message.type");
        Validate($"schemas/{type}.json", envelope["lcp"]?["payload"]
            ?? throw new ArgumentException("LCP envelope is missing lcp.payload"));
    }

    public void ValidateOffer(JsonNode offer) => Validate("schemas/offer.json", offer);

    public void ValidateVertical(string vertical, JsonNode attributes) => Validate($"verticals/{vertical}.json", attributes);

    private static string Normalize(string value) => value.Replace('\\', '/').TrimStart('/').Replace("schemas/", "", StringComparison.OrdinalIgnoreCase).Replace("verticals/", "vertical:", StringComparison.OrdinalIgnoreCase).Replace(".json", "", StringComparison.OrdinalIgnoreCase);
}
