package com.spearsystems.lcp

import com.networknt.schema.InputFormat
import com.networknt.schema.SchemaLocation
import com.networknt.schema.SchemaRegistry
import com.networknt.schema.SpecificationVersion
import java.nio.file.Files
import java.nio.file.Path
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import tools.jackson.databind.node.ObjectNode

class LcpSchemaValidator(schemaTexts: Map<String, String>) {
    private val mapper = ObjectMapper()
    private val ids = mutableMapOf<String, String>()
    private val documents = mutableMapOf<String, JsonNode>()
    private val registry: SchemaRegistry

    init {
        val resources = linkedMapOf<String, String>()
        schemaTexts.forEach { (name, text) ->
            val schema = mapper.readTree(text)
            val id = schema.path("\$id").asText(name)
            documents[normalize(name)] = schema
            documents[normalize(id)] = schema
            resources[id] = text
            ids[normalize(name)] = id
            ids[normalize(id)] = id
        }
        registry = SchemaRegistry.withDefaultDialect(SpecificationVersion.DRAFT_2020_12) { builder ->
            builder.schemas(resources)
        }
    }

    fun validate(schemaName: String, document: String) {
        val id = ids[normalize(schemaName)] ?: error("Unknown LCP schema: $schemaName")
        val errors = registry.getSchema(SchemaLocation.of(id)).validate(document, InputFormat.JSON) { executionContext ->
            executionContext.executionConfig { config -> config.formatAssertionsEnabled(true) }
        }
        require(errors.isEmpty()) { "LCP schema validation failed for $schemaName: $errors" }
    }

    fun validateEnvelope(envelope: String) {
        validate("schemas/envelope.json", envelope)
        val root = mapper.readTree(envelope)
        val type = root.path("lcp").path("message").path("type").asText(null) ?: error("Missing lcp.message.type")
        val payload = root.path("lcp").path("payload")
        validate("schemas/$type.json", payload.toString())
        validateVerticalPolicy(type, payload)
    }

    private fun validateVerticalPolicy(type: String, payload: JsonNode) {
        if (type !in setOf("lead", "call", "post", "ping")) return
        val attributes = payload.path("attributes")
        if (!attributes.isObject) return
        val vertical = if (type == "ping") payload.path("vertical").asText("")
        else attributes.path("vertical").asText("")
        if (vertical.isEmpty()) return
        val schema = documents[normalize("verticals/$vertical.json")]
            ?: error("Vertical schema '$vertical' not found")
        val verticalAttributes = mapper.readTree(attributes.toString()) as ObjectNode
        if (type == "ping") {
            if (!verticalAttributes.has("vertical")) verticalAttributes.put("vertical", vertical)
            if (!verticalAttributes.has("schema_version")) {
                val version = schema.path("properties").path("schema_version").path("const")
                if (!version.isMissingNode) verticalAttributes.set<JsonNode>("schema_version", version)
            }
        }
        validate("verticals/$vertical.json", verticalAttributes.toString())
        if (type == "ping") validatePingSafe(vertical, attributes, schema, "attributes")
    }

    private fun validatePingSafe(vertical: String, value: JsonNode, schema: JsonNode, path: String) {
        if (!value.isObject) return
        val fields = value.fields()
        while (fields.hasNext()) {
            val field = fields.next()
            val name = field.key
            if (path == "attributes" && (name == "vertical" || name == "schema_version")) continue
            val definition = schema.path("properties").path(name)
            if (!definition.path("ping_safe").asBoolean(false)) {
                error("$path.$name is not tagged ping_safe: true in vertical '$vertical'")
            }
            if (definition.has("properties")) validatePingSafe(vertical, field.value, definition, "$path.$name")
        }
    }

    fun validateOffer(offer: String) = validate("schemas/offer.json", offer)
    fun validateVertical(vertical: String, attributes: String) = validate("verticals/$vertical.json", attributes)

    companion object {
        fun fromDirectory(root: Path): LcpSchemaValidator {
            val documents = linkedMapOf<String, String>()
            val roots = mutableListOf(root)
            if (root.fileName?.toString() == "schemas") {
                val verticalRoot = root.parent?.resolve("verticals") ?: Path.of("verticals")
                if (Files.isDirectory(verticalRoot)) roots += verticalRoot
            }
            roots.forEach { scanRoot ->
                Files.walk(scanRoot).use { paths ->
                    paths.filter { Files.isRegularFile(it) && it.toString().endsWith(".json") }.forEach {
                        val relative = scanRoot.relativize(it).toString().replace('\\', '/')
                        val name = if (scanRoot.fileName?.toString() == "verticals") "verticals/$relative" else relative
                        documents[name] = Files.readString(it)
                    }
                }
            }
            return LcpSchemaValidator(documents)
        }
    }

    private fun normalize(value: String): String = value.replace('\\', '/').trimStart('/')
        .removePrefix("schemas/").removePrefix("verticals/").removeSuffix(".json")
}
