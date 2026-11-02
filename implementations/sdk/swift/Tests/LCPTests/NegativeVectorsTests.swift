import Foundation
import CoreFoundation
import XCTest
import LCP

/// Swift SDK must refuse every attack in `test-vectors/sdk/negative/`.
///
/// Python reference: `implementations/sdk/python/tests/test_negative_vectors.py`;
/// Rust sibling: `implementations/sdk/rust/tests/negative_vectors.rs`. Format
/// contract: `test-vectors/sdk/negative/README.md`. Deterministic: the suite's
/// fixed `inputs.now` clocks every verify, schemas load from the repo tree, no
/// network. The format lint runs before any behavior assertion so a malformed
/// vector fails CI even when behavior would accidentally match.
///
/// Outcome-only asserts per the batch-A contract: `LCPSigning.verify` and
/// `LCPValidator` surface bare throws (NSError/LCPValidationError), not
/// protocol error codes, so the runner never asserts `expect.error_code`.
///
/// CI discovery: none needed — `.github/workflows/sdk.yml` runs `swift test`
/// for the `LCPTests` target with `LCP_REPO_ROOT=$GITHUB_WORKSPACE`, and
/// XCTest auto-discovers this file the same way it discovered
/// InteropProbeTests.swift (#139).
final class NegativeVectorsTests: XCTestCase {
    private static let suiteName = "lcp-sdk-negative"
    private static let suiteVersion = 1
    private static let messageTypes: Set<String> = ["lead", "call", "ping", "post", "ack", "event", "bid"]
    private static let requiredKeys = ["id", "invariant", "spec_ref", "description", "applies_to", "check", "inputs", "expect", "sdk_exercisable"]
    /// SPEC.md §5 codes each check is allowed to expect on rejection.
    private static let checkCodes: [String: Set<String>] = [
        "verify_hmac": ["LCP-012"],
        "verify_raw_body": ["LCP-012"],
        "envelope_validate": ["LCP-006", "LCP-008", "LCP-100"],
        "offer_validate": ["LCP-100"],
        "production_mode_verify": ["LCP-013"],
    ]

    // MARK: - Fixture discovery (InteropProbeTests pattern, #139)

    private func repoRoot() throws -> URL {
        let marker = "test-vectors/sdk/negative/signature.json"
        if let fromEnv = ProcessInfo.processInfo.environment["LCP_REPO_ROOT"], !fromEnv.isEmpty {
            return URL(fileURLWithPath: fromEnv)
        }
        var directory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        while directory.path != "/" {
            if FileManager.default.fileExists(atPath: directory.appendingPathComponent(marker).path) {
                return directory
            }
            directory.deleteLastPathComponent()
        }
        throw XCTSkip("negative vector suite not found; set LCP_REPO_ROOT")
    }

    private func loadSuites() throws -> [(file: String, suite: [String: Any])] {
        let dir = try repoRoot().appendingPathComponent("test-vectors/sdk/negative")
        let contents = try FileManager.default.contentsOfDirectory(atPath: dir.path)
        let files = contents.filter { $0.hasSuffix(".json") }.sorted()
        XCTAssertFalse(files.isEmpty, "no vector files found under \(dir.path)")
        return try files.map { name in
            let raw = try Data(contentsOf: dir.appendingPathComponent(name))
            guard let suite = try JSONSerialization.jsonObject(with: raw) as? [String: Any] else {
                XCTFail("\(name): top level must be a JSON object"); return (name, [:])
            }
            return (name, suite)
        }
    }

    // MARK: - JSON helpers

    private static func str(_ object: [String: Any], _ key: String) -> String? { object[key] as? String }
    private static func isBoolean(_ value: Any?) -> Bool {
        guard let number = value as? NSNumber else { return false }
        return CFGetTypeID(number as CFTypeRef) == CFBooleanGetTypeID()
    }
    private static func intIfNotBoolean(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber, !isBoolean(number), number.doubleValue == number.doubleValue.rounded() else { return nil }
        return number.intValue
    }

    // MARK: - Format lint (mirrors the Python linter exactly)

