package com.spearsystems.lcp

import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/** LEP-0003 SDK lane: contact-hash vectors, slot round-trip, §8 capability gate. */
class Lep0003SlotsTest {
    private val root = Path.of("../../../")

    @Test
    fun everyPinnedContactHashVectorReproduces() {
        val suite = tools.jackson.databind.ObjectMapper().readTree(root.resolve("test-vectors/sdk/contact-hash.json").toFile())
        val vectors = suite.get("vectors").toList()
        assertEquals(2, vectors.size, "contact-hash.json must expose its pinned rows")
        val failures = vectors.mapNotNull { vector ->
            val observed = LcpSdk.contactHash(
                vector.get("secret").asText(),
                vector.get("preimage").asText(),
                vector.get("domain").asText(),
            )
            val pinned = vector.get("digest").asText()
            if (observed == pinned) null else
                "${vector.get("name").asText()} (${vector.get("domain").asText()}): preimage \"${vector.get("preimage").asText()}\" pinned $pinned but Kotlin SDK produced $observed"
        }
        assertEquals(emptyList(), failures, "contact-hash vector reproduction failed")
    }

    @Test
    fun preimageRulesMatchSpecSectionNine() {
        // Phone: verbatim wire bytes — formatting noise must change the digest.
        assertNotEquals(
            "c47aff331606437528b34b7f919eb37704e87ee35eed80e52cfa36826a4a2e12",
            LcpSdk.contactHash("pair-secret-for-vectors-0123456789", "+1 (512) 555-0147"),
        )
        val pinnedEmail = "d28da9b5424990cf7ac6000cf4e04537ca487929e71cef12f91191f29f78a6c4"
        assertEquals(pinnedEmail, LcpSdk.contactHash("pair-secret-for-vectors-0123456789", "  Jordan@Example.COM  ", "email"))
        assertNotEquals(pinnedEmail, LcpSdk.contactHash("pair-secret-for-vectors-0123456789", "jordan@example.com", "email"))
        assertNotEquals(pinnedEmail, LcpSdk.contactHash("pair-secret-for-vectors-0123456789", "Jordan+crm@example.com", "email"))
    }

    @Test
    fun versionStringMembershipParsesFullSemver() {
        assertFalse(Capability.receiverAdvertisesSlotFields(listOf("1.0.0")))
        assertTrue(Capability.receiverAdvertisesSlotFields(listOf("1.1.0")))
        assertTrue(Capability.receiverAdvertisesSlotFields(listOf("1.0.0", "1.1.0")))
        assertTrue(Capability.receiverAdvertisesSlotFields(listOf("1.10.0")))
        assertFalse(Capability.receiverAdvertisesSlotFields(listOf("2.0.0")))
        assertFalse(Capability.receiverAdvertisesSlotFields(listOf("1.1")))
        assertFalse(Capability.receiverAdvertisesSlotFields(listOf("v1.1.0", "1.1.0.1", "")))
    }

    @Test
    fun slotRoundTripAndCapabilityGate() {
        val validator = LcpSchemaValidator.fromDirectory(root.resolve("schemas"))
        val envelopes = listOf("ping-slots", "bid-slots", "post-contacted", "event-contacted")
            .map { name -> name to root.resolve("examples/$name.json").toFile().readText() }

        envelopes.forEach { (_, envelope) -> validator.validateEnvelope(envelope) }
        envelopes.forEach { (name, envelope) ->
            val error = assertFailsWith<IllegalArgumentException> {
                validator.validateEnvelope(envelope, listOf("1.0.0"))
            }
            assertTrue((error.message ?: "").contains("capability gate"), "slot-bearing $name must be rejected for a v1.0-only receiver: ${error.message}")
        }
        envelopes.forEach { (_, envelope) -> validator.validateEnvelope(envelope, listOf("1.0.0", "1.1.0")) }
    }
}
