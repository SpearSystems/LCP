require_relative "../lib/lcp_sdk"

body = '{"hello":"world"}'
signature = LcpSdk::Signing.sign("sdk-shared-secret", "2026-08-15T10:20:00Z", "sdk-vector-001", body)
expected = "50f90d29b46e92f257eae62c94a3d985bf9a925da03fee82e33c800fe54e7259"
raise signature unless signature == expected
LcpSdk::Signing.verify("sdk-shared-secret", signature, "2026-08-15T10:20:00Z", "sdk-vector-001", body, now: Time.utc(2026, 8, 15, 10, 20, 1))
root = File.expand_path("../../../..", __dir__)
validator = LcpSdk::SchemaValidator.from_directory(File.join(root, "schemas"))
validator.validate_envelope(JSON.parse(File.read(File.join(root, "examples", "lead.json"))))
corpus = JSON.parse(File.read(File.join(root, "test-vectors", "sdk", "validation-corpus.json")))
failures = []
corpus.fetch("fixtures").each do |fixture|
  is_offer = fixture.key?("offer")
  document = is_offer ? fixture["offer"] : fixture["envelope"]
  passed = begin
    is_offer ? validator.validate_offer(document) : validator.validate_envelope(document)
    true
  rescue StandardError
    false
  end
  failures << "#{fixture["id"]} (#{fixture["rule"]}): expected #{fixture["expect"]}" unless passed == (fixture["expect"] == "pass")
end
raise "Shared validation corpus mismatches: #{failures.join(", ")}" unless failures.empty?
puts "Ruby SDK HMAC and full JSON Schema vectors passed"
puts "Ruby SDK shared validation corpus passed"
require_relative "negative_vector_test"
puts "Ruby SDK negative security vectors passed (#{LcpSdk::NegativeVectorTest.run!} vectors executed)"
require_relative "lep0003_slots_test"
puts "Ruby SDK LEP-0003 vectors and slot round-trip passed (#{LcpSdk::Lep0003Slots.run!} contact-hash rows reproduced byte-exactly)"

# LEP-0001 W2-01 batch submission: 207 is a successful response, receipts come
# back in input order, and a batch over the cap is refused before any request.
require "socket"
server = TCPServer.new("127.0.0.1", 0)
port = server.addr[1]
captured = nil
thread = Thread.new do
  socket = server.accept
  raw = +""
  raw << socket.readpartial(4096) until raw.include?("\r\n\r\n")
  headers, rest = raw.split("\r\n\r\n", 2)
  length = headers[/content-length:\s*(\d+)/i, 1].to_i
  request_body = rest.to_s
  request_body << socket.readpartial(length - request_body.bytesize) while request_body.bytesize < length
  captured = request_body
  response = '{"request_id":"req-batch-1","summary":{"total":2,"accepted":1,"duplicate":0,"rejected":1,"error":0},"results":[{"item_id":"item_b","status":"accepted","lead_id":"lead_b"},{"item_id":"item_a","status":"rejected","error":{"code":"LCP-100","message":"invalid lead"}}]}'
  socket.write("HTTP/1.1 207 Multi-Status\r\nContent-Type: application/json\r\nContent-Length: #{response.bytesize}\r\nConnection: close\r\n\r\n#{response}")
  socket.close
end
client = LcpSdk::Client.new("http://127.0.0.1:#{port}", max_retries: 0)
results = client.submit_leads_batch(
  [
    { "item_id" => "item_a", "idempotency_key" => "batch-a", "payload" => { "lead_id" => "lead_a" } },
    { "item_id" => "item_b", "idempotency_key" => "batch-b", "payload" => { "lead_id" => "lead_b" } },
  ],
  request_id: "req-batch-1",
)
thread.join
raise "unexpected batch order: #{results.map { |result| result["item_id"] }.join(",")}" unless results.map { |result| result["item_id"] } == %w[item_a item_b]
raise "per-item error lost" unless results[0]["status"] == "rejected" && results[0]["error"]["code"] == "LCP-100"
raise "accepted receipt lost" unless results[1]["status"] == "accepted"
sent = JSON.parse(captured)
raise "batch wire shape lost" unless sent["request_id"] == "req-batch-1" && sent["items"][0]["payload"]["lead_id"] == "lead_a"
begin
  client.submit_leads_batch(Array.new(101) { |index| { "item_id" => "item_#{index}", "idempotency_key" => "key_#{index}", "payload" => {} } })
  raise "batch over the cap was accepted"
