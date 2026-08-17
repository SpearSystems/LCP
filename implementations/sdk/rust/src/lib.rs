use hmac::{Hmac, KeyInit, Mac};
use reqwest::blocking::Client as HttpClient;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::Sha256;
use std::time::Duration;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

pub mod validation;
pub use validation::SchemaValidator;

type HmacSha256 = Hmac<Sha256>;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Message {
    pub id: String,
    #[serde(rename = "type")]
    pub message_type: String,
    pub timestamp: String,
    pub sender_id: String,
    pub receiver_id: String,
    pub correlation_id: Option<String>,
    pub idempotency_key: String,
    pub test: bool,
}

pub fn build_envelope(message_type: &str, sender_id: &str, receiver_id: &str, payload: Value, test: bool) -> Value {
    let id = uuid();
    json!({"lcp":{"version":"1.0.0","message":{"id":id,"type":message_type,"timestamp":OffsetDateTime::now_utc().format(&Rfc3339).expect("timestamp"),"sender_id":sender_id,"receiver_id":receiver_id,"correlation_id":null,"idempotency_key":format!("{}-{}-{}", sender_id, message_type, uuid()),"test":test},"payload":payload}})
}

fn uuid() -> String {
    let bytes = uuid_bytes();
    format!("{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}", bytes[0],bytes[1],bytes[2],bytes[3],bytes[4],bytes[5],bytes[6],bytes[7],bytes[8],bytes[9],bytes[10],bytes[11],bytes[12],bytes[13],bytes[14],bytes[15])
}
fn uuid_bytes() -> [u8; 16] { let mut value = [0u8; 16]; getrandom::fill(&mut value).expect("random UUID bytes"); value[6] = (value[6] & 0x0f) | 0x40; value[8] = (value[8] & 0x3f) | 0x80; value }

pub fn canonical_signing_bytes(timestamp: &str, idempotency_key: Option<&str>, body: &[u8]) -> Vec<u8> { [format!("{}\n{}\n", timestamp, idempotency_key.unwrap_or("" )).as_bytes(), body].concat() }
pub fn sign_hmac(secret: &str, timestamp: &str, idempotency_key: Option<&str>, body: &[u8]) -> String { let mut mac = HmacSha256::new_from_slice(secret.as_bytes()).expect("HMAC key"); mac.update(&canonical_signing_bytes(timestamp, idempotency_key, body)); hex::encode(mac.finalize().into_bytes()) }
pub fn verify_hmac(secret: &str, signature: &str, timestamp: &str, idempotency_key: Option<&str>, body: &[u8], max_skew: Duration, now: OffsetDateTime) -> Result<(), String> { let signed_at = OffsetDateTime::parse(timestamp, &Rfc3339).map_err(|_| "invalid timestamp".to_string())?; if (now - signed_at).whole_seconds().unsigned_abs() > max_skew.as_secs() { return Err("timestamp outside replay window".into()); } let expected = sign_hmac(secret, timestamp, idempotency_key, body); if expected.as_bytes().ct_eq(signature.to_lowercase().as_bytes()).unwrap_u8() != 1 { return Err("invalid signature".into()); } Ok(()) }

pub struct Client { endpoint: String, sender_id: Option<String>, api_key: Option<String>, hmac_secret: Option<String>, http: HttpClient, max_retries: u32 }
impl Client {
    pub fn new(endpoint: impl Into<String>) -> Self { Self { endpoint: endpoint.into().trim_end_matches('/').to_string(), sender_id: None, api_key: None, hmac_secret: None, http: HttpClient::builder().timeout(Duration::from_secs(30)).build().expect("HTTP client"), max_retries: 2 } }
    pub fn sender_id(mut self, value: impl Into<String>) -> Self { self.sender_id = Some(value.into()); self }
    pub fn api_key(mut self, value: impl Into<String>) -> Self { self.api_key = Some(value.into()); self }
    pub fn hmac_secret(mut self, value: impl Into<String>) -> Self { self.hmac_secret = Some(value.into()); self }
    pub fn request(&self, method: reqwest::Method, path: &str, payload: Option<Value>, test: bool) -> Result<Value, Box<dyn std::error::Error>> {
        let body = payload.as_ref().map(|value| serde_json::to_vec(value)).transpose()?; let key = payload.as_ref().and_then(|value| value.pointer("/lcp/message/idempotency_key")).and_then(Value::as_str);
        for attempt in 0..=self.max_retries { let mut request = self.http.request(method.clone(), format!("{}/{}", self.endpoint, path.trim_start_matches('/'))); if let Some(value) = &self.sender_id { request = request.header("X-LCP-Sender-Id", value); } if let Some(value) = &self.api_key { request = request.bearer_auth(value); } if let Some(value) = key { request = request.header("X-LCP-Idempotency-Key", value); } if test { request = request.header("X-LCP-Test", "true"); } if let Some(secret) = &self.hmac_secret { let timestamp = OffsetDateTime::now_utc().format(&Rfc3339)?; request = request.header("X-LCP-Timestamp", &timestamp).header("X-LCP-Signature", sign_hmac(secret, &timestamp, key, body.as_deref().unwrap_or_default())); } if let Some(bytes) = &body { request = request.header("Content-Type", "application/json").body(bytes.clone()); } let response = match request.send() { Ok(value) => value, Err(error) if attempt < self.max_retries => { std::thread::sleep(Duration::from_secs(1 << attempt)); continue; }, Err(error) => return Err(Box::new(error)) }; let status = response.status(); let value: Value = response.json().unwrap_or_else(|_| json!({})); if status.is_success() { return Ok(value); } if !matches!(status.as_u16(), 429 | 500 | 502 | 503 | 504) || attempt == self.max_retries { return Err(format!("LCP HTTP {}: {}", status, value).into()); } std::thread::sleep(Duration::from_secs(1 << attempt)); }
        Err("LCP request failed".into())
    }

