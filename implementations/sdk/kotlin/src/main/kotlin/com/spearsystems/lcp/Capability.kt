package com.spearsystems.lcp

import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper

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
object Capability {
    /** Payload fields introduced at v1.1 that require a v1.1 advertisement. */
    val SLOT_FIELDS = listOf("slots_available", "slots_claimed", "contacted_hashes")

    private const val MIN_SLOT_MINOR = 1
    private val mapper = ObjectMapper()

    /**
     * §8 version-string membership: the gate opens only for a parseable full
     * semver element with major == 1 and minor >= 1. Malformed or partial
     * elements ("1.1", "v1.1.0", "1.1.0.1") never open the gate.
     */
    fun receiverAdvertisesSlotFields(advertisedLcpVersions: List<String>): Boolean = advertisedLcpVersions.any { element ->
        val parts = element.split(".")
        parts.size == 3 &&
            parts.all { it.isNotEmpty() && it.all(Char::isDigit) } &&
            parts[0].toInt() == 1 && parts[1].toInt() >= MIN_SLOT_MINOR
    }

    /** Slot fields carried by an envelope payload (incl. CONTACTED report details). */
    fun slotFieldsPresent(payload: JsonNode?): List<String> {
        if (payload == null || !payload.isObject) return emptyList()
        val present = SLOT_FIELDS.filter(payload::has).toMutableList()
        if (payload.path("event").asText() == "CONTACTED" && payload.path("details").has("contacted_hashes")) present.add("details.contacted_hashes")
        return present
    }

    /**
     * Errors when an envelope carries v1.1 slot fields but the receiver's
     * advertisement does not support them (§8 gate; invalid example D). An
     * empty list is capability-compatibility, not a schema verdict.
     */
    fun slotCapabilityErrors(envelopeJson: String, advertisedLcpVersions: List<String>): List<String> {
        val payload = mapper.readTree(envelopeJson).path("lcp").path("payload")
        val present = slotFieldsPresent(payload)
        if (present.isEmpty() || receiverAdvertisesSlotFields(advertisedLcpVersions)) return emptyList()
        return present.map { field ->
            "$field requires the receiver to advertise a full-semver LCP version with major 1 and minor >= $MIN_SLOT_MINOR (SPEC.md §8 capability gate)"
        }
    }
}
