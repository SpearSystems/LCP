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
      OpenSSL::HMAC.hexdigest("SHA256", secret, canonical_bytes(timestamp, idempotency_key, body))
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

  class Client
    def initialize(endpoint, sender_id: nil, api_key: nil, hmac_secret: nil, max_retries: 2)
      @endpoint = endpoint.delete_suffix("/"); @sender_id = sender_id; @api_key = api_key; @hmac_secret = hmac_secret; @max_retries = max_retries
    end

    def request(method, path, payload: nil, test: false)
      body = payload.nil? ? "" : JSON.generate(payload)
      key = payload&.dig("lcp", "message", "idempotency_key")
      (0..@max_retries).each do |attempt|
        uri = URI.join("#{@endpoint}/", path.delete_prefix("/")); request = Net::HTTP.const_get(method.capitalize).new(uri)
        request["Content-Type"] = "application/json"; request["Accept"] = "application/json"; request["X-LCP-Sender-Id"] = @sender_id if @sender_id; request["Authorization"] = "Bearer #{@api_key}" if @api_key; request["X-LCP-Idempotency-Key"] = key if key; request["X-LCP-Test"] = "true" if test
        if @hmac_secret; timestamp = Time.now.utc.strftime("%Y-%m-%dT%H:%M:%SZ"); request["X-LCP-Timestamp"] = timestamp; request["X-LCP-Signature"] = Signing.sign(@hmac_secret, timestamp, key, body); end
        request.body = body unless body.empty?
        response = Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == "https") { |http| http.request(request) }
        data = response.body.nil? || response.body.empty? ? {} : JSON.parse(response.body)
        return data if response.is_a?(Net::HTTPSuccess)
        raise "LCP HTTP #{response.code}: #{data}" unless [429, 500, 502, 503, 504].include?(response.code.to_i) && attempt < @max_retries
        sleep(2**attempt)
      end
      raise "LCP request failed"
    end

    def submit_lead(envelope, test: false); LcpSdk.validate_envelope(envelope); request("POST", "/v1/lcp/leads", payload: envelope, test: test); end
    def submit_call(envelope, test: false); LcpSdk.validate_envelope(envelope); request("POST", "/v1/lcp/calls", payload: envelope, test: test); end
    def submit_bid(envelope, test: false); LcpSdk.validate_envelope(envelope); request("POST", "/v1/lcp/bids", payload: envelope, test: test); end
    def query_lead_status(id); request("GET", "/v1/lcp/leads/#{URI.encode_www_form_component(id)}"); end
    def get_capabilities; request("GET", "/v1/lcp/capabilities"); end
    def list_offers(vertical = nil); request("GET", "/v1/lcp/offers#{vertical ? "?vertical=#{URI.encode_www_form_component(vertical)}" : ""}"); end
  end
end
