import Foundation
import CryptoKit
import XCTest

@testable import LCP

/// Swift SDK interop probe for tools/sdk_interop_matrix.py (LEP-0002 step 1).
///
/// Signs the fixed vector message through the SDK's own LCPSigning
/// canonicalBytes + sign, then formats the SDK's generation expression
/// (Lcp.swift:16 `ISO8601DateFormatter().string(from: Date())`) at a frozen
/// sub-second instant — the default formatter has no fractional option, so
/// Swift is compliant today. The probe never asserts; the matrix owns the
/// checks. Emits the `LCP_INTEROP {json}` line on stderr and writes
/// LCP_INTEROP_OUT so the workflow can collect it without scraping test logs.
final class InteropProbeTests: XCTestCase {
    func repoRoot() throws -> URL {
        if let fromEnv = ProcessInfo.processInfo.environment["LCP_REPO_ROOT"], !fromEnv.isEmpty {
            return URL(fileURLWithPath: fromEnv)
        }
        var directory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        while directory.path != "/" {
            if FileManager.default.fileExists(atPath: directory.appendingPathComponent("test-vectors/sdk/hmac.json").path) {
                return directory
            }
            directory.deleteLastPathComponent()
        }
        throw XCTSkip("hmac.json vector not found; set LCP_REPO_ROOT")
    }

    func testEmitInteropProbe() throws {
        let vectorURL = repoRoot().appendingPathComponent("test-vectors/sdk/hmac.json")
        guard let raw = FileManager.default.contents(atPath: vectorURL.path),
              let vector = try JSONSerialization.jsonObject(with: raw) as? [String: Any]
        else { throw XCTSkip("cannot read \(vectorURL.path)") }
        func field(_ name: String) throws -> String {
            guard let value = vector[name] as? String else { throw XCTSkip("vector field \(name) missing") }
            return value
        }
        let timestamp = try field("timestamp")
        let idempotencyKey = try field("idempotency_key")
        let secret = try field("secret")
        let body = Data(try field("body_utf8").utf8)

        let canonical = LCPSigning.canonicalBytes(timestamp: timestamp, idempotencyKey: idempotencyKey, body: body)
        // Frozen instant 2026-08-15T10:20:00.123456789Z through the SDK's own
        // generation expression (default internet-date format, no fraction).
        let frozen = Date(timeIntervalSince1970: 1_786_789_200.123456789)
        let emitted = ISO8601DateFormatter().string(from: frozen)

        let result: [String: String] = [
            "canonical_sha256": Data(SHA256.hash(data: canonical)).map { String(format: "%02x", $0) }.joined(),
            "emitted_timestamp": emitted,
            "sdk": "swift",
            "signature_hex": LCPSigning.sign(secret: secret, timestamp: timestamp, idempotencyKey: idempotencyKey, body: body),
        ]
        let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
        let json = String(decoding: data, as: UTF8.self)
        // Logging hygiene: no payload prints on stdout; stderr + LCP_INTEROP_OUT.
        FileHandle.standardError.write(Data("LCP_INTEROP \(json)\n".utf8))
        if let out = ProcessInfo.processInfo.environment["LCP_INTEROP_OUT"], !out.isEmpty {
            try (json + "\n").write(toFile: out, atomically: true, encoding: .utf8)
        }
    }
}
