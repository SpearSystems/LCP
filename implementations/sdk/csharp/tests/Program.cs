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
Console.WriteLine("C# SDK HMAC and full JSON Schema vectors passed");
