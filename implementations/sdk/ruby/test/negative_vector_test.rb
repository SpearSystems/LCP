# frozen_string_literal: true

# Ruby SDK must refuse every attack in test-vectors/sdk/negative/.
# Python reference: implementations/sdk/python/tests/test_negative_vectors.py
require "json"
require "time"
require_relative "../lib/lcp_sdk"

module LcpSdk
  module NegativeVectorTest
    ROOT = File.expand_path("../../../..", __dir__)
    NEGATIVE_DIR = File.join(ROOT, "test-vectors", "sdk", "negative")

    # Returns the number of vectors actually executed; raises on any mismatch.
    def self.run!
      files = Dir.glob(File.join(NEGATIVE_DIR, "*.json")).sort
      raise "no vector files found under #{NEGATIVE_DIR}" if files.empty?

      validator = SchemaValidator.from_directory(File.join(ROOT, "schemas"))
      mismatches = []
      gaps = []
      ran = 0
      files.each do |file|
        suite = JSON.parse(File.read(file))
        suite.fetch("vectors", []).each do |vector|
          if vector["sdk_exercisable"] == false
            gaps << "#{vector.fetch("id")}: #{vector["gap_note"]}"
            next
          end
          outcome, detail = execute(validator, vector.fetch("inputs"), vector.fetch("check"))
          ran += 1
          expected = vector.dig("expect", "outcome")
          next if outcome == expected

          mismatches << "#{vector["id"]} (#{vector["check"]}): expected #{expected}, " \
                        "got #{outcome} - #{detail} - #{vector["description"]}"
        end
      end
      gaps.each { |gap| warn "GAP (reported, never silently passed): #{gap}" }
      raise "Negative vector mismatches:\n#{mismatches.join("\n")}" unless mismatches.empty?
      raise "no negative vector was actually executed" if ran.zero?

      ran
    end

    def self.execute(validator, inputs, check)
      case check
      when "verify_hmac", "verify_raw_body"
        body = check == "verify_hmac" ? inputs["body"] : inputs["verify_body"]
        attempt do
          Signing.verify(
            inputs["secret"], inputs["signature"], inputs["timestamp"],
            inputs["idempotency_key"], body,
            max_skew_seconds: inputs["replay_window_seconds"],
            now: Time.iso8601(inputs["now"])
          )
        end
      when "envelope_validate"
        attempt { validator.validate_envelope(inputs["envelope"]) }
      when "offer_validate"
        attempt { validator.validate_offer(inputs["offer"]) }
      else
        # Exercisable but unmapped check: fail loudly, never skip.
        raise "no runner adapter for check #{check.inspect}"
      end
    end

    def self.attempt
      yield
      ["accept", ""]
    rescue StandardError => e
      ["reject", "#{e.class}: #{e.message}"]
    end
  end
end

if __FILE__ == $PROGRAM_NAME
  count = LcpSdk::NegativeVectorTest.run!
  puts "Ruby SDK negative security vectors passed (#{count} vectors executed)"
end
