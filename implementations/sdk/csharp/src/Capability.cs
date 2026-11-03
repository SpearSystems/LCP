using System.Text.Json.Nodes;

namespace LcpSdk;

/// <summary>
/// Receiver capability helpers for v1.1 slot fields (SPEC.md §8, LEP-0003).
/// The ping/bid/post payload objects are closed (additionalProperties: false),
/// so a schema-validating v1.0 receiver REJECTS slot fields rather than
/// ignoring them. Senders MUST only include them toward a receiver whose §8
/// lcp_versions advertisement parses to a full major.minor.patch element with
/// major == 1 and minor >= 1 — compared as parsed versions, never as a string
/// literal or prefix.
/// </summary>
public static class LcpCapability
{
    /// <summary>Payload fields introduced at v1.1 that require a v1.1 advertisement.</summary>
    public static readonly string[] SlotFields = { "slots_available", "slots_claimed", "contacted_hashes" };

    private const int MinSlotMinor = 1;

    /// <summary>
    /// §8 version-string membership: the gate opens only for a parseable full
    /// semver element with major == 1 and minor >= 1. Malformed or partial
    /// elements ("1.1", "v1.1.0", "1.1.0.1") never open the gate.
    /// </summary>
    public static bool ReceiverAdvertisesSlotFields(IEnumerable<string> advertisedLcpVersions)
    {
        foreach (var element in advertisedLcpVersions)
        {
            var parts = element.Split('.');
            if (parts.Length != 3) continue;
            var numeric = true;
            foreach (var part in parts)
                if (part.Length == 0 || part.Any(c => !char.IsAsciiDigit(c))) { numeric = false; break; }
            if (!numeric) continue;
            if (int.Parse(parts[0]) == 1 && int.Parse(parts[1]) >= MinSlotMinor) return true;
        }
        return false;
    }

    /// <summary>Slot fields carried by an envelope payload (incl. CONTACTED report details).</summary>
    public static List<string> SlotFieldsPresent(JsonNode? payload)
    {
        var present = new List<string>();
        if (payload is not JsonObject obj) return present;
        foreach (var field in SlotFields)
            if (obj.ContainsKey(field)) present.Add(field);
        if (obj["event"]?.GetValue<string>() == "CONTACTED" && obj["details"] is JsonObject details && details.ContainsKey("contacted_hashes"))
            present.Add("details.contacted_hashes");
        return present;
    }

    /// <summary>
    /// Errors when an envelope carries v1.1 slot fields but the receiver's
    /// advertisement does not support them (§8 gate; invalid example D). An
    /// empty list is capability-compatibility, not a schema verdict.
    /// </summary>
    public static List<string> SlotCapabilityErrors(JsonNode envelope, IEnumerable<string> advertisedLcpVersions)
    {
        var payload = envelope["lcp"]?["payload"];
        var present = SlotFieldsPresent(payload);
        if (present.Count == 0 || ReceiverAdvertisesSlotFields(advertisedLcpVersions)) return new List<string>();
        return present
            .Select(field => $"{field} requires the receiver to advertise a full-semver LCP version with major 1 and minor >= {MinSlotMinor} (SPEC.md §8 capability gate)")
            .ToList();
    }
}
