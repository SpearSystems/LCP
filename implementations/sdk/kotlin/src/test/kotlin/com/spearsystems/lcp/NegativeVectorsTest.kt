package com.spearsystems.lcp

import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.time.Duration
import java.time.Instant
import kotlin.test.Test
import kotlin.test.assertTrue
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper

/**
 * Kotlin SDK must refuse every attack in `test-vectors/sdk/negative/`.
 *
 * Loads the shared negative security-vector suite (format documented in
 * `test-vectors/sdk/negative/README.md`), runs each vector through this SDK's real primitives
 * ([LcpSdk.verifyHmac], [LcpSchemaValidator]), and asserts the expected outcome. Deterministic:
 * fixed clocks, repo `schemas/`, no network. A thrown exception is a reject; a clean return is an
 * accept — this SDK does not surface protocol error codes, so per the runner contract the code is
 * asserted only where a primitive would expose one (never).
 *
 * Gap vectors (`sdk_exercisable: false`) are reported loudly on stderr and never counted as
 * executed — never a silent pass. Format lint runs first so a malformed vector fails CI even when
 * behavior would accidentally match. Auto-discovered by `gradle test`.
 */
class NegativeVectorsTest {
    private data class Suite(val file: String, val node: JsonNode)

    private fun loadSuites(): List<Suite> {
        val files = try {
            Files.list(NEGATIVE_DIR).filter { it.toString().endsWith(".json") }.sorted().toList()
        } finally {
            // Files.list returns a stream that must be closed; toList() consumes it above.
        }
        if (files.isEmpty()) error("no vector files found under ${NEGATIVE_DIR.toAbsolutePath()}")
        return files.map { Suite(it.fileName.toString(), mapper.readTree(it.toFile().readText())) }
    }

    private fun iterVectors(suites: List<Suite>): List<JsonNode> =
        suites.flatMap { suite -> suite.node.path("vectors").toList() }

    @Test
    fun suiteFilesAreWellFormed() {
        val problems = mutableListOf<String>()
        val seenIds = linkedSetOf<String>()
        for (suite in loadSuites()) {
            val source = suite.file
            val node = suite.node
            if (node.path("suite").asText(null) != SUITE_NAME) problems += "$source: suite must be '$SUITE_NAME'"
            if (node.path("version").asInt(-1) != SUITE_VERSION) problems += "$source: unsupported suite version ${node.path("version").asInt(-1)}"
            for (field in listOf("group", "description")) {
                if (node.path(field).asText("").isEmpty()) problems += "$source: missing $field"
            }
            if (!node.path("vectors").isArray || node.path("vectors").isEmpty()) problems += "$source: empty vectors array"
            for (vector in node.path("vectors")) {
                val id = vector.path("id").asText("(no id)")
                val where = "$source:$id"
                for (key in REQUIRED_KEYS) if (!vector.has(key)) problems += "$where: missing field '$key'"
                if (!id.startsWith("lcp-neg-")) problems += "$where: id must start with 'lcp-neg-'"
                if (!seenIds.add(id)) problems += "$where: duplicate vector id"
                val invariant = vector.path("invariant")
                if (!invariant.isNull && !(invariant.isIntegralNumber && !invariant.isBoolean && invariant.asInt() in 1..8)) {
                    problems += "$where: invariant must be 1-8 or null"
                }
                if (vector.path("spec_ref").asText("").isEmpty()) problems += "$where: spec_ref required (even with invariant set)"
                val appliesTo = vector.path("applies_to")
                if (!appliesTo.isArray || appliesTo.isEmpty()) {
                    problems += "$where: applies_to must be a non-empty list"
                } else {
                    for (kind in appliesTo) {
                        if (!kind.isTextual) { problems += "$where: applies_to kind must be a string"; continue }
                        val value = kind.asText()
                        if (value != "*" && value !in MESSAGE_TYPES) problems += "$where: applies_to has unknown kind '$value'"
                    }
                }
                val check = vector.path("check").asText("")
                if (check !in CHECK_CODES) { problems += "$where: unknown check '$check'"; continue }
                if (!vector.path("inputs").isObject) problems += "$where: inputs must be an object"
                val expect = vector.path("expect")
                val outcome = expect.path("outcome").asText("")
                when (outcome) {
                    "reject" -> {
                        if (expect.path("error_code").asText(null) !in CHECK_CODES.getValue(check)) {
                            problems += "$where: reject expects error_code in ${CHECK_CODES.getValue(check).sorted()}, got '${expect.path("error_code").asText(null)}'"
                        }
                        if (expect.path("error_name").asText("").isEmpty()) problems += "$where: reject requires error_name"
                    }
                    "accept" -> if (!expect.path("error_code").isNull || !expect.path("error_name").isNull) {
                        problems += "$where: accept vectors must not declare an error"
                    }
                    else -> problems += "$where: expect.outcome must be reject|accept"
                }
                if (vector.path("sdk_exercisable").asBoolean(true) == false) {
                    if (vector.path("gap_note").asText("").length < 40) {
                        problems += "$where: sdk_exercisable=false requires a substantive gap_note"
                    }
                } else if (vector.has("gap_note")) {
                    problems += "$where: gap_note only allowed when sdk_exercisable is false"
                }
            }
        }
        check(problems.isEmpty()) { "negative vector format violations: $problems" }
    }

