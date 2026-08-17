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
