package com.spearsystems.lcp;

import org.junit.jupiter.api.Test;
import java.nio.file.Path;

final class SchemaValidatorTest {
    @Test
    void validatesCanonicalLeadFixture() throws Exception {
        var root = Path.of("../../../");
        var validator = SchemaValidator.fromDirectory(root.resolve("schemas"));
        validator.validateEnvelope(java.nio.file.Files.readString(root.resolve("examples/lead.json")));
    }
}
