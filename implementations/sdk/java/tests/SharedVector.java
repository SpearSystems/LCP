package com.spearsystems.lcp;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;

public final class SharedVector {
    public static void main(String[] args) {
        var body = "{\"hello\":\"world\"}".getBytes(StandardCharsets.UTF_8);
        var signature = LcpSdk.signHmac("sdk-shared-secret", "2026-08-15T10:20:00Z", "sdk-vector-001", body);
        var expected = "50f90d29b46e92f257eae62c94a3d985bf9a925da03fee82e33c800fe54e7259";
        if (!expected.equals(signature)) throw new AssertionError(signature);
        LcpSdk.verifyHmac("sdk-shared-secret", signature, "2026-08-15T10:20:00Z", "sdk-vector-001", body, Duration.ofMinutes(5), Instant.parse("2026-08-15T10:20:01Z"));
        System.out.println("Java SDK HMAC vector passed");
    }
}
