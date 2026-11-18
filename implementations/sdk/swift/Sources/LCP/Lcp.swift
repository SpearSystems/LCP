import Foundation
import CryptoKit
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public struct LCPMessage: Codable {
    public let id: String
    public let type: String
    public let timestamp: String
    public let senderId: String
    public let receiverId: String
    public let correlationId: String?
    public let idempotencyKey: String
    public let test: Bool
    public init(id: String = UUID().uuidString, type: String, timestamp: String = ISO8601DateFormatter().string(from: Date()), senderId: String, receiverId: String, correlationId: String? = nil, idempotencyKey: String? = nil, test: Bool = false) { self.id = id; self.type = type; self.timestamp = timestamp; self.senderId = senderId; self.receiverId = receiverId; self.correlationId = correlationId; self.idempotencyKey = idempotencyKey ?? "\(senderId)-\(type)-\(UUID().uuidString)"; self.test = test }
    enum CodingKeys: String, CodingKey { case id, type, timestamp, senderId = "sender_id", receiverId = "receiver_id", correlationId = "correlation_id", idempotencyKey = "idempotency_key", test }
}

public struct LCPEnvelope<Payload: Encodable>: Encodable {
    public let version: String
    public let message: LCPMessage
    public let payload: Payload
    public init(version: String = "1.0.0", message: LCPMessage, payload: Payload) { self.version = version; self.message = message; self.payload = payload }
    enum CodingKeys: String, CodingKey { case lcp }
    enum LCPKeys: String, CodingKey { case version, message, payload }
    public func encode(to encoder: Encoder) throws { var container = encoder.container(keyedBy: CodingKeys.self); var lcp = container.nestedContainer(keyedBy: LCPKeys.self, forKey: .lcp); try lcp.encode(version, forKey: .version); try lcp.encode(message, forKey: .message); try lcp.encode(payload, forKey: .payload) }
}

/// Errors the LCP Swift SDK throws. Each case bridges to an `NSError` in the
/// `"LCP"` domain with the code the SDK used before typed errors (400 for bad
/// input or timestamps, 401 for a bad signature, the HTTP status for a
/// non-2xx response), so callers that inspect `NSError` keep working.
public enum LCPError: Error, Equatable, CustomNSError, LocalizedError {
    /// `X-LCP-Timestamp` is not a zoned ISO 8601 or RFC 2822 instant.
    case invalidTimestamp
    /// The timestamp is valid but outside the replay window.
    case timestampOutsideReplayWindow
    /// The signature is not hex or does not match the signed bytes.
    case invalidSignature
    /// A caller-supplied argument is invalid; the message says which.
    case invalidInput(String)
    /// The endpoint answered with a non-2xx status after any retries.
    case http(status: Int, body: String)

    public static var errorDomain: String { "LCP" }

    public var errorCode: Int {
        switch self {
        case .invalidSignature: return 401
        case .http(let status, _): return status
        case .invalidTimestamp, .timestampOutsideReplayWindow, .invalidInput: return 400
        }
    }

    public var errorDescription: String? {
        switch self {
        case .invalidTimestamp: return "invalid X-LCP-Timestamp"
        case .timestampOutsideReplayWindow: return "timestamp outside replay window"
        case .invalidSignature: return "invalid signature"
        case .invalidInput(let message): return message
        case .http(let status, let body): return body.isEmpty ? "LCP HTTP \(status)" : body
        }
    }

    public var errorUserInfo: [String: Any] { [NSLocalizedDescriptionKey: errorDescription ?? ""] }
}

public enum LCPSigning {
    public static func canonicalBytes(timestamp: String, idempotencyKey: String?, body: Data) -> Data { Data("\(timestamp)\n\(idempotencyKey ?? "")\n".utf8) + body }
    public static func sign(secret: String, timestamp: String, idempotencyKey: String?, body: Data) -> String { hmacHex(secret: secret, data: canonicalBytes(timestamp: timestamp, idempotencyKey: idempotencyKey, body: body)) }

