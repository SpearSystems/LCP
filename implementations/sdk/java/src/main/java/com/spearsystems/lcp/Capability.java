package com.spearsystems.lcp;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.util.ArrayList;
import java.util.List;

/**
 * Receiver capability helpers for v1.1 slot fields (SPEC.md §8, LEP-0003).
 *
 * The ping/bid/post payload objects are closed (additionalProperties: false),
 * so a schema-validating v1.0 receiver REJECTS slot fields rather than
 * ignoring them. Senders MUST only include them toward a receiver whose §8
 * lcp_versions advertisement parses to a full major.minor.patch element with
 * major == 1 and minor >= 1 — compared as parsed versions, never as a string
 * literal or prefix.
 */
public final class Capability {
    private Capability() {}

    /** Payload fields introduced at v1.1 that require a v1.1 advertisement. */
    public static final List<String> SLOT_FIELDS = List.of("slots_available", "slots_claimed", "contacted_hashes");

    private static final int MIN_SLOT_MINOR = 1;
    private static final ObjectMapper MAPPER = new ObjectMapper();

    /**
     * §8 version-string membership: the gate opens only for a parseable full
     * semver element with major == 1 and minor >= 1. Malformed or partial
     * elements ("1.1", "v1.1.0", "1.1.0.1") never open the gate.
     */
    public static boolean receiverAdvertisesSlotFields(List<String> advertisedLcpVersions) {
        for (String element : advertisedLcpVersions) {
            String[] parts = element.split("\\.");
            if (parts.length != 3) continue;
            try {
                for (String part : parts) if (part.isEmpty() || !part.chars().allMatch(Character::isDigit)) throw new NumberFormatException();
                if (Integer.parseInt(parts[0]) == 1 && Integer.parseInt(parts[1]) >= MIN_SLOT_MINOR) return true;
            } catch (NumberFormatException ignored) {
                // Not a full semver element — it never opens the gate.
            }
        }
        return false;
    }

    /** Slot fields carried by an envelope payload (incl. CONTACTED report details). */
    public static List<String> slotFieldsPresent(JsonNode payload) {
        List<String> present = new ArrayList<>();
        if (payload == null || !payload.isObject()) return present;
        for (String field : SLOT_FIELDS) if (payload.has(field)) present.add(field);
        if ("CONTACTED".equals(payload.path("event").asText()) && payload.path("details").has("contacted_hashes")) present.add("details.contacted_hashes");
        return present;
    }

    /**
     * Errors when an envelope carries v1.1 slot fields but the receiver's
     * advertisement does not support them (§8 gate; invalid example D). An
     * empty list is capability-compatibility, not a schema verdict.
     */
    public static List<String> slotCapabilityErrors(String envelopeJson, List<String> advertisedLcpVersions) {
        JsonNode payload = MAPPER.readTree(envelopeJson).path("lcp").path("payload");
        List<String> present = slotFieldsPresent(payload);
        List<String> problems = new ArrayList<>();
        if (present.isEmpty() || receiverAdvertisesSlotFields(advertisedLcpVersions)) return problems;
        for (String field : present) {
            problems.add(field + " requires the receiver to advertise a full-semver LCP version with major 1 and minor >= " + MIN_SLOT_MINOR + " (SPEC.md §8 capability gate)");
        }
        return problems;
    }
}
