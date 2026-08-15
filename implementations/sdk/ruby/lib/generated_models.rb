# GENERATED FROM schemas/ — do not edit manually.
module LcpSdk
  MessageModel = Struct.new(:id, :type, :timestamp, :sender_id, :receiver_id, :correlation_id, :idempotency_key, :test, :security, keyword_init: true)
  EnvelopeModel = Struct.new(:version, :message, :payload, keyword_init: true)
  LeadPayload = Struct.new(:lead_id, :status, :channel, :consumer, :location, :attributes, :external_id, :submitted_at, :compliance, :provenance, :exclusivity, :contact_window, :lead_quality, :expiry, :attachments, keyword_init: true)
  CallPayload = Struct.new(:lead_id, :status, :channel, :consumer, :location, :call, :external_id, :submitted_at, :compliance, :provenance, :attributes, :expiry, :exclusivity, :attachments, keyword_init: true)
  PingPayload = Struct.new(:ping_id, :lead_reference, :phone_hash, :country_code, :vertical, :floor_price_cents, :currency, :publisher_id, :offer_id, :email_hash, :attributes, keyword_init: true)
  PostPayload = Struct.new(:lead_id, :delivered_at, :price_cents, :currency, :buyer_id, :consumer, :location, :attributes, :submitted_at, :offer_id, :buyer_reference, :pricing, :compliance, :provenance, :call, :attachments, keyword_init: true)
  BidPayload = Struct.new(:ping_id, :decision, :bid_price_cents, :currency, :estimated_contact_seconds, :buyer_reference, :reject_reason, :capacity_remaining, keyword_init: true)
  AckPayload = Struct.new(:original_message_id, :status, :errors, :lead_id, :request_id, :rejection_reason, keyword_init: true)
  EventPayload = Struct.new(:lead_id, :event, :timestamp, :details, :external_reference, keyword_init: true)
  OfferModel = Struct.new(:offer_id, :buyer_id, :vertical, :countries, :floor_price_cents, :currency, :tenant_id, :active, :routing_mode, :schema_version, :extensions, :allowed_publisher_ids, :allowed_brand_ids, :attribute_equals, :attribute_in, :monthly_minimum_payable, :monthly_maximum_payable, :monthly_quota_timezone, :monthly_quota_policy, :payable_rules, :call_routing_mode, :connect_timeout_seconds, keyword_init: true)
end
