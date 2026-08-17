package com.spearsystems.lcp;

import com.networknt.schema.Error;
import com.networknt.schema.InputFormat;
import com.networknt.schema.Schema;
import com.networknt.schema.SchemaLocation;
import com.networknt.schema.SchemaRegistry;
import com.networknt.schema.SpecificationVersion;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

/** Full JSON Schema 2020-12 validation for canonical schemas and verticals. */
public final class SchemaValidator {
    private final ObjectMapper mapper = new ObjectMapper();
    private final SchemaRegistry registry;
    private final Map<String, String> idsByName = new LinkedHashMap<>();
    private final Map<String, JsonNode> documents = new LinkedHashMap<>();

    public SchemaValidator(Map<String, String> schemaTexts) throws IOException {
        Map<String, String> resources = new LinkedHashMap<>();
        for (var entry : schemaTexts.entrySet()) {
            JsonNode schema = mapper.readTree(entry.getValue());
            String id = schema.path("$id").asText(entry.getKey());
            documents.put(normalize(entry.getKey()), schema);
            documents.put(normalize(id), schema);
            resources.put(id, entry.getValue());
            idsByName.put(normalize(entry.getKey()), id);
            idsByName.put(normalize(id), id);
        }
        registry = SchemaRegistry.withDefaultDialect(SpecificationVersion.DRAFT_2020_12,
            builder -> builder.schemas(resources));
    }

    public static SchemaValidator fromDirectory(Path root) throws IOException {
        Map<String, String> schemas = new LinkedHashMap<>();
        var scanRoots = new java.util.ArrayList<Path>();
        scanRoots.add(root);
        if (root.getFileName() != null && root.getFileName().toString().equals("schemas")) {
            Path verticalRoot = root.getParent() == null ? Path.of("verticals") : root.getParent().resolve("verticals");
            if (Files.isDirectory(verticalRoot)) scanRoots.add(verticalRoot);
        }
        for (Path scanRoot : scanRoots) {
            try (var paths = Files.walk(scanRoot)) {
                paths.filter(path -> path.toString().endsWith(".json")).forEach(path -> {
                    try {
                        String relative = scanRoot.relativize(path).toString().replace('\\', '/');
                        String name = scanRoot.getFileName() != null && scanRoot.getFileName().toString().equals("verticals")
                            ? "verticals/" + relative : relative;
                        schemas.put(name, Files.readString(path));
                    } catch (IOException error) {
                        throw new SchemaLoadException(error);
                    }
                });
            } catch (SchemaLoadException error) {
                throw error.ioException;
            }
        }
        return new SchemaValidator(schemas);
    }

    public void validate(String schemaName, String document) throws IOException {
        String id = idsByName.get(normalize(schemaName));
        if (id == null) throw new IllegalArgumentException("Unknown LCP schema: " + schemaName);
        Schema schema = registry.getSchema(SchemaLocation.of(id));
        List<Error> errors = schema.validate(document, InputFormat.JSON, executionContext ->
            executionContext.executionConfig(config -> config.formatAssertionsEnabled(true)));
        if (!errors.isEmpty()) {
            throw new IllegalArgumentException("LCP schema validation failed for " + schemaName + ": " + errors);
        }
    }

    public void validateEnvelope(String envelope) throws IOException {
        validate("schemas/envelope.json", envelope);
        JsonNode root = mapper.readTree(envelope);
        String type = root.path("lcp").path("message").path("type").asText();
        JsonNode payload = root.path("lcp").path("payload");
        validate("schemas/" + type + ".json", payload.toString());
        validateVerticalPolicy(type, payload);
    }

    private void validateVerticalPolicy(String type, JsonNode payload) throws IOException {
        if (!(type.equals("lead") || type.equals("call") || type.equals("post") || type.equals("ping"))) return;
        JsonNode attributes = payload.path("attributes");
        if (!attributes.isObject()) return;
        String vertical = type.equals("ping")
            ? payload.path("vertical").asText("")
            : attributes.path("vertical").asText("");
        if (vertical.isEmpty()) return;
        JsonNode schema = documents.get(normalize("verticals/" + vertical + ".json"));
        if (schema == null) throw new IllegalArgumentException("Vertical schema '" + vertical + "' not found");
        ObjectNode verticalAttributes = (ObjectNode) attributes.deepCopy();
        if (type.equals("ping")) {
            if (!verticalAttributes.has("vertical")) verticalAttributes.put("vertical", vertical);
            if (!verticalAttributes.has("schema_version")) {
                JsonNode version = schema.path("properties").path("schema_version").path("const");
                if (!version.isMissingNode()) verticalAttributes.set("schema_version", version);
            }
        }
        validate("verticals/" + vertical + ".json", verticalAttributes.toString());
        if (type.equals("ping")) validatePingSafe(vertical, attributes, schema, "attributes");
    }

    private void validatePingSafe(String vertical, JsonNode value, JsonNode schema, String path) {
        if (!value.isObject()) return;
        value.properties().forEach(field -> {
            String name = field.getKey();
            if (path.equals("attributes") && (name.equals("vertical") || name.equals("schema_version"))) return;
            JsonNode definition = schema.path("properties").path(name);
            if (!definition.path("ping_safe").asBoolean(false)) {
                throw new PingSafeValidationException(path + "." + name + " is not tagged ping_safe: true in vertical '" + vertical + "'");
            }
            if (definition.has("properties")) validatePingSafe(vertical, field.getValue(), definition, path + "." + name);
        });
    }

    private static final class PingSafeValidationException extends RuntimeException {
        private PingSafeValidationException(String message) { super(message); }
    }

    public void validateOffer(String offer) throws IOException { validate("schemas/offer.json", offer); }
    public void validateVertical(String vertical, String attributes) throws IOException {
        validate("verticals/" + vertical + ".json", attributes);
    }

    private static String normalize(String value) {
        return value.replace('\\', '/').replaceFirst("^/", "")
            .replaceFirst("^schemas/", "")
            .replaceFirst("^verticals/", "vertical:")
            .replaceFirst("\\.json$", "");
    }

    private static final class SchemaLoadException extends RuntimeException {
        private final IOException ioException;
        private SchemaLoadException(IOException error) { super(error); ioException = error; }
    }
}
