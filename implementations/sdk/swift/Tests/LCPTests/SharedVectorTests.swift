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

    func testSharedValidationCorpus() throws {
        guard let root = ProcessInfo.processInfo.environment["LCP_REPO_ROOT"] else {
            throw XCTSkip("LCP_REPO_ROOT is required for the repository corpus fixture")
        }
        let rootURL = URL(fileURLWithPath: root)
        let validator = try LCPValidator.fromDirectory(rootURL.appendingPathComponent("schemas"))
        let corpusData = try String(contentsOf: rootURL.appendingPathComponent("test-vectors/sdk/validation-corpus.json"), encoding: .utf8)
        let corpus = try JSONSerialization.jsonObject(with: Data(corpusData.utf8)) as! [String: Any]
        let fixtures = corpus["fixtures"] as! [[String: Any]]
        var mismatches: [String] = []
        for fixture in fixtures {
            let id = fixture["id"] as! String
            let rule = fixture["rule"] as! String
            let expectPass = (fixture["expect"] as! String) == "pass"
            let isOffer = fixture["offer"] != nil
            let document: Any = isOffer ? fixture["offer"]! : fixture["envelope"]!
            let raw = try JSONSerialization.data(withJSONObject: document)
            let jsonString = String(data: raw, encoding: .utf8)!
            var passed = true
            do {
                if isOffer {
                    try validator.validateOffer(jsonString)
                } else {
                    try validator.validateEnvelope(jsonString)
                }
            } catch {
                passed = false
            }
            if passed != expectPass {
                mismatches.append("\(id) (\(rule)): expected pass=\(expectPass)")
            }
        }
        XCTAssertTrue(mismatches.isEmpty, "Shared validation corpus mismatches: \(mismatches.joined(separator: "; "))")
    }
}
