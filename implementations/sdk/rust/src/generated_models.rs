// GENERATED FROM schemas/ — do not edit manually.
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Serialize, Deserialize)]
pub struct MessageModel { pub id: String, #[serde(rename = "type")] pub message_type: String, pub timestamp: String, pub sender_id: String, pub receiver_id: String, pub correlation_id: Option<String>, pub idempotency_key: String, pub test: bool, pub security: Option<Value> }
#[derive(Debug, Serialize, Deserialize)]
pub struct EnvelopeModel { pub lcp: Value }
#[derive(Debug, Serialize, Deserialize)]
pub struct LeadPayload { pub lead_id: String, pub status: String, pub channel: String, pub consumer: Value, pub location: Value, pub attributes: Value, pub external_id: Option<String>, pub submitted_at: Option<String>, pub compliance: Option<Value>, pub provenance: Option<Value>, pub exclusivity: Option<Value>, pub contact_window: Option<Value>, pub lead_quality: Option<Value>, pub expiry: Option<Value>, pub attachments: Option<Vec<Value>> }
#[derive(Debug, Serialize, Deserialize)]
pub struct CallPayload { pub lead_id: String, pub status: String, pub channel: String, pub consumer: Value, pub location: Value, pub call: Value, pub external_id: Option<String>, pub submitted_at: Option<String>, pub compliance: Option<Value>, pub provenance: Option<Value>, pub attributes: Option<Value>, pub expiry: Option<Value>, pub exclusivity: Option<Value>, pub attachments: Option<Vec<Value>> }
#[derive(Debug, Serialize, Deserialize)]
pub struct PingPayload { pub ping_id: String, pub lead_reference: String, pub phone_hash: String, pub country_code: String, pub vertical: String, pub floor_price_cents: i64, pub currency: String, pub publisher_id: Option<String>, pub offer_id: Option<String>, pub email_hash: Option<String>, pub attributes: Option<Value> }
#[derive(Debug, Serialize, Deserialize)]
pub struct PostPayload { pub lead_id: String, pub delivered_at: String, pub price_cents: i64, pub currency: String, pub buyer_id: String, pub consumer: Value, pub location: Value, pub attributes: Value, pub submitted_at: Option<String>, pub offer_id: Option<String>, pub buyer_reference: Option<String>, pub pricing: Option<Value>, pub compliance: Option<Value>, pub provenance: Option<Value>, pub call: Option<Value>, pub attachments: Option<Vec<Value>> }
#[derive(Debug, Serialize, Deserialize)]
pub struct BidPayload { pub ping_id: String, pub decision: String, #[serde(skip_serializing_if = "Option::is_none")] pub bid_price_cents: Option<i64>, #[serde(skip_serializing_if = "Option::is_none")] pub currency: Option<String>, #[serde(skip_serializing_if = "Option::is_none")] pub estimated_contact_seconds: Option<i64>, #[serde(skip_serializing_if = "Option::is_none")] pub buyer_reference: Option<String>, #[serde(skip_serializing_if = "Option::is_none")] pub reject_reason: Option<String>, #[serde(skip_serializing_if = "Option::is_none")] pub capacity_remaining: Option<i64> }
#[derive(Debug, Serialize, Deserialize)]
pub struct AckPayload { pub original_message_id: String, pub status: String, pub errors: Option<Vec<Value>>, pub lead_id: Option<String>, pub request_id: Option<String>, pub rejection_reason: Option<String> }
#[derive(Debug, Serialize, Deserialize)]
pub struct EventPayload { pub lead_id: String, pub event: String, pub timestamp: String, pub details: Option<Value>, pub external_reference: Option<String> }
#[derive(Debug, Serialize, Deserialize)]
pub struct OfferModel { pub offer_id: String, pub buyer_id: String, pub vertical: String, pub countries: Vec<String>, pub floor_price_cents: i64, pub currency: String, pub tenant_id: Option<String>, pub active: bool, pub routing_mode: Option<String>, pub schema_version: Option<String>, pub extensions: Option<Value>, pub allowed_publisher_ids: Option<Vec<String>>, pub allowed_brand_ids: Option<Vec<String>>, pub attribute_equals: Option<Value>, pub attribute_in: Option<Value>, pub monthly_minimum_payable: Option<i64>, pub monthly_maximum_payable: Option<i64>, pub monthly_quota_timezone: Option<String>, pub monthly_quota_policy: Option<String>, pub payable_rules: Option<Value>, pub call_routing_mode: Option<String>, pub connect_timeout_seconds: Option<i64> }
