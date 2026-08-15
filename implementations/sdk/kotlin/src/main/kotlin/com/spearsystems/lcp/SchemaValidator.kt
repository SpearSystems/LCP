package com.spearsystems.lcp

import com.fasterxml.jackson.databind.ObjectMapper
import com.networknt.schema.InputFormat
import com.networknt.schema.JsonSchemaFactory
import com.networknt.schema.SchemaLocation
import com.networknt.schema.SpecVersion.VersionFlag
import java.nio.file.Files
import java.nio.file.Path

class LcpSchemaValidator(schemaTexts: Map<String, String>) {
    private val mapper = ObjectMapper()
    private val ids = mutableMapOf<String, String>()
    private val factory: JsonSchemaFactory

    init {
        val resources = linkedMapOf<String, String>()
        schemaTexts.forEach { (name, text) ->
            val id = mapper.readTree(text).path("\$id").asText(name)
            resources[id] = text
            ids[normalize(name)] = id
            ids[normalize(id)] = id
        }
        factory = JsonSchemaFactory.getInstance(VersionFlag.V202012) { builder ->
            builder.schemaLoaders { loaders -> loaders.schemas(resources) }
        }
    }

    fun validate(schemaName: String, document: String) {
        val id = ids[normalize(schemaName)] ?: error("Unknown LCP schema: $schemaName")
        val errors = factory.getSchema(SchemaLocation.of(id)).validate(document, InputFormat.JSON) { executionContext ->
            executionContext.executionConfig.setFormatAssertionsEnabled(true)
        }
        require(errors.isEmpty()) { "LCP schema validation failed for $schemaName: $errors" }
    }

    fun validateEnvelope(envelope: String) {
        validate("schemas/envelope.json", envelope)
        val root = mapper.readTree(envelope)
        val type = root.path("lcp").path("message").path("type").asText(null) ?: error("Missing lcp.message.type")
        validate("schemas/$type.json", root.path("lcp").path("payload").toString())
    }

    fun validateOffer(offer: String) = validate("schemas/offer.json", offer)
    fun validateVertical(vertical: String, attributes: String) = validate("verticals/$vertical.json", attributes)

    companion object {
        fun fromDirectory(root: Path): LcpSchemaValidator {
            val documents = linkedMapOf<String, String>()
            Files.walk(root).use { paths ->
                paths.filter { Files.isRegularFile(it) && it.toString().endsWith(".json") }.forEach {
                    documents[root.relativize(it).toString().replace('\\', '/')] = Files.readString(it)
                }
            }
            return LcpSchemaValidator(documents)
        }
    }

    private fun normalize(value: String): String = value.replace('\\', '/').trimStart('/')
        .removePrefix("schemas/").removePrefix("verticals/").removeSuffix(".json")
}
