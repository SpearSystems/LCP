import Foundation

/// Receiver capability helpers for v1.1 slot fields (SPEC.md §8, LEP-0003).
///
/// The ping/bid/post payload objects are closed (additionalProperties: false),
/// so a schema-validating v1.0 receiver REJECTS slot fields rather than
/// ignoring them. Senders MUST only include them toward a receiver whose §8
/// lcp_versions advertisement parses to a full major.minor.patch element with
/// major == 1 and minor >= 1 — compared as parsed versions, never as a string
/// literal or prefix.
public enum LCPCapability {
    /// Payload fields introduced at v1.1 that require a v1.1 advertisement.
    public static let slotFields = ["slots_available", "slots_claimed", "contacted_hashes"]

    private static let minSlotMinor = 1

    /// §8 version-string membership: the gate opens only for a parseable full
    /// semver element with major == 1 and minor >= 1. Malformed or partial
    /// elements ("1.1", "v1.1.0", "1.1.0.1") never open the gate.
    public static func receiverAdvertisesSlotFields(_ advertisedLcpVersions: [String]) -> Bool {
        for element in advertisedLcpVersions {
            let parts = element.split(separator: ".", omittingEmptySubsequences: false)
            guard parts.count == 3, parts.allSatisfy({ !$0.isEmpty && $0.allSatisfy(\.isNumber) }) else { continue }
            guard let major = Int(parts[0]), let minor = Int(parts[1]) else { continue }
            if major == 1 && minor >= minSlotMinor { return true }
        }
        return false
    }

    /// Slot fields carried by an envelope payload (incl. CONTACTED report details).
    public static func slotFieldsPresent(_ payload: Any?) -> [String] {
        guard let payload = payload as? [String: Any] else { return [] }
        var present = slotFields.filter { payload[$0] != nil }
        if (payload["event"] as? String) == "CONTACTED",
           let details = payload["details"] as? [String: Any],
           details["contacted_hashes"] != nil {
            present.append("details.contacted_hashes")
        }
        return present
    }

    /// Errors when an envelope carries v1.1 slot fields but the receiver's
    /// advertisement does not support them (§8 gate; invalid example D). An
    /// empty array is capability-compatibility, not a schema verdict.
    public static func slotCapabilityErrors(envelope: [String: Any], advertisedLcpVersions: [String]) -> [String] {
        let payload = (envelope["lcp"] as? [String: Any])?["payload"]
        let present = slotFieldsPresent(payload)
        if present.isEmpty || receiverAdvertisesSlotFields(advertisedLcpVersions) { return [] }
        return present.map { field in
            "\(field) requires the receiver to advertise a full-semver LCP version with major 1 and minor >= \(minSlotMinor) (SPEC.md §8 capability gate)"
        }
    }
}
