# Ruby SDK interop probe for tools/sdk_interop_matrix.py (LEP-0002 step 1).
#
# Signs the fixed vector message through the SDK's own Signing.canonical_bytes
# + Signing.sign, then formats the SDK's generation expression
# (lib/lcp_sdk.rb: Time.now.utc.strftime("%Y-%m-%dT%H:%M:%SZ")) at a frozen
# sub-second instant — strftime drops the fraction, so Ruby is compliant today.
# The probe never asserts; the matrix owns the checks.

require "json"
require "digest"
require_relative "../lib/lcp_sdk"

index = ARGV.index("--vector")
vector_path = if index && ARGV[index + 1]
  ARGV[index + 1]
elsif ENV["LCP_VECTOR"] && !ENV["LCP_VECTOR"].empty?
  ENV["LCP_VECTOR"]
else
  directory = Dir.pwd
  loop do
    candidate = File.join(directory, "test-vectors", "sdk", "hmac.json")
    break candidate if File.exist?(candidate)
    parent = File.dirname(directory)
    abort "hmac.json vector not found (pass --vector)" if parent == directory
    directory = parent
  end
end

vector = JSON.parse(File.read(vector_path))
canonical = LcpSdk::Signing.canonical_bytes(vector["timestamp"], vector["idempotency_key"], vector["body_utf8"])
# Frozen instant 2026-08-15T10:20:00.123456789Z through the SDK's expression.
emitted = Time.at(1_786_789_200.123456789).utc.strftime("%Y-%m-%dT%H:%M:%SZ")

result = {
  "canonical_sha256" => Digest::SHA256.hexdigest(canonical),
  "emitted_timestamp" => emitted,
  "sdk" => "ruby",
  "signature_hex" => LcpSdk::Signing.sign(vector["secret"], vector["timestamp"], vector["idempotency_key"], vector["body_utf8"]),
}
json = JSON.generate(result)
# Logging hygiene: no payload prints on stdout; stderr + LCP_INTEROP_OUT.
$stderr.puts "LCP_INTEROP #{json}"
File.write(ENV["LCP_INTEROP_OUT"], "#{json}\n") if ENV["LCP_INTEROP_OUT"] && !ENV["LCP_INTEROP_OUT"].empty?
