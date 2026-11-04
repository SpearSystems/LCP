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

/// LEP-0001 W2-01: batch submission treats 207 as success, preserves per-item
/// errors, returns receipts in input order, and refuses an over-cap batch
/// before any request.
final class BatchSubmissionTests: XCTestCase {
    func testBatchSubmissionTreats207AsSuccessAndOrdersReceipts() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BatchURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        BatchURLProtocol.requests = []
        BatchURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/v1/lcp/leads/batch")
            let body = #"{"request_id":"req-batch-1","summary":{"total":2,"accepted":1,"duplicate":0,"rejected":1,"error":0},"results":[{"item_id":"item_b","status":"accepted","lead_id":"lead_b"},{"item_id":"item_a","status":"rejected","error":{"code":"LCP-100","message":"invalid lead"}}]}"#
            return (HTTPURLResponse(url: request.url!, statusCode: 207, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!, Data(body.utf8))
        }
        let client = LCPClient(endpoint: URL(string: "https://lcp.invalid")!, session: session)
        let items = [
            LCPBatchItem(itemId: "item_a", idempotencyKey: "batch-a", payload: ["lead_id": .string("lead_a")]),
            LCPBatchItem(itemId: "item_b", idempotencyKey: "batch-b", payload: ["lead_id": .string("lead_b")]),
        ]
        let results = try await client.submitLeadsBatch(items, requestId: "req-batch-1")
        XCTAssertEqual(results.map(\.itemId), ["item_a", "item_b"])
        XCTAssertEqual(results[0].status, "rejected")
        if case .object(let error)? = results[0].error {
            XCTAssertEqual(error["code"], .string("LCP-100"))
        } else {
            XCTFail("per-item error object was lost")
        }
        XCTAssertEqual(results[1].status, "accepted")
        let overCap = (0..<101).map { LCPBatchItem(itemId: "item_\($0)", idempotencyKey: "key_\($0)", payload: [:]) }
        do {
            _ = try await client.submitLeadsBatch(overCap)
            XCTFail("batch over the cap was accepted")
        } catch { }
        XCTAssertEqual(BatchURLProtocol.requests.count, 1, "cap check must run before sending")
    }
}

final class BatchURLProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?
    static var requests: [URLRequest] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: NSError(domain: "LCP", code: 500))
            return
        }
        do {
            Self.requests.append(request)
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

/// LEP-0005: the history read sends exactly the supplied, non-empty filters
/// (a blank filter would widen the page), returns the decoded body, and
/// surfaces a 4xx as the client's normal error.
final class HistoryReadTests: XCTestCase {
    private func session() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BatchURLProtocol.self]
        return URLSession(configuration: configuration)
    }

    func testHistoryReadSendsOnlySuppliedFiltersAndReturnsBody() async throws {
        let session = self.session()
        defer { session.invalidateAndCancel() }
        BatchURLProtocol.requests = []
        BatchURLProtocol.handler = { request in
            let body = #"{"requests":[{"request_id":"req-hist-1","message_type":"lead","direction":"inbound","status":"OPEN","created_at":"2026-11-01T00:00:00Z"}]}"#
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!, Data(body.utf8))
        }
        let client = LCPClient(endpoint: URL(string: "https://lcp.invalid")!, session: session)
        let body = try await client.requestHistory(
            LCPHistoryParams(limit: 2, cursor: "", status: "OPEN", to: "2026-11-01T00:00:00Z")
        )
        guard case .object(let root) = body,
              case .array(let rows)? = root["requests"],
              rows.count == 1,
              case .object(let row) = rows[0]
        else {
            return XCTFail("history body was reshaped: \(body)")
        }
        XCTAssertEqual(row["request_id"], .string("req-hist-1"))
        XCTAssertEqual(row["status"], .string("OPEN"))
        let url = try XCTUnwrap(BatchURLProtocol.requests.first?.url)
        XCTAssertEqual(url.path, "/v1/lcp/history/requests")
        let components = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false))
        XCTAssertEqual(components.queryItems, [
            URLQueryItem(name: "limit", value: "2"),
            URLQueryItem(name: "status", value: "OPEN"),
            URLQueryItem(name: "to", value: "2026-11-01T00:00:00Z"),
        ])
    }

    func testHistoryReadSurfacesHTTPErrors() async throws {
        let session = self.session()
        defer { session.invalidateAndCancel() }
        BatchURLProtocol.requests = []
        BatchURLProtocol.handler = { request in
            let body = #"{"errors":[{"code":"LCP-001","message":"limit out of range"}]}"#
            return (HTTPURLResponse(url: request.url!, statusCode: 400, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!, Data(body.utf8))
        }
        let client = LCPClient(endpoint: URL(string: "https://lcp.invalid")!, session: session)
        do {
            _ = try await client.requestHistory(LCPHistoryParams(limit: 500))
            XCTFail("history 4xx was accepted")
        } catch {
            let error = error as NSError
            XCTAssertEqual(error.domain, "LCP")
            XCTAssertEqual(error.code, 400)
            XCTAssertTrue((error.userInfo[NSLocalizedDescriptionKey] as? String)?.contains("LCP-001") == true)
        }
    }
}

