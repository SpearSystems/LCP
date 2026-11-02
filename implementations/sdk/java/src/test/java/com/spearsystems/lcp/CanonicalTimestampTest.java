package com.spearsystems.lcp;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;

import org.junit.jupiter.api.Test;

/**
 * LEP-0002: the Java SDK's timestamp generation MUST emit the canonical
 * whole-second UTC wire form ({@code YYYY-MM-DDTHH:MM:SSZ}) — never the
 * fractional ISO form {@code Instant.toString()} would keep for a non-zero
 * nano field. Deterministic: frozen clock, no network.
 */
final class CanonicalTimestampTest {
    @Test
    void formatsFrozenNanosecondInstantAsWholeSecondUtc() {
        var frozen = Instant.parse("2026-08-15T10:20:00.123456789Z");
        var clock = Clock.fixed(frozen, ZoneOffset.UTC);
        String emitted = LcpSdk.formatTimestamp(Instant.now(clock));
        assertEquals("2026-08-15T10:20:00Z", emitted);
    }

    @Test
    void formatterTruncatesSubsecondNoiseToCanonicalWireShape() {
        assertEquals(
            "2026-08-15T10:20:00Z",
            LcpSdk.CANONICAL_TIMESTAMP_FORMAT.format(Instant.parse("2026-08-15T10:20:00.123456789Z"))
        );
    }
}