    private func lintProblems(_ suites: [(file: String, suite: [String: Any])]) -> [String] {
        var problems: [String] = []
        var seenIDs = Set<String>()
        for (source, suite) in suites {
            if Self.str(suite, "suite") != Self.suiteName {
                problems.append("\(source): suite must be '\(Self.suiteName)'")
            }
            if Self.intIfNotBoolean(suite["version"]) != Self.suiteVersion {
                problems.append("\(source): unsupported suite version \(suite["version"] ?? "nil")")
            }
            for field in ["group", "description"] where Self.str(suite, field)?.isEmpty ?? true {
                problems.append("\(source): missing \(field)")
            }
            guard let vectors = suite["vectors"] as? [[String: Any]], !vectors.isEmpty else {
                problems.append("\(source): empty vectors array")
                continue
            }
            for vector in vectors {
                let vid = Self.str(vector, "id") ?? "(no id)"
                let site = "\(source):\(vid)"
                for key in Self.requiredKeys where vector[key] == nil {
                    problems.append("\(site): missing field '\(key)'")
                }
                if !vid.hasPrefix("lcp-neg-") { problems.append("\(site): id must start with 'lcp-neg-'") }
                if seenIDs.contains(vid) { problems.append("\(site): duplicate vector id") }
                seenIDs.insert(vid)
                if let invariant = vector["invariant"], !(invariant is NSNull) {
                    guard let level = Self.intIfNotBoolean(invariant), (1...8).contains(level) else {
                        problems.append("\(site): invariant must be 1-8 or null")
                        continue
                    }
                }
                if Self.str(vector, "spec_ref")?.isEmpty ?? true {
                    problems.append("\(site): spec_ref required (even with invariant set)")
                }
                let appliesTo = vector["applies_to"] as? [String]
                if appliesTo?.isEmpty ?? true {
                    problems.append("\(site): applies_to must be a non-empty list")
                } else if appliesTo?.contains(where: { $0 != "*" && !Self.messageTypes.contains($0) }) ?? false {
                    problems.append("\(site): applies_to has unknown kind \(String(describing: vector["applies_to"]))")
                }
                guard let check = Self.str(vector, "check"), Self.checkCodes[check] != nil else {
                    problems.append("\(site): unknown check \(String(describing: vector["check"]))")
                    continue
                }
                if vector["inputs"] as? [String: Any] == nil {
                    problems.append("\(site): inputs must be an object")
                }
                let expect = vector["expect"] as? [String: Any] ?? [:]
                let outcome = Self.str(expect, "outcome")
                switch outcome {
                case "reject":
                    let allowed = Self.checkCodes[check] ?? []
                    let code = Self.str(expect, "error_code")
                    if code == nil || !allowed.contains(code!) {
                        problems.append("\(site): reject expects error_code in \(allowed.sorted()), got \(String(describing: code))")
                    }
                    if Self.str(expect, "error_name")?.isEmpty ?? true {
                        problems.append("\(site): reject requires error_name")
                    }
                case "accept":
                    for field in ["error_code", "error_name"] where expect[field] != nil && !(expect[field] is NSNull) {
                        problems.append("\(site): accept vectors must not declare an error (\(field))")
                    }
                default:
                    problems.append("\(site): expect.outcome must be reject|accept")
                }
                if Self.isBoolean(vector["sdk_exercisable"]) && (vector["sdk_exercisable"] as? Bool) == false {
                    let note = Self.str(vector, "gap_note") ?? ""
                    if note.count < 40 {
                        problems.append("\(site): sdk_exercisable=false requires a substantive gap_note")
                    }
                } else if vector["gap_note"] != nil {
                    problems.append("\(site): gap_note only allowed when sdk_exercisable is false")
                }
            }
        }
        return problems
    }

    // MARK: - Dispatch against the real SDK primitives