/// The single-message surface: one test per protocol operation, each asserting
/// the request line against the reference platform's route and that the
/// response body comes back decoded and unreshaped.
final class SingleOperationTests: XCTestCase {
    private func session(returning body: String, assertRequest: @escaping (URLRequest) throws -> Void) -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BatchURLProtocol.self]
        BatchURLProtocol.requests = []
        BatchURLProtocol.handler = { request in
            try assertRequest(request)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!, Data(body.utf8))
        }
        return URLSession(configuration: configuration)
    }

    private func decoded(_ json: String) throws -> JSONValue {
        try JSONDecoder().decode(JSONValue.self, from: Data(json.utf8))
    }

    /// A minimal envelope carrying the type and its idempotency key, so the
    /// derived `X-LCP-Idempotency-Key` can be asserted too.
    private func envelope(type: String) -> JSONValue {
        .object(["lcp": .object([
            "version": .string("1.0.0"),
            "message": .object([
                "id": .string("msg-1"),
                "type": .string(type),
                "idempotency_key": .string("idem-\(type)"),
            ]),
            "payload": .object(["lead_id": .string("lead_1")]),
        ])])
    }

    private func assertNoQuery(_ request: URLRequest) throws {
        let components = try XCTUnwrap(URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false))
        XCTAssertNil(components.queryItems)
    }

    func testSubmitLeadPostsEnvelopeAndReturnsBody() async throws {
        let body = #"{"lcp":{"message":{"id":"msg-1"},"payload":{"lead_id":"lead_1","status":"accepted"}}}"#
        let session = self.session(returning: body) { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/v1/lcp/leads")
            XCTAssertEqual(request.value(forHTTPHeaderField: "X-LCP-Idempotency-Key"), "idem-lead")
            try self.assertNoQuery(request)
        }
        defer { session.invalidateAndCancel() }
        let client = LCPClient(endpoint: URL(string: "https://lcp.invalid")!, session: session)
        let value = try await client.submitLead(envelope(type: "lead"))
        XCTAssertEqual(value, try decoded(body), "submitLead body was reshaped: \(value)")
    }

    func testSubmitCallPostsEnvelopeAndReturnsBody() async throws {
        let body = #"{"lcp":{"message":{"id":"msg-1"},"payload":{"lead_id":"lead_1","status":"accepted"}}}"#
        let session = self.session(returning: body) { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/v1/lcp/calls")
            XCTAssertEqual(request.value(forHTTPHeaderField: "X-LCP-Idempotency-Key"), "idem-call")
            try self.assertNoQuery(request)
        }
        defer { session.invalidateAndCancel() }
        let client = LCPClient(endpoint: URL(string: "https://lcp.invalid")!, session: session)
        let value = try await client.submitCall(envelope(type: "call"))
        XCTAssertEqual(value, try decoded(body), "submitCall body was reshaped: \(value)")
    }

    func testSubmitBidPostsEnvelopeAndReturnsBody() async throws {
        let body = #"{"lcp":{"message":{"id":"msg-1"},"payload":{"ping_id":"ping_1","decision":"bid"}}}"#
        let session = self.session(returning: body) { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/v1/lcp/bids")
            XCTAssertEqual(request.value(forHTTPHeaderField: "X-LCP-Idempotency-Key"), "idem-bid")
            try self.assertNoQuery(request)
        }
        defer { session.invalidateAndCancel() }
        let client = LCPClient(endpoint: URL(string: "https://lcp.invalid")!, session: session)
        let value = try await client.submitBid(envelope(type: "bid"))
        XCTAssertEqual(value, try decoded(body), "submitBid body was reshaped: \(value)")
    }

    func testQueryLeadStatusReadsOneLeadAndReturnsBody() async throws {
        let body = #"{"lead_id":"lead_42","status":"DELIVERED"}"#
        let session = self.session(returning: body) { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/v1/lcp/leads/lead_42")
            try self.assertNoQuery(request)
        }
        defer { session.invalidateAndCancel() }
        let client = LCPClient(endpoint: URL(string: "https://lcp.invalid")!, session: session)
        let value = try await client.queryLeadStatus("lead_42")
        XCTAssertEqual(value, try decoded(body), "queryLeadStatus body was reshaped: \(value)")
    }

    func testGetCapabilitiesReadsCapabilityDocument() async throws {
        let body = #"{"verticals":["personal_loans"],"version":"1.0.0"}"#
        let session = self.session(returning: body) { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/v1/lcp/capabilities")
            try self.assertNoQuery(request)
        }
        defer { session.invalidateAndCancel() }
        let client = LCPClient(endpoint: URL(string: "https://lcp.invalid")!, session: session)
        let value = try await client.getCapabilities()
        XCTAssertEqual(value, try decoded(body), "getCapabilities body was reshaped: \(value)")
    }

    func testListOffersSendsVerticalFilterAndReturnsBody() async throws {
        let body = #"{"offers":[{"offer_id":"offer_1","vertical":"personal_loans"}]}"#
        let session = self.session(returning: body) { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/v1/lcp/offers")
            let components = try XCTUnwrap(URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false))
            XCTAssertEqual(components.queryItems, [URLQueryItem(name: "vertical", value: "personal_loans")])
        }
        defer { session.invalidateAndCancel() }
        let client = LCPClient(endpoint: URL(string: "https://lcp.invalid")!, session: session)
        let value = try await client.listOffers(vertical: "personal_loans")
        XCTAssertEqual(value, try decoded(body), "listOffers body was reshaped: \(value)")
    }

    func testGetSchemaKeepsNestedNameAndReturnsBody() async throws {
        let body = #"{"type":"object","title":"personal_loans"}"#
        let session = self.session(returning: body) { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/v1/lcp/schemas/verticals/personal_loans")
            try self.assertNoQuery(request)
        }
        defer { session.invalidateAndCancel() }
        let client = LCPClient(endpoint: URL(string: "https://lcp.invalid")!, session: session)
        let value = try await client.getSchema("verticals/personal_loans")
        XCTAssertEqual(value, try decoded(body), "getSchema body was reshaped: \(value)")
    }
}
