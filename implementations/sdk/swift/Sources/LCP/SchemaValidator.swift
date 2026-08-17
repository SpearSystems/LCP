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
    private var documents: [String: Any] = [:]

    public init(schemaTexts: [String: String]) throws {
        let parsed = try schemaTexts.mapValues { try JSONSerialization.jsonObject(with: Data($0.utf8)) }
        let core = parsed.first { Self.normalize($0.key) == "core" }?.value
        for (name, value) in parsed {
            documents[Self.normalize(name)] = value
            if let object = value as? [String: Any], let schemaId = object["$id"] as? String {
                documents[Self.normalize(schemaId)] = value
            }
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
        let root = root.standardizedFileURL
        var documents: [String: String] = [:]
        let enumerator = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.isRegularFileKey])
        while let url = enumerator?.nextObject() as? URL {
            guard url.pathExtension == "json" else { continue }
            let relative = url.path.replacingOccurrences(of: root.path + "/", with: "")
            documents[relative] = try String(contentsOf: url, encoding: .utf8)
        }
        if root.lastPathComponent == "schemas" {
            let verticalRoot = root.deletingLastPathComponent().appendingPathComponent("verticals")
            let verticalEnumerator = FileManager.default.enumerator(at: verticalRoot, includingPropertiesForKeys: [.isRegularFileKey])
            while let url = verticalEnumerator?.nextObject() as? URL {
                guard url.pathExtension == "json" else { continue }
                let relative = url.path.replacingOccurrences(of: verticalRoot.path + "/", with: "")
                documents["verticals/" + relative] = try String(contentsOf: url, encoding: .utf8)
            }
        }
        return try LCPValidator(schemaTexts: documents)
    }

    public func validate(_ schemaName: String, instance: String) throws {
        let normalized = normalize(schemaName)
        let fileName = schemaName.split(separator: "/").last.map(String.init) ?? schemaName
        guard let key = names[normalized]
            ?? schemas.keys.first(where: { Self.normalize($0) == normalized })
            ?? schemas.keys.first(where: { $0 == fileName || $0.hasSuffix("/" + fileName) }),
            let schema = schemas[key] else { throw LCPValidationError.unknownSchema(schemaName) }
        let result = try schema.validate(instance: instance)
        guard result.isValid else { throw LCPValidationError.invalid(schemaName, String(describing: result)) }
    }

    public func validateEnvelope(_ envelope: String) throws {
        try validate("schemas/envelope.json", instance: envelope)
        guard let root = try JSONSerialization.jsonObject(with: Data(envelope.utf8)) as? [String: Any], let lcp = root["lcp"] as? [String: Any], let message = lcp["message"] as? [String: Any], let type = message["type"] as? String, let payload = lcp["payload"] else { throw LCPValidationError.invalid("envelope", "missing message type or payload") }
        let payloadJSON = String(data: try JSONSerialization.data(withJSONObject: payload), encoding: .utf8)!
        try validate("schemas/\(type).json", instance: payloadJSON)
        try validateVerticalPolicy(type, payload: payload)
    }

    private func validateVerticalPolicy(_ type: String, payload: Any) throws {
        guard ["lead", "call", "post", "ping"].contains(type), let payloadObject = payload as? [String: Any], let attributes = payloadObject["attributes"] as? [String: Any] else { return }
        let vertical = type == "ping" ? payloadObject["vertical"] as? String : attributes["vertical"] as? String
        guard let vertical, !vertical.isEmpty else { return }
        guard let schema = documents[Self.normalize("verticals/\(vertical).json")] as? [String: Any] else {
            throw LCPValidationError.unknownSchema("verticals/\(vertical).json")
        }
        var verticalAttributes = attributes
        if type == "ping" {
            verticalAttributes["vertical"] = verticalAttributes["vertical"] ?? vertical
            if verticalAttributes["schema_version"] == nil,
               let properties = schema["properties"] as? [String: Any],
               let version = properties["schema_version"] as? [String: Any],
               let constant = version["const"] {
                verticalAttributes["schema_version"] = constant
            }
        }
        let verticalJSON = String(data: try JSONSerialization.data(withJSONObject: verticalAttributes), encoding: .utf8)!
        try validate("verticals/\(vertical).json", instance: verticalJSON)
        if type == "ping" { try validatePingSafe(vertical, value: attributes, schema: schema, path: "attributes") }
    }

    private func validatePingSafe(_ vertical: String, value: [String: Any], schema: [String: Any], path: String) throws {
        let properties = schema["properties"] as? [String: Any] ?? [:]
        for (name, child) in value {
            if path == "attributes" && (name == "vertical" || name == "schema_version") { continue }
            guard let definition = properties[name] as? [String: Any], definition["ping_safe"] as? Bool == true else {
                throw LCPValidationError.invalid("verticals/\(vertical).json", "\(path).\(name) is not tagged ping_safe: true")
            }
            if let childObject = child as? [String: Any], let childProperties = definition["properties"] as? [String: Any] {
                var childSchema = definition
                childSchema["properties"] = childProperties
                try validatePingSafe(vertical, value: childObject, schema: childSchema, path: "\(path).\(name)")
            }
        }
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
