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

public enum LCPSigning {
    public static func canonicalBytes(timestamp: String, idempotencyKey: String?, body: Data) -> Data { Data("\(timestamp)\n\(idempotencyKey ?? "")\n".utf8) + body }
    public static func sign(secret: String, timestamp: String, idempotencyKey: String?, body: Data) -> String { let key = SymmetricKey(data: Data(secret.utf8)); let digest = HMAC<SHA256>.authenticationCode(for: canonicalBytes(timestamp: timestamp, idempotencyKey: idempotencyKey, body: body), using: key); return digest.map { String(format: "%02x", $0) }.joined() }
    public static func verify(secret: String, signature: String, timestamp: String, idempotencyKey: String?, body: Data, maxSkew: TimeInterval = 300, now: Date = Date()) throws { guard let date = ISO8601DateFormatter().date(from: timestamp), abs(now.timeIntervalSince(date)) <= maxSkew else { throw NSError(domain: "LCP", code: 400, userInfo: [NSLocalizedDescriptionKey: "timestamp outside replay window"]) }; guard sign(secret: secret, timestamp: timestamp, idempotencyKey: idempotencyKey, body: body) == signature.lowercased() else { throw NSError(domain: "LCP", code: 401, userInfo: [NSLocalizedDescriptionKey: "invalid signature"]) } }
}

public final class LCPClient {
    private let endpoint: URL; private let senderId: String?; private let apiKey: String?; private let hmacSecret: String?; private let session: URLSession
    public init(endpoint: URL, senderId: String? = nil, apiKey: String? = nil, hmacSecret: String? = nil, session: URLSession = .shared) { self.endpoint = endpoint; self.senderId = senderId; self.apiKey = apiKey; self.hmacSecret = hmacSecret; self.session = session }
    public func request(path: String, method: String, body: Data? = nil, idempotencyKey: String? = nil, test: Bool = false) async throws -> Data { var request = URLRequest(url: endpoint.appendingPathComponent(path.trimmingCharacters(in: CharacterSet(charactersIn: "/")))); request.httpMethod = method; request.httpBody = body; request.setValue("application/json", forHTTPHeaderField: "Content-Type"); request.setValue("application/json", forHTTPHeaderField: "Accept"); if let senderId { request.setValue(senderId, forHTTPHeaderField: "X-LCP-Sender-Id") }; if let apiKey { request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization") }; if let idempotencyKey { request.setValue(idempotencyKey, forHTTPHeaderField: "X-LCP-Idempotency-Key") }; if test { request.setValue("true", forHTTPHeaderField: "X-LCP-Test") }; if let hmacSecret { let timestamp = ISO8601DateFormatter().string(from: Date()); request.setValue(timestamp, forHTTPHeaderField: "X-LCP-Timestamp"); request.setValue(LCPSigning.sign(secret: hmacSecret, timestamp: timestamp, idempotencyKey: idempotencyKey, body: body ?? Data()), forHTTPHeaderField: "X-LCP-Signature") }; let (data, response) = try await session.data(for: request); guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { throw NSError(domain: "LCP", code: (response as? HTTPURLResponse)?.statusCode ?? 500, userInfo: [NSLocalizedDescriptionKey: String(data: data, encoding: .utf8) ?? "LCP HTTP error"]) }; return data }
}
