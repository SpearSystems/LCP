package com.spearsystems.lcp

import java.nio.file.Path
import kotlin.test.Test
import tools.jackson.databind.ObjectMapper

class ValidationCorpusTest {
    @Test
    fun sharedValidationCorpusOutcomes() {
        val root = Path.of("../../../")
        val validator = LcpSchemaValidator.fromDirectory(root.resolve("schemas"))
        val mapper = ObjectMapper()
        val corpus = mapper.readTree(root.resolve("test-vectors/sdk/validation-corpus.json").toFile().readText())
        val mismatches = mutableListOf<String>()
        for (fixture in corpus.get("fixtures")) {
            val id = fixture.get("id").asText()
            val rule = fixture.get("rule").asText()
            val expectPass = fixture.get("expect").asText() == "pass"
            val offer = fixture.get("offer")
            val isOffer = offer != null
            val document = if (isOffer) offer else fixture.get("envelope")
            val documentText = mapper.writeValueAsString(document)
            val passed = try {
                if (isOffer) validator.validateOffer(documentText) else validator.validateEnvelope(documentText)
                true
            } catch (e: Exception) {
                false
            }
            if (passed != expectPass) {
                mismatches.add("$id ($rule): expected ${if (expectPass) "pass" else "fail"}")
            }
        }
        check(mismatches.isEmpty()) { "Shared validation corpus mismatches: ${mismatches.joinToString("; ")}" }
    }
}
