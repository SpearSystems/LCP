using System.Text;
using System.Text.Json.Nodes;
using LcpSdk;

var body = Encoding.UTF8.GetBytes("{\"hello\":\"world\"}");
var timestamp = "2026-08-15T10:20:00Z";
var signature = LcpSigning.SignHmac("sdk-shared-secret", timestamp, "sdk-vector-001", body);
const string expected = "50f90d29b46e92f257eae62c94a3d985bf9a925da03fee82e33c800fe54e7259";
if (signature != expected) throw new Exception($"Unexpected signature {signature}");
LcpSigning.VerifyHmac("sdk-shared-secret", signature, timestamp, "sdk-vector-001", body, TimeSpan.FromMinutes(5), DateTimeOffset.Parse("2026-08-15T10:20:01Z"));
try
{
    LcpSigning.VerifyHmac("sdk-shared-secret", signature, timestamp, "sdk-vector-001", Encoding.UTF8.GetBytes("tampered"), TimeSpan.FromMinutes(5), DateTimeOffset.Parse("2026-08-15T10:20:01Z"));
    throw new Exception("Tampered body was accepted");
}
catch (ArgumentException) { }
var repositoryRoot = Path.GetFullPath(Path.Combine(Environment.CurrentDirectory, "../../.."));
var schemaValidator = LcpSchemaValidator.FromDirectory(Path.Combine(repositoryRoot, "schemas"));
var leadFixture = JsonNode.Parse(File.ReadAllText(Path.Combine(repositoryRoot, "examples", "lead.json")))!;
schemaValidator.ValidateEnvelope(leadFixture);
var corpus = JsonNode.Parse(File.ReadAllText(Path.Combine(repositoryRoot, "test-vectors", "sdk", "validation-corpus.json")))!;
var corpusFailures = new List<string>();
foreach (var fixtureNode in corpus["fixtures"]!.AsArray())
{
    var fixture = fixtureNode!.AsObject();
    var id = (string)fixture["id"]!;
    var rule = (string)fixture["rule"]!;
    var expectPass = (string)fixture["expect"]! == "pass";
    var offerNode = fixture["offer"];
    var isOffer = offerNode != null;
    var document = isOffer ? offerNode : fixture["envelope"];
    var passed = true;
    try
    {
        if (isOffer) schemaValidator.ValidateOffer(document!);
        else schemaValidator.ValidateEnvelope(document!);
    }
    catch { passed = false; }
    if (passed != expectPass)
        corpusFailures.Add($"{id} ({rule}): expected pass={expectPass}");
}
if (corpusFailures.Count > 0)
    throw new Exception("Shared validation corpus mismatches: " + string.Join("; ", corpusFailures));
Console.WriteLine("C# SDK HMAC and full JSON Schema vectors passed");
Console.WriteLine("C# SDK shared validation corpus passed");
