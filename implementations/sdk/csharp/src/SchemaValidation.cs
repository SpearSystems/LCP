using System.Text.Json;
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
    private readonly Dictionary<string, JsonNode> documents = new(StringComparer.OrdinalIgnoreCase);
    private readonly SchemaRegistry registry = new();

    public LcpSchemaValidator(IReadOnlyDictionary<string, string> schemaTexts)
    {
        // Keep registrations isolated per validator instance. Build core first
        // so message schemas can resolve their external $refs in this registry.
        var buildOptions = new BuildOptions { SchemaRegistry = registry, Dialect = Dialect.Draft202012 };
        foreach (var (name, text) in schemaTexts.OrderBy(pair =>
                     Path.GetFileName(pair.Key).Equals("core.json", StringComparison.OrdinalIgnoreCase) ? 0 : 1))
        {
            var document = JsonNode.Parse(text) ?? throw new InvalidDataException($"Empty schema: {name}");
            var schema = JsonSchema.FromText(text, buildOptions);
            documents[Normalize(name)] = document;
            var schemaId = document["$id"]?.GetValue<string>();
            if (schemaId is not null) documents[Normalize(schemaId)] = document;
            schemas[Normalize(name)] = schema;
            if (name.EndsWith(".json", StringComparison.OrdinalIgnoreCase))
                schemas[Normalize(Path.GetFileNameWithoutExtension(name))] = schema;
        }
    }

    public static LcpSchemaValidator FromDirectory(string root)
    {
        var rootPath = Path.GetFullPath(root);
        var files = Directory.EnumerateFiles(rootPath, "*.json", SearchOption.AllDirectories)
            .ToDictionary(path => Path.GetRelativePath(rootPath, path).Replace('\\', '/'), File.ReadAllText);
        if (Path.GetFileName(rootPath).Equals("schemas", StringComparison.OrdinalIgnoreCase))
        {
            var verticalRoot = Path.Combine(Directory.GetParent(rootPath)?.FullName ?? ".", "verticals");
            if (Directory.Exists(verticalRoot))
            {
                foreach (var path in Directory.EnumerateFiles(verticalRoot, "*.json", SearchOption.AllDirectories))
                {
                    files["verticals/" + Path.GetRelativePath(verticalRoot, path).Replace('\\', '/')] = File.ReadAllText(path);
                }
            }
        }
        return new LcpSchemaValidator(files);
    }

    public void Validate(string schemaName, JsonNode instance)
    {
        if (!schemas.TryGetValue(Normalize(schemaName), out var schema))
            throw new KeyNotFoundException($"Unknown LCP schema: {schemaName}");
        using var document = JsonDocument.Parse(instance.ToJsonString());
        var results = schema.Evaluate(document.RootElement, new EvaluationOptions { OutputFormat = OutputFormat.List });
        if (!results.IsValid)
            throw new LcpSchemaValidationException(schemaName, results);
    }

    public void ValidateEnvelope(JsonNode envelope)
    {
        Validate("schemas/envelope.json", envelope);
        var type = envelope["lcp"]?["message"]?["type"]?.GetValue<string>()
            ?? throw new ArgumentException("LCP envelope is missing lcp.message.type");
        var payload = envelope["lcp"]?["payload"]
            ?? throw new ArgumentException("LCP envelope is missing lcp.payload");
        Validate($"schemas/{type}.json", payload);
        ValidateVerticalPolicy(type, payload);
    }

    private void ValidateVerticalPolicy(string type, JsonNode payload)
    {
        if (type is not ("lead" or "call" or "post" or "ping")) return;
        if (payload["attributes"] is not JsonObject attributes) return;
        var vertical = type == "ping"
            ? payload["vertical"]?.GetValue<string>()
            : attributes["vertical"]?.GetValue<string>();
        if (string.IsNullOrWhiteSpace(vertical)) return;
        if (!documents.TryGetValue(Normalize($"verticals/{vertical}.json"), out var schema))
            throw new KeyNotFoundException($"Vertical schema '{vertical}' not found");
        var verticalAttributes = JsonNode.Parse(attributes.ToJsonString())!.AsObject();
        if (type == "ping")
        {
            verticalAttributes["vertical"] ??= vertical;
            verticalAttributes["schema_version"] ??= schema["properties"]?["schema_version"]?["const"]?.DeepClone();
        }
        Validate($"verticals/{vertical}.json", verticalAttributes);
        if (type == "ping") ValidatePingSafe(vertical, attributes, schema, "attributes");
    }

    private static void ValidatePingSafe(string vertical, JsonNode value, JsonNode schema, string path)
    {
        if (value is not JsonObject objectValue) return;
        var properties = schema["properties"] as JsonObject;
        foreach (var (name, child) in objectValue)
        {
            if (path == "attributes" && (name == "vertical" || name == "schema_version")) continue;
            var definition = properties?[name] as JsonObject;
            if (definition?["ping_safe"]?.GetValue<bool>() != true)
                throw new ArgumentException($"{path}.{name} is not tagged ping_safe: true in vertical '{vertical}'");
            if (definition["properties"] is JsonObject && child is JsonObject)
                ValidatePingSafe(vertical, child, definition, $"{path}.{name}");
        }
    }

    public void ValidateOffer(JsonNode offer) => Validate("schemas/offer.json", offer);

    public void ValidateVertical(string vertical, JsonNode attributes) => Validate($"verticals/{vertical}.json", attributes);

    private static string Normalize(string value) => value.Replace('\\', '/').TrimStart('/').Replace("schemas/", "", StringComparison.OrdinalIgnoreCase).Replace("verticals/", "vertical:", StringComparison.OrdinalIgnoreCase).Replace(".json", "", StringComparison.OrdinalIgnoreCase);
}
