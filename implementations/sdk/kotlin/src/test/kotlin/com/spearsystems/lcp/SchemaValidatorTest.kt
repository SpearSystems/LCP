package com.spearsystems.lcp

import java.nio.file.Path
import kotlin.test.Test

class SchemaValidatorTest {
    @Test
    fun validatesCanonicalLeadFixture() {
        val root = Path.of("../../../")
        val validator = LcpSchemaValidator.fromDirectory(root.resolve("schemas"))
        validator.validateEnvelope(root.resolve("examples/lead.json").toFile().readText())
    }
}
