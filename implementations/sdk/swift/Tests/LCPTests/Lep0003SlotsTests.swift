import Foundation
import XCTest

@testable import LCP

/// LEP-0003 SDK lane: contact-hash vectors, slot round-trip, §8 capability gate.
final class Lep0003SlotsTests: XCTestCase {
    func repoRoot() throws -> URL {
        if let fromEnv = ProcessInfo.processInfo.environment["LCP_REPO_ROOT"], !fromEnv.isEmpty {
            return URL(fileURLWithPath: fromEnv)
        }
        var directory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        while directory.path != "/" {
            if FileManager.default.fileExists(atPath: directory.appendingPathComponent("test-vectors/sdk/contact-hash.json").path) {
                return directory
            }
            directory.deleteLastPathComponent()
        }
        throw XCTSkip("contact-hash.json vector not found; set LCP_REPO_ROOT")
    }

    func readJSON(_ relative: String) throws -> Any {
        let url = try repoRoot().appendingPathComponent(relative)
        guard let raw = FileManager.default.contents(atPath: url.path) else { throw XCTSkip("cannot read \(relative)") }
        return try JSONSerialization.jsonObject(with: raw)
    }

    /// Executes every pinned preimage→digest row (#153 T9 run). A row that
    /// cannot be reproduced is an interop failure, reported with the exact
    /// preimage and the observed digest.
    func testEveryPinnedContactHashVectorReproduces() throws {
        guard let suite = try readJSON("test-vectors/sdk/contact-hash.json") as? [String: Any],
              let vectors = suite["vectors"] as? [[String: Any]], !vectors.isEmpty
        else { return XCTFail("contact-hash.json exposed no vectors") }
        var mismatches: [String] = []
        for vector in vectors {
            let name = vector["name"] as? String ?? "?"
            let domain = vector["domain"] as? String ?? "?"
            let preimage = vector["preimage"] as? String ?? ""
            let pinned = vector["digest"] as? String ?? ""
            guard let observed = try? LCPSigning.contactHash(secret: vector["secret"] as? String ?? "", value: preimage, domain: domain) else {
                mismatches.append("\(name): SDK cannot compute a digest for domain \(domain)")
                continue
            }
            if observed != pinned {
                mismatches.append("\(name) (\(domain)): preimage \"\(preimage)\" pinned \(pinned) but Swift SDK produced \(observed)")
            }
        }
        XCTAssertTrue(mismatches.isEmpty, "contact-hash vector reproduction failed: \(mismatches)")
    }

    func testPreimageRulesMatchSpecSectionNine() throws {
        // Phone: verbatim wire bytes — formatting noise must change the digest.
        let phoneDigest = try LCPSigning.contactHash(secret: "pair-secret-for-vectors-0123456789", value: "+1 (512) 555-0147")
        XCTAssertNotEqual(phoneDigest, "c47aff331606437528b34b7f919eb37704e87ee35eed80e52cfa36826a4a2e12")
        let pinnedEmail = "d28da9b5424990cf7ac6000cf4e04537ca487929e71cef12f91191f29f78a6c4"
        XCTAssertEqual(try LCPSigning.contactHash(secret: "pair-secret-for-vectors-0123456789", value: "  Jordan@Example.COM  ", domain: "email"), pinnedEmail)
        XCTAssertNotEqual(try LCPSigning.contactHash(secret: "pair-secret-for-vectors-0123456789", value: "jordan@example.com", domain: "email"), pinnedEmail)
        XCTAssertNotEqual(try LCPSigning.contactHash(secret: "pair-secret-for-vectors-0123456789", value: "Jordan+crm@example.com", domain: "email"), pinnedEmail)
    }

    func testVersionStringMembershipParsesFullSemver() {
        XCTAssertFalse(LCPCapability.receiverAdvertisesSlotFields(["1.0.0"]))
        XCTAssertTrue(LCPCapability.receiverAdvertisesSlotFields(["1.1.0"]))
        XCTAssertTrue(LCPCapability.receiverAdvertisesSlotFields(["1.0.0", "1.1.0"]))
        XCTAssertTrue(LCPCapability.receiverAdvertisesSlotFields(["1.10.0"])) // parsed minor 10 >= 1
        XCTAssertFalse(LCPCapability.receiverAdvertisesSlotFields(["2.0.0"])) // major must be 1
        XCTAssertFalse(LCPCapability.receiverAdvertisesSlotFields(["1.1"]))   // literal never opens the gate
        XCTAssertFalse(LCPCapability.receiverAdvertisesSlotFields(["v1.1.0", "1.1.0.1", ""]))
    }

    func testSlotRoundTripAndCapabilityGate() throws {
        let validator = try LCPValidator.fromDirectory(try repoRoot().appendingPathComponent("schemas"))
        var documents: [(String, [String: Any])] = []
        for name in ["ping-slots", "bid-slots", "post-contacted", "event-contacted"] {
            guard let envelope = try readJSON("examples/\(name).json") as? [String: Any] else { return XCTFail("cannot read \(name)") }
            documents.append((name, envelope))
        }
        let serialized = try documents.map { (name, envelope) -> (String, String) in
            (name, String(data: try JSONSerialization.data(withJSONObject: envelope), encoding: .utf8)!)
        }

        // Slot-bearing ping/bid/post/event validate through the SDK schema path.
        for (name, envelope) in serialized {
            do { try validator.validateEnvelope(envelope) } catch { XCTFail("slot-bearing \(name) failed validation: \(error)") }
        }
        // The capability gate rejects them toward a v1.0-only receiver…
        for (name, envelope) in serialized {
            XCTAssertThrowsError(
                try validator.validateEnvelope(envelope, receiverAdvertisedLcpVersions: ["1.0.0"]),
                "slot-bearing \(name) must be rejected for a v1.0-only receiver"
            ) { error in
                XCTAssertTrue("\(error)".contains("capability") || "\(error)".contains("advertise"), "\(error)")
            }
        }
        // …and accepts them toward a v1.1 advertisement.
        for (name, envelope) in serialized {
            do { try validator.validateEnvelope(envelope, receiverAdvertisedLcpVersions: ["1.0.0", "1.1.0"]) } catch { XCTFail("slot-bearing \(name) must validate for a v1.1 receiver: \(error)") }
        }
    }
}