    /// The single HMAC-SHA256 -> lowercase-hex construction in this SDK (SPEC.md §9).
    public static func hmacHex(secret: String, data: Data) -> String {
        let key = SymmetricKey(data: Data(secret.utf8))
        let digest = HMAC<SHA256>.authenticationCode(for: data, using: key)
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    /// The SPEC.md §9 pinned contact preimage for a value. Phone: the wire value
    /// verbatim, byte-for-byte — no folding, stripping, or reformatting (the
    /// reference platform's phone_hash builder is the executed baseline). Email:
    /// the §9 pinned candidate canonical — trim surrounding whitespace,
    /// ASCII-lowercase the domain, keep the local-part bytes verbatim,
    /// plus-addresses distinct. Email-form contacted_hashes ENTRIES stay
    /// emission-gated until #153 T9 executed vectors publish; this is the pinned
    /// compute rule every contact-hash vector row reproduces under.
    public static func normalizeContact(_ value: String, domain: String) throws -> String {
        switch domain {
        case "phone": return value
        case "email":
            let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
            guard let separator = trimmed.lastIndex(of: "@") else {
                throw LCPError.invalidInput("email contact preimage must contain '@'")
            }
            let local = String(trimmed[..<separator])
            let host = String(trimmed[trimmed.index(after: separator)...])
            let lowered = String(host.map { $0 >= "A" && $0 <= "Z" ? Character(UnicodeScalar($0.asciiValue! + 32)) : $0 })
            return "\(local)@\(lowered)"
        default:
            throw LCPError.invalidInput("unknown contact domain '\(domain)' (expected phone or email)")
        }
    }

    /// Per-pair contact hash (SPEC.md §9, LEP-0003 §3): lowercase-hex
    /// HMAC-SHA256(pair_secret, normalized_preimage) keyed with the secret shared
    /// with the message's receiver. phone_hash, email_hash, and contacted_hashes
    /// all ride this one construction — never an unsalted digest, never SHA-1.
    public static func contactHash(secret: String, value: String, domain: String = "phone") throws -> String {
        try hmacHex(secret: secret, data: Data(normalizeContact(value, domain: domain).utf8))
    }
    /// Verify an LCP HMAC signature and its replay window (SPEC.md §9).
    ///
    /// The timestamp rule matches the Python reference verifier: an ISO 8601 /
    /// RFC 3339 date-time with or without fractional seconds, or an RFC 2822
    /// date, and it must carry a timezone. A timestamp without one cannot be
    /// compared with the receiver clock and is rejected. The signature must be
    /// hex (either case); it is compared in constant time by CryptoKit.
    public static func verify(secret: String, signature: String, timestamp: String, idempotencyKey: String?, body: Data, maxSkew: TimeInterval = 300, now: Date = Date()) throws {
        guard let signedAt = parseTimestamp(timestamp) else { throw LCPError.invalidTimestamp }
        guard abs(now.timeIntervalSince(signedAt)) <= maxSkew else { throw LCPError.timestampOutsideReplayWindow }
        guard let mac = hexBytes(signature),
              HMAC<SHA256>.isValidAuthenticationCode(mac, authenticating: canonicalBytes(timestamp: timestamp, idempotencyKey: idempotencyKey, body: body), using: SymmetricKey(data: Data(secret.utf8)))
        else { throw LCPError.invalidSignature }
    }

    private static let isoFractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
    private static let isoWhole = ISO8601DateFormatter()
    private static let rfc2822: [DateFormatter] = ["EEE, d MMM yyyy HH:mm:ss zzz", "d MMM yyyy HH:mm:ss zzz"].map { format in
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = format
        return formatter
    }

    /// The verifier clock parse; nil when the value is not a zoned instant.
    static func parseTimestamp(_ value: String) -> Date? {
        isoWhole.date(from: value) ?? isoFractional.date(from: value) ?? rfc2822.lazy.compactMap { $0.date(from: value) }.first
    }

    /// Decode a hex string, or nil when it holds any non-hex character.
    private static func hexBytes(_ value: String) -> Data? {
        let digits = Array(value.utf8)
        guard digits.count % 2 == 0 else { return nil }
        var bytes = Data(capacity: digits.count / 2)
        for index in stride(from: 0, to: digits.count, by: 2) {
            guard let high = hexValue(digits[index]), let low = hexValue(digits[index + 1]) else { return nil }
            bytes.append(high << 4 | low)
        }
        return bytes
    }

    private static func hexValue(_ digit: UInt8) -> UInt8? {
        switch digit {
        case UInt8(ascii: "0")...UInt8(ascii: "9"): return digit - UInt8(ascii: "0")
        case UInt8(ascii: "a")...UInt8(ascii: "f"): return digit - UInt8(ascii: "a") + 10
        case UInt8(ascii: "A")...UInt8(ascii: "F"): return digit - UInt8(ascii: "A") + 10
        default: return nil
        }
    }
}

/// One lead in a batch: per-item identifiers plus the lead payload (schemas/batch.json).
public struct LCPBatchItem: Encodable {
    public let itemId: String
    public let idempotencyKey: String
    public let payload: [String: JSONValue]
    public init(itemId: String, idempotencyKey: String, payload: [String: JSONValue]) { self.itemId = itemId; self.idempotencyKey = idempotencyKey; self.payload = payload }
    enum CodingKeys: String, CodingKey { case itemId = "item_id", idempotencyKey = "idempotency_key", payload }
}

/// One per-item receipt from a batch submission.
public struct LCPBatchResult: Decodable {
    public let itemId: String
    public let status: String
    public let leadId: String?
    public let error: JSONValue?
    enum CodingKeys: String, CodingKey { case itemId = "item_id", status, leadId = "lead_id", error }
}

private struct LCPBatchRequest: Encodable {
    let requestId: String
    let items: [LCPBatchItem]
    enum CodingKeys: String, CodingKey { case requestId = "request_id", items }
}

private struct LCPBatchResponse: Decodable { let results: [LCPBatchResult] }

public final class LCPClient {
    private let endpoint: URL; private let senderId: String?; private let apiKey: String?; private let hmacSecret: String?; private let session: URLSession
    private let timeout: TimeInterval; private let maxRetries: Int

