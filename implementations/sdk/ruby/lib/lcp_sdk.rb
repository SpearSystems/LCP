# frozen_string_literal: true

require "json"
require "openssl"
require "securerandom"
require "time"
require "net/http"
require "uri"
require_relative "schema_validator"
require_relative "generated_models"

module LcpSdk
  Message = Struct.new(:id, :type, :timestamp, :sender_id, :receiver_id, :correlation_id, :idempotency_key, :test, keyword_init: true)

  module Signing
    module_function

    def canonical_bytes(timestamp, idempotency_key, body)
      "#{timestamp}\n#{idempotency_key || ""}\n".b + body.b
    end

    def sign(secret, timestamp, idempotency_key, body)
      hmac_hex(secret, canonical_bytes(timestamp, idempotency_key, body))
    end

    # The single HMAC-SHA256 → lowercase-hex construction in this SDK (SPEC.md §9).
    # Signing and contact hashes share it; nothing else computes a keyed digest.
    def hmac_hex(secret, data)
      OpenSSL::HMAC.hexdigest("SHA256", secret, data)
    end

    CONTACT_HASH_DOMAINS = ["phone", "email"].freeze

    # SPEC.md §9 pinned contact preimage. Phone: the wire value verbatim,
    # byte-for-byte — no folding, stripping, or reformatting (the reference
    # platform's phone_hash builder is the executed baseline). Email: the §9
    # pinned candidate canonical — trim surrounding whitespace, ASCII-lowercase
    # the domain, keep the local-part bytes verbatim, plus-addresses distinct.
    # Email-form contacted_hashes ENTRIES stay emission-gated until #153 T9
    # executed vectors publish; this is the pinned compute rule every
    # contact-hash vector row reproduces under.
    def normalize_contact(value, domain = "phone")
      case domain
      when "phone" then value
      when "email"
        trimmed = value.strip
        separator = trimmed.rindex("@")
        raise ArgumentError, "email contact preimage must contain '@'" unless separator
        local = trimmed[0...separator]
        host = trimmed[(separator + 1)..].chars.map { |c| c =~ /\A[A-Z]\z/ ? c.downcase : c }.join
        "#{local}@#{host}"
      else
        raise ArgumentError, "unknown contact domain #{domain.inspect} (expected phone or email)"
      end
    end

    # Per-pair contact hash (SPEC.md §9, LEP-0003 §3): lowercase-hex
    # HMAC-SHA256(pair_secret, normalized_preimage) keyed with the secret
    # shared with the message's receiver. phone_hash, email_hash, and
    # contacted_hashes entries all ride this one construction — never an
    # unsalted digest, never SHA-1.
    def contact_hash(secret, value, domain = "phone")
      hmac_hex(secret, normalize_contact(value, domain))
    end

    def verify(secret, signature, timestamp, idempotency_key, body, max_skew_seconds: 300, now: Time.now.utc)
      signed_at = Time.iso8601(timestamp)
      raise ArgumentError, "timestamp outside replay window" if (now - signed_at).abs > max_skew_seconds
      expected = sign(secret, timestamp, idempotency_key, body)
      actual = signature.downcase
      raise ArgumentError, "invalid signature" unless expected.bytesize == actual.bytesize && secure_equal?(expected, actual)
      true
    end

    def secure_equal?(left, right)
      difference = 0
      left.bytes.zip(right.bytes) { |a, b| difference |= a ^ b }
      difference.zero?
    end

    def verify_request(secret, headers, raw_body, max_skew_seconds: 300, now: Time.now.utc)
      normalized = headers.to_h.transform_keys(&:downcase)
      required = %w[x-lcp-signature x-lcp-timestamp x-lcp-idempotency-key]
      missing = required.reject { |key| normalized[key] && !normalized[key].empty? }
      raise ArgumentError, "missing required LCP headers: #{missing.join(", ")}" unless missing.empty?
      verify(secret, normalized["x-lcp-signature"], normalized["x-lcp-timestamp"], normalized["x-lcp-idempotency-key"], raw_body, max_skew_seconds: max_skew_seconds, now: now)
    end
  end

  module_function

  def build_envelope(type, sender_id, receiver_id, payload, test: false)
    id = SecureRandom.uuid
    { "lcp" => { "version" => "1.0.0", "message" => { "id" => id, "type" => type, "timestamp" => Time.now.utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "sender_id" => sender_id, "receiver_id" => receiver_id, "correlation_id" => nil, "idempotency_key" => "#{sender_id}-#{type}-#{SecureRandom.hex(16)}", "test" => test }, "payload" => payload } }
  end

  def validate_envelope(envelope)
    message = envelope.dig("lcp", "message")
    raise ArgumentError, "invalid LCP envelope" unless envelope.dig("lcp", "version").is_a?(String) && message.is_a?(Hash)
    %w[id type timestamp sender_id receiver_id idempotency_key].each { |key| raise ArgumentError, "missing lcp.message.#{key}" unless message[key].is_a?(String) && !message[key].empty? }
    raise ArgumentError, "missing lcp.payload" unless envelope.dig("lcp").key?("payload")
    true
  end

  # SPEC.md §8 capability gate (LEP-0003): v1.1 slot fields are
  # capability-gated, never ignore-gated — the ping/bid/post objects are
  # closed, so a v1.0 schema-validating receiver REJECTS the fields. Senders
  # MUST only include them toward a receiver whose lcp_versions advertisement
  # parses to a full major.minor.patch element with major == 1 and minor >= 1
  # (never a string literal or prefix).
  SLOT_FIELDS = ["slots_available", "slots_claimed", "contacted_hashes"].freeze

  def receiver_advertises_slot_fields(advertised_lcp_versions)
    Array(advertised_lcp_versions).any? do |element|
      parts = element.to_s.split(".")
      next false unless parts.size == 3 && parts.all? { |part| !part.empty? && part.match?(/\A\d+\z/) }
      parts[0].to_i == 1 && parts[1].to_i >= 1
    end
  end

  def slot_fields_present(payload)
    return [] unless payload.is_a?(Hash)
    present = SLOT_FIELDS.select { |field| payload.key?(field) }
    if payload["event"] == "CONTACTED" && payload["details"].is_a?(Hash) && payload["details"].key?("contacted_hashes")
      present = present + ["details.contacted_hashes"]
    end
    present
  end

  # Errors when an envelope carries v1.1 slot fields but the receiver's
  # advertisement does not support them (§8 gate; invalid example D). An empty
  # list is capability-compatibility, not a schema verdict.
  def slot_capability_errors(envelope, advertised_lcp_versions)
    payload = envelope.dig("lcp", "payload")
    present = slot_fields_present(payload)
    return [] if present.empty? || receiver_advertises_slot_fields(advertised_lcp_versions)
    present.map { |field| "#{field} requires the receiver to advertise a full-semver LCP version with major 1 and minor >= 1 (SPEC.md §8 capability gate)" }
  end

  class Client
    def initialize(endpoint, sender_id: nil, api_key: nil, hmac_secret: nil, max_retries: 2)
      @endpoint = endpoint.delete_suffix("/"); @sender_id = sender_id; @api_key = api_key; @hmac_secret = hmac_secret; @max_retries = max_retries
    end

    def request(method, path, payload: nil, test: false, max_retries: @max_retries)
      body = payload.nil? ? "" : JSON.generate(payload)
      key = payload&.dig("lcp", "message", "idempotency_key")
      (0..max_retries).each do |attempt|
        uri = URI.join("#{@endpoint}/", path.delete_prefix("/")); request = Net::HTTP.const_get(method.capitalize).new(uri)
        request["Content-Type"] = "application/json"; request["Accept"] = "application/json"; request["X-LCP-Sender-Id"] = @sender_id if @sender_id; request["Authorization"] = "Bearer #{@api_key}" if @api_key; request["X-LCP-Idempotency-Key"] = key if key; request["X-LCP-Test"] = "true" if test
        if @hmac_secret; timestamp = Time.now.utc.strftime("%Y-%m-%dT%H:%M:%SZ"); request["X-LCP-Timestamp"] = timestamp; request["X-LCP-Signature"] = Signing.sign(@hmac_secret, timestamp, key, body); end
        request.body = body unless body.empty?
        response = Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == "https") { |http| http.request(request) }
        data = response.body.nil? || response.body.empty? ? {} : JSON.parse(response.body)
        return data if response.is_a?(Net::HTTPSuccess)
        raise "LCP HTTP #{response.code}: #{data}" unless [429, 500, 502, 503, 504].include?(response.code.to_i) && attempt < max_retries
        sleep(2**attempt)
      end
      raise "LCP request failed"
    end

    def submit_lead(envelope, test: false); LcpSdk.validate_envelope(envelope); request("POST", "/v1/lcp/leads", payload: envelope, test: test); end
    def submit_call(envelope, test: false); LcpSdk.validate_envelope(envelope); request("POST", "/v1/lcp/calls", payload: envelope, test: test); end
    def submit_bid(envelope, test: false); LcpSdk.validate_envelope(envelope); request("POST", "/v1/lcp/bids", payload: envelope, test: test); end

    # Submit up to MAX_BATCH_ITEMS lead items in one request (LEP-0001 W2-01)
    # and return the per-item receipts in input order. HTTP 207 partial success
    # is a successful response; per-item error objects are preserved. The batch
    # is never retried as a whole — retrying only rejected/error receipts is the
    # caller's job.
    MAX_BATCH_ITEMS = 100

    def submit_leads_batch(items, request_id: nil)
      unless (1..MAX_BATCH_ITEMS).cover?(items.length)
        raise ArgumentError, "batch supports 1..#{MAX_BATCH_ITEMS} items, got #{items.length}"
      end
      response = request("POST", "/v1/lcp/leads/batch", payload: { "request_id" => request_id || SecureRandom.uuid, "items" => items }, max_retries: 0)
      results = response["results"]
      raise "batch response missing results" unless results.is_a?(Array)
      order = items.each_with_index.to_h { |item, index| [item["item_id"], index] }
      results.each_with_index.sort_by { |result, index| [order.fetch(result["item_id"], items.length), index] }.map(&:first)
    end
    def query_lead_status(id); request("GET", "/v1/lcp/leads/#{URI.encode_www_form_component(id)}"); end
    def get_capabilities; request("GET", "/v1/lcp/capabilities"); end
    def list_offers(vertical = nil); request("GET", "/v1/lcp/offers#{vertical ? "?vertical=#{URI.encode_www_form_component(vertical)}" : ""}"); end

    # LEP-0005 history read (GET /v1/lcp/history/requests, history:read). Only
    # supplied, non-empty filters reach the query string — a blank filter would
    # widen the page instead of narrowing it. Rows are scoped server-side to the
    # credential's tenant, so there is no tenant argument. Returns the decoded
    # body unchanged.
    HISTORY_PARAMS = %w[limit cursor request_id status vertical caller_phone_hash from to].freeze

    def request_history(**params)
      query = HISTORY_PARAMS.filter_map do |name|
        value = params[name.to_sym]
        next if value.nil? || value.to_s.empty?
        "#{name}=#{URI.encode_www_form_component(value.to_s)}"
      end
      request("GET", "/v1/lcp/history/requests#{query.empty? ? "" : "?#{query.join("&")}"}")
    end
  end
end
