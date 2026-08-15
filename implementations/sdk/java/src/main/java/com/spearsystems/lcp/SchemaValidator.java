package com.spearsystems.lcp;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.networknt.schema.InputFormat;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SchemaLocation;
import com.networknt.schema.SpecVersion.VersionFlag;
import com.networknt.schema.ValidationMessage;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/** Full JSON Schema 2020-12 validation for canonical schemas and verticals. */
public final class SchemaValidator {
    private final ObjectMapper mapper = new ObjectMapper();
    private final JsonSchemaFactory factory;
    private final Map<String, String> idsByName = new LinkedHashMap<>();

    public SchemaValidator(Map<String, String> schemaTexts) throws IOException {
        Map<String, String> resources = new LinkedHashMap<>();
        for (var entry : schemaTexts.entrySet()) {
            JsonNode schema = mapper.readTree(entry.getValue());
            String id = schema.path("$id").asText(entry.getKey());
            resources.put(id, entry.getValue());
            idsByName.put(normalize(entry.getKey()), id);
            idsByName.put(normalize(id), id);
        }
        factory = JsonSchemaFactory.getInstance(VersionFlag.V202012,
            builder -> builder.schemaLoaders(loaders -> loaders.schemas(resources)));
    }

    public static SchemaValidator fromDirectory(Path root) throws IOException {
        Map<String, String> schemas = new LinkedHashMap<>();
        try (var paths = Files.walk(root)) {
            paths.filter(path -> path.toString().endsWith(".json")).forEach(path -> {
                try {
                    schemas.put(root.relativize(path).toString().replace('\\', '/'), Files.readString(path));
                } catch (IOException error) {
                    throw new SchemaLoadException(error);
                }
            });
        } catch (SchemaLoadException error) {
            throw error.ioException;
        }
        return new SchemaValidator(schemas);
    }

    public void validate(String schemaName, String document) throws IOException {
        String id = idsByName.get(normalize(schemaName));
        if (id == null) throw new IllegalArgumentException("Unknown LCP schema: " + schemaName);
        JsonSchema schema = factory.getSchema(SchemaLocation.of(id));
        Set<ValidationMessage> errors = schema.validate(document, InputFormat.JSON, executionContext ->
            executionContext.getExecutionConfig().setFormatAssertionsEnabled(true));
        if (!errors.isEmpty()) {
            throw new IllegalArgumentException("LCP schema validation failed for " + schemaName + ": " + errors);
        }
    }

    public void validateEnvelope(String envelope) throws IOException {
        validate("schemas/envelope.json", envelope);
        JsonNode root = mapper.readTree(envelope);
        String type = root.path("lcp").path("message").path("type").asText();
        validate("schemas/" + type + ".json", root.path("lcp").path("payload").toString());
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
