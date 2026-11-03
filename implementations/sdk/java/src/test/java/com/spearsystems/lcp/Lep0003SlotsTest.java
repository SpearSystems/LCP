package com.spearsystems.lcp;

import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** LEP-0003 SDK lane: contact-hash vectors, slot round-trip, §8 capability gate. */
final class Lep0003SlotsTest {
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final Path ROOT = Path.of("../../../");

    @Test
    void everyPinnedContactHashVectorReproduces() throws Exception {
        var suite = MAPPER.readTree(Files.readString(ROOT.resolve("test-vectors/sdk/contact-hash.json")));
        var failures = new ArrayList<String>();
        int ran = 0;
        for (var vector : suite.get("vectors")) {
            ran++;
            var observed = LcpSdk.contactHash(
                vector.get("secret").asText(),
                vector.get("preimage").asText(),
                vector.get("domain").asText());
            var pinned = vector.get("digest").asText();
            if (!observed.equals(pinned)) {
                failures.add(vector.get("name").asText() + " (" + vector.get("domain").asText() + "): preimage \""
                    + vector.get("preimage").asText() + "\" pinned " + pinned + " but Java SDK produced " + observed);
            }
        }
        assertEquals(2, ran, "contact-hash.json must expose its pinned rows");
        assertEquals(List.of(), failures, "contact-hash vector reproduction failed");
    }

    @Test
    void preimageRulesMatchSpecSectionNine() {
        // Phone: verbatim wire bytes — formatting noise must change the digest.
        assertNotEquals("c47aff331606437528b34b7f919eb37704e87ee35eed80e52cfa36826a4a2e12",
            LcpSdk.contactHash("pair-secret-for-vectors-0123456789", "+1 (512) 555-0147", "phone"));
        String pinnedEmail = "d28da9b5424990cf7ac6000cf4e04537ca487929e71cef12f91191f29f78a6c4";
        assertEquals(pinnedEmail, LcpSdk.contactHash("pair-secret-for-vectors-0123456789", "  Jordan@Example.COM  ", "email"));
        assertNotEquals(pinnedEmail, LcpSdk.contactHash("pair-secret-for-vectors-0123456789", "jordan@example.com", "email"));
        assertNotEquals(pinnedEmail, LcpSdk.contactHash("pair-secret-for-vectors-0123456789", "Jordan+crm@example.com", "email"));
    }

    @Test
    void versionStringMembershipParsesFullSemver() {
        assertFalse(Capability.receiverAdvertisesSlotFields(List.of("1.0.0")));
        assertTrue(Capability.receiverAdvertisesSlotFields(List.of("1.1.0")));
        assertTrue(Capability.receiverAdvertisesSlotFields(List.of("1.0.0", "1.1.0")));
        assertTrue(Capability.receiverAdvertisesSlotFields(List.of("1.10.0")));
        assertFalse(Capability.receiverAdvertisesSlotFields(List.of("2.0.0")));
        assertFalse(Capability.receiverAdvertisesSlotFields(List.of("1.1")));
        assertFalse(Capability.receiverAdvertisesSlotFields(List.of("v1.1.0", "1.1.0.1", "")));
    }

    @Test
    void slotRoundTripAndCapabilityGate() throws Exception {
        var validator = SchemaValidator.fromDirectory(ROOT.resolve("schemas"));
        var ping = Files.readString(ROOT.resolve("examples/ping-slots.json"));
        var bid = Files.readString(ROOT.resolve("examples/bid-slots.json"));
        var post = Files.readString(ROOT.resolve("examples/post-contacted.json"));
        var contacted = Files.readString(ROOT.resolve("examples/event-contacted.json"));

        for (var envelope : List.of(ping, bid, post, contacted)) validator.validateEnvelope(envelope);

        for (var envelope : List.of(ping, bid, post, contacted)) {
            var error = assertThrows(Exception.class,
                () -> validator.validateEnvelope(envelope, List.of("1.0.0")),
                "slot-bearing envelope must be rejected for a v1.0-only receiver");
            assertTrue(error.getMessage().contains("capability gate"), error.getMessage());
        }
        for (var envelope : List.of(ping, bid, post, contacted)) validator.validateEnvelope(envelope, List.of("1.0.0", "1.1.0"));
    }
}
