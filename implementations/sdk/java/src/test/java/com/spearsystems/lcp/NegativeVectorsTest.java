package com.spearsystems.lcp;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

import org.junit.jupiter.api.Test;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/**
 * Java SDK must refuse every attack in {@code test-vectors/sdk/negative/}.
 *
 * <p>Loads the shared negative security-vector suite (format documented in
 * {@code test-vectors/sdk/negative/README.md}), runs each vector through this SDK's real
 * primitives ({@link LcpSdk#verifyHmac}, {@link SchemaValidator}), and asserts the expected
 * outcome. Deterministic: fixed clocks, repo {@code schemas/}, no network. A thrown exception is
 * a reject; a clean return is an accept — this SDK does not surface protocol error codes, so
 * per the runner contract the code is asserted only where a primitive would expose one (never).
 *
 * <p>Gap vectors ({@code sdk_exercisable: false}) are reported loudly on stderr and never counted
 * as executed — never a silent pass. Format lint runs first so a malformed vector fails CI even
 * when behavior would accidentally match. Auto-discovered by {@code mvn test} (surefire).
 */
final class NegativeVectorsTest {
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final Path NEGATIVE_DIR = Path.of("../../../test-vectors/sdk/negative");

    private static final String SUITE_NAME = "lcp-sdk-negative";
    private static final int SUITE_VERSION = 1;
    private static final Set<String> REQUIRED_KEYS = Set.of(
        "id", "invariant", "spec_ref", "description", "applies_to", "check", "inputs", "expect", "sdk_exercisable");
    // SPEC.md §5 codes each check is allowed to expect on rejection.
    private static final Map<String, Set<String>> CHECK_CODES = Map.of(
        "verify_hmac", Set.of("LCP-012"),
        "verify_raw_body", Set.of("LCP-012"),
        "envelope_validate", Set.of("LCP-006", "LCP-008", "LCP-100"),
        "offer_validate", Set.of("LCP-100"),
        "production_mode_verify", Set.of("LCP-013"));
    // SPEC.md §2/§6 closed message.type enum (validates vector applies_to kinds).
    private static final Set<String> MESSAGE_TYPES = Set.of(
        "lead", "call", "ping", "post", "ack", "event", "bid");

    private record Suite(String file, JsonNode node) {}

    private static List<Suite> loadSuites() throws Exception {
        List<Path> files;
        try (var paths = Files.list(NEGATIVE_DIR)) {
            files = paths.filter(p -> p.toString().endsWith(".json")).sorted().toList();
        }
        if (files.isEmpty()) {
            throw new AssertionError("no vector files found under " + NEGATIVE_DIR.toAbsolutePath());
        }
        var suites = new ArrayList<Suite>();
        for (Path path : files) {
            suites.add(new Suite(path.getFileName().toString(),
                MAPPER.readTree(Files.readString(path, StandardCharsets.UTF_8))));
        }
        return suites;
    }

    private static List<JsonNode> iterVectors(List<Suite> suites) {
        var vectors = new ArrayList<JsonNode>();
        for (Suite suite : suites) {
            for (JsonNode vector : suite.node().path("vectors")) {
                vectors.add(vector);
            }
        }
        return vectors;
    }

    private static String text(JsonNode node, String field) {
        return node.path(field).asText();
    }

