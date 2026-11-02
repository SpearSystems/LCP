package com.spearsystems.lcp

import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * LEP-0002: the Kotlin SDK's timestamp generation MUST emit the canonical
 * whole-second UTC wire form (`YYYY-MM-DDTHH:MM:SSZ`) — never the fractional
 * ISO form `Instant.toString()` would keep for a non-zero nano field.
 * Deterministic: frozen clock, no network.
 */
class CanonicalTimestampTest {
    @Test
    fun formatsFrozenNanosecondInstantAsWholeSecondUtc() {
        val frozen = Instant.parse("2026-08-15T10:20:00.123456789Z")
        val clock = Clock.fixed(frozen, ZoneOffset.UTC)
        val emitted = LcpSdk.formatTimestamp(Instant.now(clock))
        assertEquals("2026-08-15T10:20:00Z", emitted)
    }

    @Test
    fun formatterTruncatesSubsecondNoiseToCanonicalWireShape() {
        assertEquals(
            "2026-08-15T10:20:00Z",
            LcpSdk.CANONICAL_TIMESTAMP_FORMAT.format(Instant.parse("2026-08-15T10:20:00.123456789Z"))
        )
    }
}