rescue ArgumentError
end
puts "Ruby SDK batch submission passed"

# LEP-0005 history read: only supplied, non-empty filters reach the query, the
# decoded body comes back unchanged, and a 4xx raises through the normal path.
history_server = TCPServer.new("127.0.0.1", 0)
history_port = history_server.addr[1]
history_target = nil
history_thread = Thread.new do
  socket = history_server.accept
  raw = +""
  raw << socket.readpartial(4096) until raw.include?("\r\n\r\n")
  history_target = raw[/\AGET (\S+) HTTP/, 1]
  response = '{"requests":[{"request_id":"req-hist-1","message_type":"lead","direction":"inbound","status":"OPEN","created_at":"2026-11-01T00:00:00Z"}]}'
  socket.write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: #{response.bytesize}\r\nConnection: close\r\n\r\n#{response}")
  socket.close
end
history_client = LcpSdk::Client.new("http://127.0.0.1:#{history_port}", max_retries: 0)
history_body = history_client.request_history(limit: 2, cursor: "", status: "OPEN", to: "2026-11-01T00:00:00Z")
history_thread.join
unless history_body["requests"][0]["request_id"] == "req-hist-1" && history_body["requests"][0]["status"] == "OPEN"
  raise "history row lost"
end
unless history_target == "/v1/lcp/history/requests?limit=2&status=OPEN&to=2026-11-01T00%3A00%3A00Z"
  raise "history query: #{history_target}"
end

history_server = TCPServer.new("127.0.0.1", 0)
history_port = history_server.addr[1]
history_thread = Thread.new do
  socket = history_server.accept
  raw = +""
  raw << socket.readpartial(4096) until raw.include?("\r\n\r\n")
  response = '{"errors":[{"code":"LCP-001","message":"limit out of range"}]}'
  socket.write("HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: #{response.bytesize}\r\nConnection: close\r\n\r\n#{response}")
  socket.close
end
begin
  LcpSdk::Client.new("http://127.0.0.1:#{history_port}", max_retries: 0).request_history(limit: 500)
  raise "history 4xx was accepted"
rescue RuntimeError => history_error
  raise history_error unless history_error.message.start_with?("LCP HTTP 400")
end
history_thread.join
puts "Ruby SDK history read passed"

# Schema fetch: a slash-namespaced name keeps its separators, every other
# character is encoded, and the decoded body comes back unchanged.
schema_server = TCPServer.new("127.0.0.1", 0)
schema_port = schema_server.addr[1]
schema_target = nil
schema_thread = Thread.new do
  socket = schema_server.accept
  raw = +""
  raw << socket.readpartial(4096) until raw.include?("\r\n\r\n")
  schema_target = raw.lines.first.split(" ")[1]
  response = '{"title":"Lead","type":"object"}'
  socket.write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: #{response.bytesize}\r\nConnection: close\r\n\r\n#{response}")
  socket.close
end
schema_body = LcpSdk::Client.new("http://127.0.0.1:#{schema_port}", max_retries: 0).get_schema("verticals/mortgage?x=1")
schema_thread.join
raise "schema body lost" unless schema_body["title"] == "Lead" && schema_body["type"] == "object"
raise "schema path: #{schema_target}" unless schema_target == "/v1/lcp/schemas/verticals/mortgage%3Fx%3D1"
puts "Ruby SDK schema fetch passed"
