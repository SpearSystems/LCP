package com.spearsystems.lcp

import java.time.Instant

fun main() {
    val body = "{\"hello\":\"world\"}".toByteArray()
    val signature = LcpSdk.signHmac("sdk-shared-secret", "2026-08-15T10:20:00Z", "sdk-vector-001", body)
    check(signature == "50f90d29b46e92f257eae62c94a3d985bf9a925da03fee82e33c800fe54e7259")
    LcpSdk.verifyHmac("sdk-shared-secret", signature, "2026-08-15T10:20:00Z", "sdk-vector-001", body, now = Instant.parse("2026-08-15T10:20:01Z"))
    println("Kotlin SDK HMAC vector passed")
}
