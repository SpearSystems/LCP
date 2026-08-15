import XCTest
import Foundation
@testable import LCP

final class SharedVectorTests: XCTestCase {
    func testSharedHMACVector() throws {
        let body = Data("{\"hello\":\"world\"}".utf8)
        let signature = LCPSigning.sign(secret: "sdk-shared-secret", timestamp: "2026-08-15T10:20:00Z", idempotencyKey: "sdk-vector-001", body: body)
        XCTAssertEqual(signature, "50f90d29b46e92f257eae62c94a3d985bf9a925da03fee82e33c800fe54e7259")
        try LCPSigning.verify(secret: "sdk-shared-secret", signature: signature, timestamp: "2026-08-15T10:20:00Z", idempotencyKey: "sdk-vector-001", body: body, now: ISO8601DateFormatter().date(from: "2026-08-15T10:20:01Z")!)
    }

    func testFullSchemaVector() throws {
        guard let root = ProcessInfo.processInfo.environment["LCP_REPO_ROOT"] else {
            throw XCTSkip("LCP_REPO_ROOT is required for the repository schema fixture")
        }
        let rootURL = URL(fileURLWithPath: root)
        let validator = try LCPValidator.fromDirectory(rootURL.appendingPathComponent("schemas"))
        try validator.validateEnvelope(String(contentsOf: rootURL.appendingPathComponent("examples/lead.json"), encoding: .utf8))
    }
}
