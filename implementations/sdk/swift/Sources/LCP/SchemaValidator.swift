import Foundation
import JSONSchema

public enum LCPValidationError: Error, CustomStringConvertible {
    case unknownSchema(String)
    case invalid(String, String)
    public var description: String {
        switch self {
        case .unknownSchema(let name): return "Unknown LCP schema: \(name)"
        case .invalid(let name, let message): return "LCP schema validation failed for \(name): \(message)"
        }
    }
}

/// Validates canonical LCP schemas offline using the complete generated bundle.
public final class LCPValidator {
    private var schemas: [String: Schema] = [:]
    private var names: [String: String] = [:]

    public init(schemaTexts: [String: String]) throws {
        let parsed = try schemaTexts.mapValues { try JSONSerialization.jsonObject(with: Data($0.utf8)) }
        let core = parsed.first { Self.normalize($0.key) == "core" }?.value
        for (name, value) in parsed {
            let inlined = Self.inlineCoreReferences(value, core: core)
            let data = try JSONSerialization.data(withJSONObject: inlined)
            let schema = try Schema(instance: String(data: data, encoding: .utf8)!)
            schemas[normalize(name)] = schema
            names[normalize(name)] = normalize(name)
            if let object = value as? [String: Any], let schemaId = object["$id"] as? String {
                names[normalize(schemaId)] = normalize(name)
            }
        }
    }

    public static func fromDirectory(_ root: URL) throws -> LCPValidator {
        var documents: [String: String] = [:]
        let enumerator = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.isRegularFileKey])
        while let url = enumerator?.nextObject() as? URL {
            guard url.pathExtension == "json" else { continue }
            let relative = url.path.replacingOccurrences(of: root.path + "/", with: "")
            documents[relative] = try String(contentsOf: url, encoding: .utf8)
        }
        return try LCPValidator(schemaTexts: documents)
    }

    public func validate(_ schemaName: String, instance: String) throws {
        let normalized = normalize(schemaName)
        guard let key = names[normalized], let schema = schemas[key] else { throw LCPValidationError.unknownSchema(schemaName) }
        let result = try schema.validate(instance: instance)
        guard result.isValid else { throw LCPValidationError.invalid(schemaName, String(describing: result)) }
    }

    public func validateEnvelope(_ envelope: String) throws {
        try validate("schemas/envelope.json", instance: envelope)
        guard let root = try JSONSerialization.jsonObject(with: Data(envelope.utf8)) as? [String: Any], let lcp = root["lcp"] as? [String: Any], let message = lcp["message"] as? [String: Any], let type = message["type"] as? String, let payload = lcp["payload"] else { throw LCPValidationError.invalid("envelope", "missing message type or payload") }
        try validate("schemas/\(type).json", instance: String(data: JSONSerialization.data(withJSONObject: payload), encoding: .utf8)!)
    }

    public func validateOffer(_ offer: String) throws { try validate("schemas/offer.json", instance: offer) }
    public func validateVertical(_ vertical: String, attributes: String) throws { try validate("verticals/\(vertical).json", instance: attributes) }

    private static func inlineCoreReferences(_ value: Any, core: Any?) -> Any {
        if let object = value as? [String: Any] {
            if let reference = object["$ref"] as? String, reference.contains("core.json#/$defs/"), let coreObject = core as? [String: Any], let defs = coreObject["$defs"] as? [String: Any], let name = reference.components(separatedBy: "core.json#/$defs/").last, let definition = defs[name] {
                return inlineCoreReferences(definition, core: core)
            }
            return object.mapValues { inlineCoreReferences($0, core: core) }
        }
        if let array = value as? [Any] { return array.map { inlineCoreReferences($0, core: core) } }
        return value
    }

    private static func normalize(_ value: String) -> String { value.replacingOccurrences(of: "\\", with: "/").trimmingCharacters(in: CharacterSet(charactersIn: "/")).replacingOccurrences(of: "schemas/", with: "").replacingOccurrences(of: "verticals/", with: "vertical:").replacingOccurrences(of: ".json", with: "") }
    private func normalize(_ value: String) -> String { Self.normalize(value) }
}