    @Test
    fun negativeVectorOutcomes() {
        val validator = LcpSchemaValidator.fromDirectory(ROOT.resolve("schemas"))
        val mismatches = mutableListOf<String>()
        val gaps = mutableListOf<String>()
        var ran = 0
        for (vector in iterVectors(loadSuites())) {
            val id = vector.path("id").asText()
            val check = vector.path("check").asText()
            val (outcome, detail) = execute(validator, vector)
            if (outcome == "gap") { gaps += "$id: $detail"; continue }
            ran++
            val expected = vector.path("expect").path("outcome").asText()
            if (outcome != expected) {
                mismatches += "$id ($check): expected $expected, got $outcome - ${detail.ifEmpty { "no detail" }} - ${vector.path("description").asText()}"
            }
        }
        for (gap in gaps) System.err.println("GAP (reported, never silently passed): $gap")
        assertTrue(ran > 0, "no negative vector was actually executed")
        check(mismatches.isEmpty()) { "negative vector outcome mismatches: $mismatches" }
    }

    /** Returns (outcome, detail): outcome is "accept", "reject", "gap", or "error". */
    private fun execute(validator: LcpSchemaValidator, vector: JsonNode): Pair<String, String> {
        if (!vector.path("sdk_exercisable").asBoolean(true)) {
            return "gap" to vector.path("gap_note").asText()
        }
        val inputs = vector.path("inputs")
        val check = vector.path("check").asText()
        return try {
            when (check) {
                "verify_hmac", "verify_raw_body" -> {
                    val bodyField = if (check == "verify_hmac") "body" else "verify_body"
                    LcpSdk.verifyHmac(
                        inputs.path("secret").asText(),
                        inputs.path("signature").asText(),
                        inputs.path("timestamp").asText(),
                        inputs.path("idempotency_key").asText(),
                        inputs.path(bodyField).asText().toByteArray(StandardCharsets.UTF_8),
                        maxSkew = Duration.ofSeconds(inputs.path("replay_window_seconds").asLong()),
                        now = Instant.parse(inputs.path("now").asText()),
                    )
                }
                "envelope_validate" -> validator.validateEnvelope(mapper.writeValueAsString(inputs.path("envelope")))
                "offer_validate" -> validator.validateOffer(mapper.writeValueAsString(inputs.path("offer")))
                // Exercisable but no adapter in this runner: format lint passed, so the vector
                // expects an SDK surface this lane has not mapped. Fail loudly, never silently pass.
                else -> return "error" to "no runner adapter for check '$check'"
            }
            "accept" to ""
        } catch (error: Throwable) {
            "reject" to "${error::class.simpleName}: ${error.message}"
        }
    }

    companion object {
        private val mapper = ObjectMapper()
        private val ROOT: Path = Path.of("../../../")
        private val NEGATIVE_DIR: Path = ROOT.resolve("test-vectors/sdk/negative")
        private const val SUITE_NAME = "lcp-sdk-negative"
        private const val SUITE_VERSION = 1
        private val REQUIRED_KEYS = setOf(
            "id", "invariant", "spec_ref", "description", "applies_to", "check", "inputs", "expect", "sdk_exercisable",
        )
        // SPEC.md §5 codes each check is allowed to expect on rejection.
        private val CHECK_CODES = mapOf(
            "verify_hmac" to setOf("LCP-012"),
            "verify_raw_body" to setOf("LCP-012"),
            "envelope_validate" to setOf("LCP-006", "LCP-008", "LCP-100"),
            "offer_validate" to setOf("LCP-100"),
            "production_mode_verify" to setOf("LCP-013"),
        )
        // SPEC.md §2/§6 closed message.type enum (validates vector applies_to kinds).
        private val MESSAGE_TYPES = setOf("lead", "call", "ping", "post", "ack", "event", "bid")
    }
}
