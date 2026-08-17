package com.spearsystems.lcp;

import org.junit.jupiter.api.Test;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

final class ValidationCorpusTest {
    private static final ObjectMapper MAPPER = new ObjectMapper();

    @Test
    void sharedValidationCorpusOutcomes() throws Exception {
        var root = Path.of("../../../");
        var validator = SchemaValidator.fromDirectory(root.resolve("schemas"));
        var corpus = MAPPER.readTree(Files.readString(root.resolve("test-vectors/sdk/validation-corpus.json")));
        var mismatches = new ArrayList<String>();
        for (var fixture : corpus.get("fixtures")) {
            var id = fixture.get("id").asText();
            var rule = fixture.get("rule").asText();
            var expectPass = fixture.get("expect").asText().equals("pass");
            var offer = fixture.get("offer");
            var isOffer = offer != null;
            var document = isOffer ? offer : fixture.get("envelope");
            var documentText = MAPPER.writeValueAsString(document);
            var passed = true;
            try {
                if (isOffer) {
                    validator.validateOffer(documentText);
                } else {
                    validator.validateEnvelope(documentText);
                }
            } catch (Exception e) {
                passed = false;
            }
            if (passed != expectPass) {
                mismatches.add(id + " (" + rule + "): expected " + (expectPass ? "pass" : "fail"));
            }
        }
        if (!mismatches.isEmpty()) {
            throw new AssertionError("Shared validation corpus mismatches: " + String.join("; ", mismatches));
        }
    }
}