    /// Returns ("reject"|"accept"|"gap"|"error", detail).
    private func execute(_ vector: [String: Any], validator: LCPValidator, clock: ISO8601DateFormatter) -> (String, String) {
        if (vector["sdk_exercisable"] as? Bool) == false {
            return ("gap", Self.str(vector, "gap_note") ?? "gap_note missing (lint should have caught this)")
        }
        let check = Self.str(vector, "check") ?? ""
        guard let inputs = vector["inputs"] as? [String: Any] else {
            return ("error", "inputs object missing")
        }
        switch check {
        case "verify_hmac", "verify_raw_body":
            let bodyKey = check == "verify_hmac" ? "body" : "verify_body"
            guard
                let secret = Self.str(inputs, "secret"),
                let signature = Self.str(inputs, "signature"),
                let timestamp = Self.str(inputs, "timestamp"),
                let bodyString = Self.str(inputs, bodyKey),
                let nowString = Self.str(inputs, "now"),
                let now = clock.date(from: nowString),
                let skew = Self.intIfNotBoolean(inputs["replay_window_seconds"])
            else { return ("error", "verify inputs malformed: \(inputs)") }
            do {
                try LCPSigning.verify(
                    secret: secret,
                    signature: signature,
                    timestamp: timestamp,
                    idempotencyKey: Self.str(inputs, "idempotency_key"),
                    body: Data(bodyString.utf8),
                    maxSkew: TimeInterval(skew),
                    now: now
                )
                return ("accept", "")
            } catch {
                return ("reject", String(describing: error))
            }
        case "envelope_validate":
            guard let envelope = inputs["envelope"] else { return ("error", "envelope input missing") }
            let raw = try! JSONSerialization.data(withJSONObject: envelope)
            do {
                try validator.validateEnvelope(String(decoding: raw, as: UTF8.self))
                return ("accept", "")
            } catch {
                return ("reject", String(describing: error))
            }
        case "offer_validate":
            guard let offer = inputs["offer"] else { return ("error", "offer input missing") }
            let raw = try! JSONSerialization.data(withJSONObject: offer)
            do {
                try validator.validateOffer(String(decoding: raw, as: UTF8.self))
                return ("accept", "")
            } catch {
                return ("reject", String(describing: error))
            }
        default:
            // Exercisable but no adapter in this runner: fail loudly, never skip.
            return ("error", "no runner adapter for check '\(check)'")
        }
    }

    // MARK: - Tests

    func testSharedNegativeSuiteFormat() throws {
        let problems = lintProblems(try loadSuites())
        XCTAssertEqual([], problems, "negative vector suite format violations")
    }

    func testNegativeVectorOutcomes() throws {
        let suites = try loadSuites()
        XCTAssertEqual([], lintProblems(suites), "negative vector suite format violations")
        let validator = try LCPValidator.fromDirectory(try repoRoot().appendingPathComponent("schemas"))
        let clock = ISO8601DateFormatter()
        var mismatches: [String] = []
        var ran = 0
        var gaps = 0
        for (_, suite) in suites {
            for vector in (suite["vectors"] as? [[String: Any]]) ?? [] {
                let id = Self.str(vector, "id") ?? "(no id)"
                let (outcome, detail) = execute(vector, validator: validator, clock: clock)
                if outcome == "gap" {
                    gaps += 1
                    // Logging hygiene (invariant 7): gap notes carry no PII.
                    FileHandle.standardError.write(Data("GAP (reported, never silently passed): \(id): \(detail)\n".utf8))
                    continue
                }
                ran += 1
                let expected = Self.str((vector["expect"] as? [String: Any]) ?? [:], "outcome") ?? "?"
                if outcome != expected {
                    let description = Self.str(vector, "description") ?? ""
                    let check = Self.str(vector, "check") ?? "?"
                    mismatches.append("\(id) (\(check)): expected \(expected), got \(outcome) - \(detail) - \(description)")
                }
            }
        }
        XCTAssertGreaterThan(ran, 0, "no negative vector was actually executed")
        XCTAssertEqual([], mismatches, "negative vector mismatches")
        FileHandle.standardError.write(Data("Swift negative vectors: \(ran) executed, \(gaps) gap-skip\n".utf8))
    }
}
