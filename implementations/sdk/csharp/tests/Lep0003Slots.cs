using System.Text.Json.Nodes;
using LcpSdk;

// LEP-0003 SDK lane: contact-hash vectors + slot round-trip + §8 capability gate.
internal static class Lep0003Slots
{
    public static string Run(string repositoryRoot)
    {
        var suite = JsonNode.Parse(File.ReadAllText(Path.Combine(repositoryRoot, "test-vectors", "sdk", "contact-hash.json")))!;
        var vectors = suite["vectors"]!.AsArray();
        if (vectors.Count == 0) throw new Exception("contact-hash.json exposed no vectors");
        var failures = new List<string>();
        foreach (var node in vectors)
        {
            var vector = node!.AsObject();
            var secret = (string)vector["secret"]!;
            var preimage = (string)vector["preimage"]!;
            var domain = (string)vector["domain"]!;
            var pinned = (string)vector["digest"]!;
            var observed = LcpSigning.ContactHash(secret, preimage, domain);
            if (observed != pinned)
                failures.Add($"{(string)vector["name"]!} ({domain}): preimage \"{preimage}\" pinned {pinned} but C# SDK produced {observed}");
        }
        if (failures.Count > 0) throw new Exception("contact-hash vector reproduction failed:\n" + string.Join("\n", failures));

        // SPEC.md §9 preimage rules: phone verbatim; email trims and lowercases the domain only.
        if (LcpSigning.ContactHash("pair-secret-for-vectors-0123456789", "+1 (512) 555-0147") ==
            "c47aff331606437528b34b7f919eb37704e87ee35eed80e52cfa36826a4a2e12")
            throw new Exception("phone preimage must stay verbatim; separators must not be folded away");
        const string pinnedEmail = "d28da9b5424990cf7ac6000cf4e04537ca487929e71cef12f91191f29f78a6c4";
        if (LcpSigning.ContactHash("pair-secret-for-vectors-0123456789", "  Jordan@Example.COM  ", "email") != pinnedEmail)
            throw new Exception("email canonical must trim whitespace and ASCII-lowercase the domain");
        if (LcpSigning.ContactHash("pair-secret-for-vectors-0123456789", "jordan@example.com", "email") == pinnedEmail)
            throw new Exception("local-part bytes are verbatim: a casefolded local part must differ");
        if (LcpSigning.ContactHash("pair-secret-for-vectors-0123456789", "Jordan+crm@example.com", "email") == pinnedEmail)
            throw new Exception("plus-addresses are distinct under the §9 canonical");

        // §8 version-string membership.
        if (LcpCapability.ReceiverAdvertisesSlotFields(new[] { "1.0.0" })) throw new Exception("v1.0-only advertisement must not open the gate");
        if (!LcpCapability.ReceiverAdvertisesSlotFields(new[] { "1.0.0", "1.1.0" })) throw new Exception("1.1.0 must open the gate");
        if (!LcpCapability.ReceiverAdvertisesSlotFields(new[] { "1.10.0" })) throw new Exception("parsed minor 10 must open the gate");
        if (LcpCapability.ReceiverAdvertisesSlotFields(new[] { "2.0.0" })) throw new Exception("major != 1 must not open the gate");
        if (LcpCapability.ReceiverAdvertisesSlotFields(new[] { "1.1" })) throw new Exception("a non-semver literal must never open the gate");
        if (LcpCapability.ReceiverAdvertisesSlotFields(new[] { "v1.1.0", "1.1.0.1", "" })) throw new Exception("malformed elements must not open the gate");

        var validator = LcpSchemaValidator.FromDirectory(Path.Combine(repositoryRoot, "schemas"));
        var ping = JsonNode.Parse(File.ReadAllText(Path.Combine(repositoryRoot, "examples", "ping-slots.json")))!;
        var bid = JsonNode.Parse(File.ReadAllText(Path.Combine(repositoryRoot, "examples", "bid-slots.json")))!;
        var post = JsonNode.Parse(File.ReadAllText(Path.Combine(repositoryRoot, "examples", "post-contacted.json")))!;
        var contacted = JsonNode.Parse(File.ReadAllText(Path.Combine(repositoryRoot, "examples", "event-contacted.json")))!;

        // Slot-bearing ping/bid/post/event validate through the SDK schema path.
        foreach (var envelope in new[] { ping, bid, post, contacted }) validator.ValidateEnvelope(envelope);

        // The capability gate rejects them toward a v1.0-only receiver…
        foreach (var (name, envelope) in new (string, JsonNode)[] { ("ping", ping), ("bid", bid), ("post", post), ("event", contacted) })
        {
            try
            {
                validator.ValidateEnvelope(envelope, new[] { "1.0.0" });
                throw new Exception($"slot-bearing {name} was accepted for a v1.0-only receiver");
            }
            catch (ArgumentException error) when (error.Message.Contains("capability gate")) { }
        }
        // …and accepts them toward a v1.1 advertisement.
        foreach (var envelope in new[] { ping, bid, post, contacted }) validator.ValidateEnvelope(envelope, new[] { "1.0.0", "1.1.0" });

        return $"C# SDK LEP-0003 vectors and slot round-trip passed ({vectors.Count} contact-hash rows reproduced byte-exactly)";
    }
}