    /// `timeout` bounds each attempt (seconds). `maxRetries` is how many times
    /// a 429, 500, 502, 503 or 504 response, or a transport failure, is retried
    /// after the first attempt; the wait honours a delta-seconds `Retry-After`
    /// and otherwise backs off 1 s, 2 s, 4 s, ... — the Python SDK policy.
    public init(endpoint: URL, senderId: String? = nil, apiKey: String? = nil, hmacSecret: String? = nil, session: URLSession = .shared, timeout: TimeInterval = 30, maxRetries: Int = 2) { self.endpoint = endpoint; self.senderId = senderId; self.apiKey = apiKey; self.hmacSecret = hmacSecret; self.session = session; self.timeout = timeout; self.maxRetries = max(0, maxRetries) }

    public func request(path: String, method: String, body: Data? = nil, idempotencyKey: String? = nil, test: Bool = false, query: [URLQueryItem] = [], maxRetries: Int? = nil) async throws -> Data {
        // Resolve rather than append: a caller-supplied identifier is escaped
        // before it reaches here, and appending would re-encode the escapes
        // (`%2F` -> `%252F`). The trailing slash keeps any endpoint base path.
        let relative = path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let base = URL(string: relative, relativeTo: endpoint.appendingPathComponent("")),
              var components = URLComponents(url: base.absoluteURL, resolvingAgainstBaseURL: false)
        else {
            throw LCPError.invalidInput("invalid LCP request URL")
        }
        if !query.isEmpty { components.queryItems = query }
        guard let url = components.url else {
            throw LCPError.invalidInput("invalid LCP request URL")
        }
        let retries = max(0, maxRetries ?? self.maxRetries)
        var attempt = 0
        while true {
            var request = URLRequest(url: url)
            request.httpMethod = method
            request.httpBody = body
            request.timeoutInterval = timeout
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.setValue("application/json", forHTTPHeaderField: "Accept")
            if let senderId { request.setValue(senderId, forHTTPHeaderField: "X-LCP-Sender-Id") }
            if let apiKey { request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization") }
            if let idempotencyKey { request.setValue(idempotencyKey, forHTTPHeaderField: "X-LCP-Idempotency-Key") }
            if test { request.setValue("true", forHTTPHeaderField: "X-LCP-Test") }
            if let hmacSecret {
                // Signed per attempt: a retry after a long Retry-After keeps a fresh timestamp.
                let timestamp = ISO8601DateFormatter().string(from: Date())
                request.setValue(timestamp, forHTTPHeaderField: "X-LCP-Timestamp")
                request.setValue(LCPSigning.sign(secret: hmacSecret, timestamp: timestamp, idempotencyKey: idempotencyKey, body: body ?? Data()), forHTTPHeaderField: "X-LCP-Signature")
            }
            let data: Data, response: URLResponse
            do {
                (data, response) = try await session.data(for: request)
            } catch let error as URLError where error.code != .cancelled && attempt < retries {
                try await Self.sleep(seconds: Self.backoff(attempt))
                attempt += 1
                continue
            }
            let status = (response as? HTTPURLResponse)?.statusCode ?? 500
            if (200..<300).contains(status) { return data }
            guard Self.retryableStatuses.contains(status), attempt < retries else {
                throw LCPError.http(status: status, body: String(data: data, encoding: .utf8) ?? "")
            }
            let retryAfter = (response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Retry-After")
            try await Self.sleep(seconds: Self.retryDelay(retryAfter: retryAfter, attempt: attempt))
            attempt += 1
        }
    }

    static let retryableStatuses: Set<Int> = [429, 500, 502, 503, 504]

    /// Wait before retry `attempt` (0-based): a delta-seconds `Retry-After`
    /// wins; an absent, HTTP-date or malformed value falls back to backoff.
    static func retryDelay(retryAfter: String?, attempt: Int) -> UInt64 {
        if let retryAfter, !retryAfter.isEmpty, retryAfter.utf8.allSatisfy({ (UInt8(ascii: "0")...UInt8(ascii: "9")).contains($0) }) {
            return UInt64(retryAfter) ?? backoff(attempt)
        }
        return backoff(attempt)
    }

    static func backoff(_ attempt: Int) -> UInt64 { 1 << UInt64(min(attempt, 32)) }

    private static func sleep(seconds: UInt64) async throws {
        let (nanoseconds, overflow) = seconds.multipliedReportingOverflow(by: 1_000_000_000)
        try await Task.sleep(nanoseconds: overflow ? UInt64.max : nanoseconds)
    }

    /// Submit one message envelope and return the decoded response body
    /// unchanged: leads (`POST /v1/lcp/leads`), calls (`POST /v1/lcp/calls`),
    /// bids (`POST /v1/lcp/bids`). The envelope's
    /// `lcp.message.idempotency_key` — when present — is the key that is sent
    /// and signed, so a signed submit labels the key its payload carries. A
    /// non-2xx throws `LCPError.http` (an `NSError` with the HTTP status as its code).
    public func submitLead(_ envelope: JSONValue, test: Bool = false) async throws -> JSONValue {
        try await submitEnvelope(envelope, path: "/v1/lcp/leads", test: test)
    }

    public func submitCall(_ envelope: JSONValue, test: Bool = false) async throws -> JSONValue {
        try await submitEnvelope(envelope, path: "/v1/lcp/calls", test: test)
    }

    public func submitBid(_ envelope: JSONValue, test: Bool = false) async throws -> JSONValue {
        try await submitEnvelope(envelope, path: "/v1/lcp/bids", test: test)
    }

    /// Read one lead's status (`GET /v1/lcp/leads/{lead_id}`) and return the
    /// decoded body unchanged.
    public func queryLeadStatus(_ leadId: String) async throws -> JSONValue {
        try await getJSON(path: "/v1/lcp/leads/\(Self.pathSegment(leadId))")
    }

    /// Read the receiver's capability document (`GET /v1/lcp/capabilities`).
    public func getCapabilities() async throws -> JSONValue {
        try await getJSON(path: "/v1/lcp/capabilities")
    }

    /// List offers (`GET /v1/lcp/offers`), optionally filtered to one vertical.
    public func listOffers(vertical: String? = nil) async throws -> JSONValue {
        let query = vertical.map { [URLQueryItem(name: "vertical", value: $0)] } ?? []
        return try await getJSON(path: "/v1/lcp/offers", query: query)
    }

    /// Fetch one published schema (`GET /v1/lcp/schemas/{name}`). A nested name
    /// keeps its separators, matching the other SDKs.
    public func getSchema(_ name: String) async throws -> JSONValue {
        try await getJSON(path: "/v1/lcp/schemas/\(Self.pathSegment(name, allowingSlash: true))")
    }

    private func submitEnvelope(_ envelope: JSONValue, path: String, test: Bool) async throws -> JSONValue {
        let body = try JSONEncoder().encode(envelope)
        let data = try await request(
            path: path,
            method: "POST",
            body: body,
            idempotencyKey: Self.envelopeIdempotencyKey(envelope),
            test: test
        )
        return try JSONDecoder().decode(JSONValue.self, from: data)
    }

    private func getJSON(path: String, query: [URLQueryItem] = []) async throws -> JSONValue {
        let data = try await request(path: path, method: "GET", query: query)
        return try JSONDecoder().decode(JSONValue.self, from: data)
    }

    /// `lcp.message.idempotency_key`, when the envelope carries a string one.
    private static func envelopeIdempotencyKey(_ envelope: JSONValue) -> String? {
        guard case .object(let root) = envelope,
              case .object(let lcp)? = root["lcp"],
              case .object(let message)? = lcp["message"],
              case .string(let key)? = message["idempotency_key"]
        else { return nil }
        return key
    }

    /// Percent-encode one path segment. `/` is escaped unless the segment is a
    /// schema name, whose nested form keeps its separators like the other SDKs.
    private static func pathSegment(_ value: String, allowingSlash: Bool = false) -> String {
        var allowed = CharacterSet.urlPathAllowed
        if !allowingSlash { allowed.remove(charactersIn: "/") }
        return value.addingPercentEncoding(withAllowedCharacters: allowed) ?? value
    }

    /// Submit up to 100 lead items in one request (LEP-0001 W2-01) and return
    /// the per-item receipts in input order. HTTP 207 partial success is a
    /// successful response; per-item error objects are preserved. The batch is
    /// never retried as a whole — retrying only rejected/error receipts is the
    /// caller's job.
    public func submitLeadsBatch(_ items: [LCPBatchItem], requestId: String? = nil) async throws -> [LCPBatchResult] {
        guard (1...100).contains(items.count) else {
            throw LCPError.invalidInput("batch supports 1..100 items, got \(items.count)")
        }
        let payload = LCPBatchRequest(requestId: requestId ?? UUID().uuidString, items: items)
        let data = try await request(path: "/v1/lcp/leads/batch", method: "POST", body: try JSONEncoder().encode(payload), maxRetries: 0)
        let response = try JSONDecoder().decode(LCPBatchResponse.self, from: data)
        let order = Dictionary(uniqueKeysWithValues: items.enumerated().map { ($1.itemId, $0) })
        return response.results.enumerated().sorted { left, right in
            let leftRank = order[left.element.itemId] ?? items.count
            let rightRank = order[right.element.itemId] ?? items.count
            return leftRank == rightRank ? left.offset < right.offset : leftRank < rightRank
        }.map(\.element)
    }

    /// Page the LEP-0005 request-history audit log (`history:read`) and return
    /// the decoded body unchanged (`requests`). A 4xx throws
    /// `LCPError.http` (an `NSError` with the HTTP status as its code).
    public func requestHistory(_ params: LCPHistoryParams = LCPHistoryParams()) async throws -> JSONValue {
        let data = try await request(path: "/v1/lcp/history/requests", method: "GET", query: params.queryItems)
        return try JSONDecoder().decode(JSONValue.self, from: data)
    }
}

/// LEP-0005 `GET /v1/lcp/history/requests` filters (`history:read`). Every
/// field is optional: `nil` and empty strings are omitted from the query
/// rather than sent blank, because a blank filter widens the page instead of
/// narrowing it. Rows are scoped server-side to the credential's tenant, so
/// there is no tenant field here.
public struct LCPHistoryParams {
    public var limit: Int?
    public var cursor: String?
    public var requestId: String?
    public var status: String?
    public var vertical: String?
    public var callerPhoneHash: String?
    public var from: String?
    public var to: String?

    public init(
        limit: Int? = nil,
        cursor: String? = nil,
        requestId: String? = nil,
        status: String? = nil,
        vertical: String? = nil,
        callerPhoneHash: String? = nil,
        from: String? = nil,
        to: String? = nil
    ) {
        self.limit = limit
        self.cursor = cursor
        self.requestId = requestId
        self.status = status
        self.vertical = vertical
        self.callerPhoneHash = callerPhoneHash
        self.from = from
        self.to = to
    }

    var queryItems: [URLQueryItem] {
        var items: [URLQueryItem] = []
        if let limit { items.append(URLQueryItem(name: "limit", value: String(limit))) }
        for (name, value) in [
            ("cursor", cursor),
            ("request_id", requestId),
            ("status", status),
            ("vertical", vertical),
            ("caller_phone_hash", callerPhoneHash),
            ("from", from),
            ("to", to),
        ] {
            if let value, !value.isEmpty { items.append(URLQueryItem(name: name, value: value)) }
        }
        return items
    }
}