    pub fn submit_lead(&self, envelope: Value, test: bool) -> Result<Value, Box<dyn std::error::Error>> { self.request(reqwest::Method::POST, "/v1/lcp/leads", Some(envelope), test) }
    pub fn submit_call(&self, envelope: Value, test: bool) -> Result<Value, Box<dyn std::error::Error>> { self.request(reqwest::Method::POST, "/v1/lcp/calls", Some(envelope), test) }
    pub fn submit_bid(&self, envelope: Value, test: bool) -> Result<Value, Box<dyn std::error::Error>> { self.request(reqwest::Method::POST, "/v1/lcp/bids", Some(envelope), test) }
    pub fn query_lead_status(&self, id: &str) -> Result<Value, Box<dyn std::error::Error>> { self.request(reqwest::Method::GET, &format!("/v1/lcp/leads/{}", urlencoding::encode(id)), None, false) }
    pub fn get_capabilities(&self) -> Result<Value, Box<dyn std::error::Error>> { self.request(reqwest::Method::GET, "/v1/lcp/capabilities", None, false) }
    pub fn list_offers(&self, vertical: Option<&str>) -> Result<Value, Box<dyn std::error::Error>> { let path = vertical.map(|value| format!("/v1/lcp/offers?vertical={}", urlencoding::encode(value))).unwrap_or_else(|| "/v1/lcp/offers".to_string()); self.request(reqwest::Method::GET, &path, None, false) }
}

trait ConstantTimeEq { fn ct_eq(&self, other: &[u8]) -> subtle::Choice; }
impl ConstantTimeEq for [u8] { fn ct_eq(&self, other: &[u8]) -> subtle::Choice { subtle::ConstantTimeEq::ct_eq(self, other) } }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shared_hmac_vector() {
        let body = b"{\"hello\":\"world\"}";
        let signature = sign_hmac("sdk-shared-secret", "2026-08-15T10:20:00Z", Some("sdk-vector-001"), body);
        assert_eq!(signature, "50f90d29b46e92f257eae62c94a3d985bf9a925da03fee82e33c800fe54e7259");
        verify_hmac("sdk-shared-secret", &signature, "2026-08-15T10:20:00Z", Some("sdk-vector-001"), body, Duration::from_secs(300), OffsetDateTime::parse("2026-08-15T10:20:01Z", &Rfc3339).unwrap()).unwrap();
    }

    #[test]
    fn full_schema_vector() {
        use std::collections::HashMap;
        let mut schemas = HashMap::new();
        for directory in ["schemas", "verticals"] {
            let root = format!("../../../{directory}");
            for entry in std::fs::read_dir(&root).unwrap() {
                let path = entry.unwrap().path();
                if path.extension().and_then(|value| value.to_str()) != Some("json") {
                    continue;
                }
                let name = path.file_name().unwrap().to_string_lossy();
                schemas.insert(
                    format!("{directory}/{name}"),
                    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap(),
                );
            }
        }
        let validator = SchemaValidator::new(schemas);
        let envelope: Value = serde_json::from_str(&std::fs::read_to_string("../../../examples/lead.json").unwrap()).unwrap();
        validator.validate_envelope(&envelope).unwrap();
    }
}