    @Test
    void suiteFilesAreWellFormed() throws Exception {
        var problems = new ArrayList<String>();
        var seenIds = new LinkedHashSet<String>();
        for (Suite suite : loadSuites()) {
            String source = suite.file();
            JsonNode node = suite.node();
            if (!SUITE_NAME.equals(node.path("suite").asText(null))) {
                problems.add(source + ": suite must be '" + SUITE_NAME + "'");
            }
            if (node.path("version").asInt(-1) != SUITE_VERSION) {
                problems.add(source + ": unsupported suite version " + node.path("version").asInt(-1));
            }
            for (String field : List.of("group", "description")) {
                if (node.path(field).asText("").isEmpty()) {
                    problems.add(source + ": missing " + field);
                }
            }
            if (!node.path("vectors").isArray() || node.path("vectors").isEmpty()) {
                problems.add(source + ": empty vectors array");
            }
            for (JsonNode vector : node.path("vectors")) {
                String id = vector.path("id").asText("(no id)");
                String where = source + ":" + id;
                for (String key : REQUIRED_KEYS) {
                    if (!vector.has(key)) {
                        problems.add(where + ": missing field '" + key + "'");
                    }
                }
                if (!id.startsWith("lcp-neg-")) {
                    problems.add(where + ": id must start with 'lcp-neg-'");
                }
                if (!seenIds.add(id)) {
                    problems.add(where + ": duplicate vector id");
                }
                JsonNode invariant = vector.path("invariant");
                if (!invariant.isNull() && !(invariant.isIntegralNumber()
                        && !invariant.isBoolean() && invariant.asInt() >= 1 && invariant.asInt() <= 8)) {
                    problems.add(where + ": invariant must be 1-8 or null");
                }
                if (vector.path("spec_ref").asText("").isEmpty()) {
                    problems.add(where + ": spec_ref required (even with invariant set)");
                }
                JsonNode appliesTo = vector.path("applies_to");
                if (!appliesTo.isArray() || appliesTo.isEmpty()) {
                    problems.add(where + ": applies_to must be a non-empty list");
                } else {
                    for (JsonNode kind : appliesTo) {
                        if (!kind.isTextual()) {
                            problems.add(where + ": applies_to kind must be a string");
                            continue;
                        }
                        String value = kind.asText();
                        if (!"*".equals(value) && !MESSAGE_TYPES.contains(value)) {
                            problems.add(where + ": applies_to has unknown kind '" + value + "'");
                        }
                    }
                }
                String check = vector.path("check").asText("");
                if (!CHECK_CODES.containsKey(check)) {
                    problems.add(where + ": unknown check '" + check + "'");
                    continue;
                }
                if (!vector.path("inputs").isObject()) {
                    problems.add(where + ": inputs must be an object");
                }
                JsonNode expect = vector.path("expect");
                String outcome = expect.path("outcome").asText("");
                if (!outcome.equals("reject") && !outcome.equals("accept")) {
                    problems.add(where + ": expect.outcome must be reject|accept");
                } else if (outcome.equals("reject")) {
                    if (!CHECK_CODES.get(check).contains(expect.path("error_code").asText(null))) {
                        problems.add(where + ": reject expects error_code in "
                            + new TreeSet<>(CHECK_CODES.get(check)) + ", got '"
                            + expect.path("error_code").asText(null) + "'");
                    }
                    if (expect.path("error_name").asText("").isEmpty()) {
                        problems.add(where + ": reject requires error_name");
                    }
                } else if (outcome.equals("accept")
                        && (!expect.path("error_code").isNull() || !expect.path("error_name").isNull())) {
                    problems.add(where + ": accept vectors must not declare an error");
                }
                boolean gap = vector.path("sdk_exercisable").asBoolean(true) == false;
                if (gap) {
                    String note = vector.path("gap_note").asText("");
                    if (note.length() < 40) {
                        problems.add(where + ": sdk_exercisable=false requires a substantive gap_note");
                    }
                } else if (vector.has("gap_note")) {
                    problems.add(where + ": gap_note only allowed when sdk_exercisable is false");
                }
            }
        }
        assertEquals(List.of(), problems, "negative vector format violations");
    }

    @Test
    void negativeVectorOutcomes() throws Exception {
        var validator = SchemaValidator.fromDirectory(Path.of("../../../schemas"));
        var mismatches = new ArrayList<String>();
        var gaps = new ArrayList<String>();
        int ran = 0;
        for (JsonNode vector : iterVectors(loadSuites())) {
            String id = text(vector, "id");
            String check = text(vector, "check");
            String[] result = execute(validator, vector);
            String outcome = result[0];
            String detail = result[1];
            if (outcome.equals("gap")) {
                gaps.add(id + ": " + detail);
                continue;
            }
            ran++;
            String expected = vector.path("expect").path("outcome").asText();
            if (!outcome.equals(expected)) {
                mismatches.add(id + " (" + check + "): expected " + expected + ", got " + outcome
                    + " - " + (detail.isEmpty() ? "no detail" : detail) + " - " + text(vector, "description"));
            }
        }
        PrintStream err = System.err;
        for (String gap : gaps) {
            err.println("GAP (reported, never silently passed): " + gap);
        }
        assertTrue(ran > 0, "no negative vector was actually executed");
        assertEquals(List.of(), mismatches, "negative vector outcome mismatches");
    }

    /** Returns {outcome, detail}: outcome is "accept", "reject", "gap", or "error". */
    private static String[] execute(SchemaValidator validator, JsonNode vector) {
        if (!vector.path("sdk_exercisable").asBoolean(true)) {
            return new String[] {"gap", vector.path("gap_note").asText()};
        }
        JsonNode inputs = vector.path("inputs");
        String check = vector.path("check").asText();
        try {
            switch (check) {
                case "verify_hmac", "verify_raw_body" -> {
                    String bodyField = check.equals("verify_hmac") ? "body" : "verify_body";
                    byte[] body = text(inputs, bodyField).getBytes(StandardCharsets.UTF_8);
                    LcpSdk.verifyHmac(
                        text(inputs, "secret"),
                        text(inputs, "signature"),
                        text(inputs, "timestamp"),
                        text(inputs, "idempotency_key"),
                        body,
                        Duration.ofSeconds(inputs.path("replay_window_seconds").asLong()),
                        Instant.parse(text(inputs, "now")));
                }
                case "envelope_validate" -> validator.validateEnvelope(MAPPER.writeValueAsString(inputs.path("envelope")));
                case "offer_validate" -> validator.validateOffer(MAPPER.writeValueAsString(inputs.path("offer")));
                // Exercisable but no adapter in this runner: format lint passed, so the vector
                // expects an SDK surface this lane has not mapped. Fail loudly, never silently pass.
                default -> {
                    return new String[] {"error", "no runner adapter for check '" + check + "'"};
                }
            }
            return new String[] {"accept", ""};
        } catch (Throwable error) {
            return new String[] {"reject", error.getClass().getSimpleName() + ": " + error.getMessage()};
        }
    }
}
