# frozen_string_literal: true

# LEP-0003 SDK lane: contact-hash vectors, slot round-trip, §8 capability gate.
# Executed from test/shared_vector_test.rb (the script CI runs).

require "json"
require_relative "../lib/lcp_sdk"

module LcpSdk
  module Lep0003Slots
    ROOT = File.expand_path("../../../..", __dir__)

    def self.run!
      vectors = JSON.parse(File.read(File.join(ROOT, "test-vectors", "sdk", "contact-hash.json")))["vectors"]
      raise "contact-hash.json exposed no vectors" if vectors.empty?

      failures = vectors.map do |vector|
        observed = LcpSdk::Signing.contact_hash(vector["secret"], vector["preimage"], vector["domain"])
        next nil if observed == vector["digest"]

        "#{vector["name"]} (#{vector["domain"]}): preimage #{vector["preimage"].inspect} pinned " \
          "#{vector["digest"]} but Ruby SDK produced #{observed}"
      end.compact
      raise "contact-hash vector reproduction failed:\n#{failures.join("\n")}" unless failures.empty?

      # SPEC.md §9 preimage rules: phone verbatim; email trims and lowercases the domain only.
      raise "phone preimage must stay verbatim" if LcpSdk::Signing.contact_hash("pair-secret-for-vectors-0123456789", "+1 (512) 555-0147") ==
        "c47aff331606437528b34b7f919eb37704e87ee35eed80e52cfa36826a4a2e12"
      pinned_email = "d28da9b5424990cf7ac6000cf4e04537ca487929e71cef12f91191f29f78a6c4"
      raise "email canonical mismatch" unless LcpSdk::Signing.contact_hash("pair-secret-for-vectors-0123456789", "  Jordan@Example.COM  ", "email") == pinned_email
      raise "local-part bytes are verbatim" if LcpSdk::Signing.contact_hash("pair-secret-for-vectors-0123456789", "jordan@example.com", "email") == pinned_email
      raise "plus-addresses are distinct" if LcpSdk::Signing.contact_hash("pair-secret-for-vectors-0123456789", "Jordan+crm@example.com", "email") == pinned_email

      # §8 version-string membership.
      raise "v1.0-only must not open the gate" if LcpSdk.receiver_advertises_slot_fields(["1.0.0"])
      raise "1.1.0 must open the gate" unless LcpSdk.receiver_advertises_slot_fields(["1.0.0", "1.1.0"])
      raise "parsed minor 10 must open the gate" unless LcpSdk.receiver_advertises_slot_fields(["1.10.0"])
      raise "major != 1 must not open the gate" if LcpSdk.receiver_advertises_slot_fields(["2.0.0"])
      raise "literals never open the gate" if LcpSdk.receiver_advertises_slot_fields(["1.1"])
      raise "malformed elements never open the gate" if LcpSdk.receiver_advertises_slot_fields(["v1.1.0", "1.1.0.1", ""])

      validator = LcpSdk::SchemaValidator.from_directory(File.join(ROOT, "schemas"))
      envelopes = %w[ping-slots bid-slots post-contacted event-contacted].to_h do |name|
        [name, JSON.parse(File.read(File.join(ROOT, "examples", "#{name}.json")))]
      end

      # Slot-bearing ping/bid/post/event validate through the SDK schema path.
      envelopes.each_value { |envelope| validator.validate_envelope(envelope) }
      # The capability gate rejects them toward a v1.0-only receiver…
      envelopes.each do |name, envelope|
        begin
          validator.validate_envelope(envelope, receiver_advertised_lcp_versions: ["1.0.0"])
          raise "slot-bearing #{name} was accepted for a v1.0-only receiver"
        rescue LcpSdk::SchemaValidationError => error
          raise "unexpected rejection for #{name}: #{error.message}" unless error.message.include?("capability gate")
        end
      end
      # …and accepts them toward a v1.1 advertisement.
      envelopes.each_value { |envelope| validator.validate_envelope(envelope, receiver_advertised_lcp_versions: %w[1.0.0 1.1.0]) }

      vectors.size
    end
  end
end
