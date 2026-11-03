using System.Net;
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
Console.WriteLine($"C# SDK negative security vectors passed ({NegativeVectors.Run(repositoryRoot)} vectors executed)");
Console.WriteLine(Lep0003Slots.Run(repositoryRoot));

// LEP-0001 W2-01 batch submission: 207 is a successful response, receipts come
// back in input order, and a batch over the cap is refused before any request.
var batchRequests = new List<string>();
using (var http = new HttpClient(new StubBatchHandler(batchRequests)))
{
    var client = new LcpClient(http, "http://stub.invalid");
    var results = await client.SubmitLeadsBatchAsync(new[]
    {
        new LcpBatchItem("item_a", "batch-a", new Dictionary<string, object> { ["lead_id"] = "lead_a" }),
        new LcpBatchItem("item_b", "batch-b", new Dictionary<string, object> { ["lead_id"] = "lead_b" }),
    }, "req-batch-1");
    var batchIds = results.Select(result => result.GetProperty("item_id").GetString()).ToArray();
    if (!batchIds.SequenceEqual(new[] { "item_a", "item_b" })) throw new Exception($"Unexpected batch order: {string.Join(",", batchIds)}");
    if (results[0].GetProperty("status").GetString() != "rejected") throw new Exception("Rejected receipt lost");
    if (results[0].GetProperty("error").GetProperty("code").GetString() != "LCP-100") throw new Exception("Per-item error object lost");
    if (results[1].GetProperty("status").GetString() != "accepted") throw new Exception("Accepted receipt lost");
    var sent = JsonNode.Parse(batchRequests.Single())!;
    if ((string)sent["request_id"]! != "req-batch-1") throw new Exception("request_id not sent");
    if ((string)sent["items"]![0]!["payload"]!["lead_id"]! != "lead_a") throw new Exception("Lead payload not sent");
    var before = batchRequests.Count;
    var overCap = Enumerable.Range(0, 101).Select(index => new LcpBatchItem($"item_{index}", $"key_{index}", new Dictionary<string, object>())).ToArray();
    try
    {
        await client.SubmitLeadsBatchAsync(overCap);
        throw new Exception("Batch over the cap was accepted");
    }
    catch (ArgumentException) { }
    if (batchRequests.Count != before) throw new Exception("Cap check must run before sending");
}
Console.WriteLine("C# SDK batch submission passed");

sealed class StubBatchHandler(List<string> requests) : HttpMessageHandler
{
    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        requests.Add(await request.Content!.ReadAsStringAsync(cancellationToken));
        return new HttpResponseMessage(HttpStatusCode.MultiStatus)
        {
            Content = new StringContent(
                "{\"request_id\":\"req-batch-1\",\"summary\":{\"total\":2,\"accepted\":1,\"duplicate\":0,\"rejected\":1,\"error\":0},\"results\":["
                + "{\"item_id\":\"item_b\",\"status\":\"accepted\",\"lead_id\":\"lead_b\"},"
                + "{\"item_id\":\"item_a\",\"status\":\"rejected\",\"error\":{\"code\":\"LCP-100\",\"message\":\"invalid lead\"}}]}",
                Encoding.UTF8,
                "application/json"),
        };
    }
}
