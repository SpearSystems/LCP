use hmac::{Hmac, KeyInit, Mac};
use reqwest::blocking::Client as HttpClient;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::Sha256;
use std::collections::HashMap;
use std::time::Duration;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

pub mod generated_models;
pub mod validation;
pub use validation::SchemaValidator;

type HmacSha256 = Hmac<Sha256>;

/**
 * Canonical whole-second UTC wire format for `X-LCP-Timestamp` and the mirrored
 * envelope field (LEP-0002). Signers MUST emit exactly this shape; the HMAC covers
 * the transmitted string verbatim (SPEC.md §9). `Rfc3339` omits the fractional
 * part when the nano field is zero, so `now_iso_utc` truncates before formatting.
 */
pub fn now_iso_utc() -> String {
    OffsetDateTime::now_utc()
        .replace_nanosecond(0)
        .expect("canonical timestamp")
        .format(&Rfc3339)
        .expect("canonical timestamp")
}

/** Format one already-parsed instant as the canonical whole-second UTC string. */
pub fn format_timestamp(value: OffsetDateTime) -> String {
    value
        .replace_nanosecond(0)
        .expect("canonical timestamp")
        .format(&Rfc3339)
        .expect("canonical timestamp")
}

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
    json!({"lcp":{"version":"1.0.0","message":{"id":id,"type":message_type,"timestamp":now_iso_utc(),"sender_id":sender_id,"receiver_id":receiver_id,"correlation_id":null,"idempotency_key":format!("{}-{}-{}", sender_id, message_type, uuid()),"test":test},"payload":payload}})
}

fn uuid() -> String {
    let bytes = uuid_bytes();
    format!("{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}", bytes[0],bytes[1],bytes[2],bytes[3],bytes[4],bytes[5],bytes[6],bytes[7],bytes[8],bytes[9],bytes[10],bytes[11],bytes[12],bytes[13],bytes[14],bytes[15])
}
fn uuid_bytes() -> [u8; 16] { let mut value = [0u8; 16]; getrandom::fill(&mut value).expect("random UUID bytes"); value[6] = (value[6] & 0x0f) | 0x40; value[8] = (value[8] & 0x3f) | 0x80; value }

pub fn canonical_signing_bytes(timestamp: &str, idempotency_key: Option<&str>, body: &[u8]) -> Vec<u8> { [format!("{}\n{}\n", timestamp, idempotency_key.unwrap_or("" )).as_bytes(), body].concat() }
pub fn hmac_sha256_hex(key: &[u8], data: &[u8]) -> String { let mut mac = HmacSha256::new_from_slice(key).expect("HMAC key"); mac.update(data); hex::encode(mac.finalize().into_bytes()) }
pub fn sign_hmac(secret: &str, timestamp: &str, idempotency_key: Option<&str>, body: &[u8]) -> String { hmac_sha256_hex(secret.as_bytes(), &canonical_signing_bytes(timestamp, idempotency_key, body)) }

/// SPEC.md §9 pinned contact preimage. Phone: the wire value verbatim,
/// byte-for-byte — no folding, stripping, or reformatting (the reference
/// platform's phone_hash builder is the executed baseline). Email: the §9
/// pinned candidate canonical — trim surrounding whitespace, ASCII-lowercase
/// the domain, keep the local-part bytes verbatim, plus-addresses distinct.
/// Email-form contacted_hashes ENTRIES stay emission-gated until #153 T9
/// executed vectors publish; this is the pinned compute rule every
/// contact-hash vector row reproduces under.
pub fn normalize_contact(value: &str, domain: &str) -> Result<String, String> {
    match domain {
        "phone" => Ok(value.to_string()),
        "email" => {
            let trimmed = value.trim();
            let separator = trimmed.rfind('@').ok_or_else(|| "email contact preimage must contain '@'".to_string())?;
            let (local, host) = trimmed.split_at(separator);
            let lowered: String = host[1..].chars().map(|c| if c.is_ascii_uppercase() { c.to_ascii_lowercase() } else { c }).collect();
            Ok(format!("{local}@{lowered}"))
        }
        other => Err(format!("unknown contact domain '{other}' (expected phone or email)")),
    }
}

/// Per-pair contact hash (SPEC.md §9, LEP-0003 §3): lowercase-hex
/// HMAC-SHA256(pair_secret, normalized_preimage) keyed with the secret shared
/// with the message's receiver. phone_hash, email_hash, and contacted_hashes
/// entries all ride this one construction — the same digest path as
/// sign_hmac, never an unsalted digest, never SHA-1.
pub fn contact_hash(secret: &str, value: &str, domain: &str) -> Result<String, String> {
    Ok(hmac_sha256_hex(secret.as_bytes(), normalize_contact(value, domain)?.as_bytes()))
}

/// Payload fields introduced at v1.1 (LEP-0003) that require the receiving
/// endpoint to advertise v1.1 support: the ping/bid/post objects are closed,
/// so a v1.0 schema-validating receiver REJECTS the fields rather than
/// ignoring them (SPEC.md §8 capability gate, never ignore-gated).
pub const SLOT_FIELDS: [&str; 3] = ["slots_available", "slots_claimed", "contacted_hashes"];

/// SPEC.md §8 version-string membership: the gate opens only for a parseable
/// full major.minor.patch element with major == 1 and minor >= 1; comparison
/// is parsed, never a string literal or prefix. Malformed or partial elements
/// ("1.1", "v1.1.0", "1.1.0.1") never open the gate.
pub fn receiver_advertises_slot_fields(advertised_lcp_versions: &[&str]) -> bool {
    advertised_lcp_versions.iter().any(|element| {
        let parts: Vec<&str> = element.split('.').collect();
        if parts.len() != 3 || !parts.iter().all(|part| !part.is_empty() && part.chars().all(|c| c.is_ascii_digit())) {
            return false;
        }
        parts[0].parse::<u32>() == Ok(1) && parts[1].parse::<u32>().unwrap_or(0) >= 1
    })
}

/// Errors when an envelope carries v1.1 slot fields but the receiver's
/// advertisement does not support them (§8 gate; invalid example D). An empty
/// vector is capability-compatibility, not a schema verdict.
pub fn slot_capability_errors(envelope: &Value, advertised_lcp_versions: &[&str]) -> Vec<String> {
    let payload = envelope.pointer("/lcp/payload");
    let mut present: Vec<String> = Vec::new();
    if let Some(object) = payload.and_then(Value::as_object) {
        for field in SLOT_FIELDS {
            if object.contains_key(field) {
                present.push(field.to_string());
            }
        }
        if object.get("event").and_then(Value::as_str) == Some("CONTACTED")
            && object.get("details").and_then(Value::as_object).map(|d| d.contains_key("contacted_hashes")).unwrap_or(false)
        {
            present.push("details.contacted_hashes".to_string());
        }
    }
    if present.is_empty() || receiver_advertises_slot_fields(advertised_lcp_versions) {
        return Vec::new();
    }
    present.into_iter().map(|field| format!("{field} requires the receiver to advertise a full-semver LCP version with major 1 and minor >= 1 (SPEC.md §8 capability gate)")).collect()
}
pub fn verify_hmac(secret: &str, signature: &str, timestamp: &str, idempotency_key: Option<&str>, body: &[u8], max_skew: Duration, now: OffsetDateTime) -> Result<(), String> { let signed_at = OffsetDateTime::parse(timestamp, &Rfc3339).map_err(|_| "invalid timestamp".to_string())?; if (now - signed_at).whole_seconds().unsigned_abs() > max_skew.as_secs() { return Err("timestamp outside replay window".into()); } let expected = sign_hmac(secret, timestamp, idempotency_key, body); if expected.as_bytes().ct_eq(signature.to_lowercase().as_bytes()).unwrap_u8() != 1 { return Err("invalid signature".into()); } Ok(()) }

/// LEP-0001 W2-01 caps one batch at 100 items (schemas/batch.json).
pub const MAX_BATCH_ITEMS: usize = 100;

/// LEP-0005 `GET /v1/lcp/history/requests` filters (`history:read`). Every
/// field is optional: `None` and empty strings are omitted from the query
/// instead of being sent blank, because a blank filter widens the page rather
/// than narrowing it. Rows are scoped server-side to the credential's tenant,
/// so there is no tenant field here.
#[derive(Debug, Default, Clone)]
pub struct HistoryParams<'a> {
    pub limit: Option<u32>,
    pub cursor: Option<&'a str>,
    pub request_id: Option<&'a str>,
    pub status: Option<&'a str>,
    pub vertical: Option<&'a str>,
    pub caller_phone_hash: Option<&'a str>,
    pub from: Option<&'a str>,
    pub to: Option<&'a str>,
}

pub struct Client { endpoint: String, sender_id: Option<String>, api_key: Option<String>, hmac_secret: Option<String>, http: HttpClient, max_retries: u32 }
impl Client {
    pub fn new(endpoint: impl Into<String>) -> Self { Self { endpoint: endpoint.into().trim_end_matches('/').to_string(), sender_id: None, api_key: None, hmac_secret: None, http: HttpClient::builder().timeout(Duration::from_secs(30)).build().expect("HTTP client"), max_retries: 2 } }
    pub fn sender_id(mut self, value: impl Into<String>) -> Self { self.sender_id = Some(value.into()); self }
    pub fn api_key(mut self, value: impl Into<String>) -> Self { self.api_key = Some(value.into()); self }
    pub fn hmac_secret(mut self, value: impl Into<String>) -> Self { self.hmac_secret = Some(value.into()); self }
    pub fn request(&self, method: reqwest::Method, path: &str, payload: Option<Value>, test: bool) -> Result<Value, Box<dyn std::error::Error>> {
        self.request_with_retries(method, path, payload, test, self.max_retries)
    }

    fn request_with_retries(&self, method: reqwest::Method, path: &str, payload: Option<Value>, test: bool, max_retries: u32) -> Result<Value, Box<dyn std::error::Error>> {
        let body = payload.as_ref().map(|value| serde_json::to_vec(value)).transpose()?; let key = payload.as_ref().and_then(|value| value.pointer("/lcp/message/idempotency_key")).and_then(Value::as_str);
        for attempt in 0..=max_retries { let mut request = self.http.request(method.clone(), format!("{}/{}", self.endpoint, path.trim_start_matches('/'))); if let Some(value) = &self.sender_id { request = request.header("X-LCP-Sender-Id", value); } if let Some(value) = &self.api_key { request = request.bearer_auth(value); } if let Some(value) = key { request = request.header("X-LCP-Idempotency-Key", value); } if test { request = request.header("X-LCP-Test", "true"); } if let Some(secret) = &self.hmac_secret { let timestamp = now_iso_utc(); request = request.header("X-LCP-Timestamp", &timestamp).header("X-LCP-Signature", sign_hmac(secret, &timestamp, key, body.as_deref().unwrap_or_default())); } if let Some(bytes) = &body { request = request.header("Content-Type", "application/json").body(bytes.clone()); } let response = match request.send() { Ok(value) => value, Err(error) if attempt < max_retries => { std::thread::sleep(Duration::from_secs(1 << attempt)); continue; }, Err(error) => return Err(Box::new(error)) }; let status = response.status(); let value: Value = response.json().unwrap_or_else(|_| json!({})); if status.is_success() { return Ok(value); } if !matches!(status.as_u16(), 429 | 500 | 502 | 503 | 504) || attempt == max_retries { return Err(format!("LCP HTTP {}: {}", status, value).into()); } std::thread::sleep(Duration::from_secs(1 << attempt)); }
        Err("LCP request failed".into())
    }

    /// Submit up to [`MAX_BATCH_ITEMS`] lead items in one request (LEP-0001
    /// W2-01) and return the per-item receipts in input order. HTTP 207 partial
    /// success is a successful response; per-item `error` objects are preserved.
    /// The batch is never retried as a whole — retrying only rejected/error
    /// receipts is the caller's job.
    pub fn submit_leads_batch(&self, items: &[Value], request_id: Option<&str>) -> Result<Vec<Value>, Box<dyn std::error::Error>> {
        if items.is_empty() || items.len() > MAX_BATCH_ITEMS {
            return Err(format!("batch supports 1..={MAX_BATCH_ITEMS} items, got {}", items.len()).into());
        }
        let request_id = request_id.map(str::to_string).unwrap_or_else(uuid);
        let body = json!({"request_id": request_id, "items": items});
        let response = self.request_with_retries(reqwest::Method::POST, "/v1/lcp/leads/batch", Some(body), false, 0)?;
        let results = response.get("results").and_then(Value::as_array).ok_or("batch response missing results")?;
        let order: HashMap<&str, usize> = items.iter().enumerate().filter_map(|(index, item)| item.get("item_id").and_then(Value::as_str).map(|id| (id, index))).collect();
        let mut ordered: Vec<(usize, usize, Value)> = results.iter().cloned().enumerate().map(|(index, result)| {
            let rank = result.get("item_id").and_then(Value::as_str).and_then(|id| order.get(id)).copied().unwrap_or(items.len());
            (rank, index, result)
        }).collect();
        ordered.sort_by_key(|(rank, index, _)| (*rank, *index));
        Ok(ordered.into_iter().map(|(_, _, result)| result).collect())
    }

    pub fn submit_lead(&self, envelope: Value, test: bool) -> Result<Value, Box<dyn std::error::Error>> { self.request(reqwest::Method::POST, "/v1/lcp/leads", Some(envelope), test) }
    pub fn submit_call(&self, envelope: Value, test: bool) -> Result<Value, Box<dyn std::error::Error>> { self.request(reqwest::Method::POST, "/v1/lcp/calls", Some(envelope), test) }
    pub fn submit_bid(&self, envelope: Value, test: bool) -> Result<Value, Box<dyn std::error::Error>> { self.request(reqwest::Method::POST, "/v1/lcp/bids", Some(envelope), test) }
    pub fn query_lead_status(&self, id: &str) -> Result<Value, Box<dyn std::error::Error>> { self.request(reqwest::Method::GET, &format!("/v1/lcp/leads/{}", urlencoding::encode(id)), None, false) }
    pub fn get_capabilities(&self) -> Result<Value, Box<dyn std::error::Error>> { self.request(reqwest::Method::GET, "/v1/lcp/capabilities", None, false) }
    pub fn list_offers(&self, vertical: Option<&str>) -> Result<Value, Box<dyn std::error::Error>> { let path = vertical.map(|value| format!("/v1/lcp/offers?vertical={}", urlencoding::encode(value))).unwrap_or_else(|| "/v1/lcp/offers".to_string()); self.request(reqwest::Method::GET, &path, None, false) }

    /// Page the LEP-0005 request-history audit log and return the decoded body
    /// unchanged (`{"requests": [...]}`); a 4xx surfaces as the shared request
    /// error, like every other read.
    pub fn request_history(&self, params: &HistoryParams) -> Result<Value, Box<dyn std::error::Error>> {
        let mut query: Vec<String> = Vec::new();
        if let Some(limit) = params.limit {
            query.push(format!("limit={limit}"));
        }
        for (name, value) in [
            ("cursor", params.cursor),
            ("request_id", params.request_id),
            ("status", params.status),
            ("vertical", params.vertical),
            ("caller_phone_hash", params.caller_phone_hash),
            ("from", params.from),
            ("to", params.to),
        ] {
            if let Some(value) = value {
                if !value.is_empty() {
                    query.push(format!("{name}={}", urlencoding::encode(value)));
                }
            }
        }
        let path = if query.is_empty() {
            "/v1/lcp/history/requests".to_string()
        } else {
            format!("/v1/lcp/history/requests?{}", query.join("&"))
        };
        self.request(reqwest::Method::GET, &path, None, false)
    }
}

trait ConstantTimeEq { fn ct_eq(&self, other: &[u8]) -> subtle::Choice; }
impl ConstantTimeEq for [u8] { fn ct_eq(&self, other: &[u8]) -> subtle::Choice { subtle::ConstantTimeEq::ct_eq(self, other) } }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn canonical_timestamp_shape_pins_whole_second_utc() {
        let frozen = OffsetDateTime::from_unix_timestamp_nanos(1_786_789_200_123_456_789).unwrap();
        assert_eq!("2026-08-15T10:20:00Z", format_timestamp(frozen));
    }

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

    #[test]
    fn shared_validation_corpus() {
        use std::collections::HashMap;
        use std::path::Path;
        let root = Path::new("../../..");
        let mut schemas = HashMap::new();
        for directory in ["schemas", "verticals"] {
            let dir = root.join(directory);
            for entry in std::fs::read_dir(&dir).unwrap() {
                let path = entry.unwrap().path();
                if path.extension().and_then(|value| value.to_str()) != Some("json") {
                    continue;
                }
                let name = format!("{directory}/{}", path.file_name().unwrap().to_string_lossy());
                schemas.insert(
                    name,
                    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap(),
                );
            }
        }
        let validator = SchemaValidator::new(schemas);
        let corpus: Value = serde_json::from_str(
            &std::fs::read_to_string(root.join("test-vectors/sdk/validation-corpus.json")).unwrap(),
        )
        .unwrap();
        let mut mismatches: Vec<String> = Vec::new();
        for fixture in corpus["fixtures"].as_array().unwrap() {
            let id = fixture["id"].as_str().unwrap();
            let rule = fixture["rule"].as_str().unwrap();
            let expect_pass = fixture["expect"].as_str() == Some("pass");
            let is_offer = fixture.get("offer").is_some();
            let document = if is_offer { &fixture["offer"] } else { &fixture["envelope"] };
            let result = if is_offer {
                validator.validate_offer(document)
            } else {
                validator.validate_envelope(document)
            };
            let passed = result.is_ok();
            if passed != expect_pass {
                let detail = result.err().map(|e| e.to_string()).unwrap_or_default();
                mismatches.push(format!("{id} ({rule}): expected pass={expect_pass}, got {detail}"));
            }
        }
        assert!(mismatches.is_empty(), "corpus mismatches: {mismatches:?}");
    }

    #[test]
    fn reject_bid_without_price_serializes_and_validates() {
        use std::collections::HashMap;
        let bid = generated_models::BidPayload {
            ping_id: "ping-001".to_string(),
            decision: "reject".to_string(),
            bid_price_cents: None,
            currency: None,
            estimated_contact_seconds: None,
            buyer_reference: None,
            reject_reason: Some("price_too_low".to_string()),
            capacity_remaining: None,
            slots_claimed: None,
        };
        let json = serde_json::to_string(&bid).unwrap();
        assert!(!json.contains("bid_price_cents"), "optional price must be omitted: {json}");
        assert!(!json.contains("currency"), "optional currency must be omitted: {json}");

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
        let payload: Value = serde_json::from_str(&json).unwrap();
        validator.validate("schemas/bid.json", &payload).unwrap();
    }

    #[test]
    fn batch_submission_orders_receipts_and_treats_207_as_success() {
        use std::io::{Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buffer = Vec::new();
            let mut chunk = [0u8; 4096];
            loop {
                let read = stream.read(&mut chunk).unwrap();
                buffer.extend_from_slice(&chunk[..read]);
                if read == 0 || buffer.windows(4).any(|window| window == b"\r\n\r\n") {
                    break;
                }
            }
            let header_end = buffer.windows(4).position(|window| window == b"\r\n\r\n").unwrap() + 4;
            let headers = String::from_utf8_lossy(&buffer[..header_end]).to_lowercase();
            let length: usize = headers
                .lines()
                .find_map(|line| line.strip_prefix("content-length:"))
                .map(|value| value.trim().parse().unwrap())
                .unwrap_or(0);
            while buffer.len() - header_end < length {
                let read = stream.read(&mut chunk).unwrap();
                if read == 0 {
                    break;
                }
                buffer.extend_from_slice(&chunk[..read]);
            }
            let body = String::from_utf8_lossy(&buffer[header_end..header_end + length]).to_string();
            assert!(body.contains("\"request_id\":\"req-batch-1\""));
            assert!(body.contains("\"item_id\":\"item_a\""));
            let response = r#"{"request_id":"req-batch-1","summary":{"total":2,"accepted":1,"duplicate":0,"rejected":1,"error":0},"results":[{"item_id":"item_b","status":"accepted","lead_id":"lead_b"},{"item_id":"item_a","status":"rejected","error":{"code":"LCP-100","message":"invalid lead"}}]}"#;
            write!(stream, "HTTP/1.1 207 Multi-Status\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", response.len(), response).unwrap();
        });
        let client = Client::new(format!("http://{}", address));
        let items = vec![
            json!({"item_id":"item_a","idempotency_key":"batch-a","payload":{"lead_id":"lead_a"}}),
            json!({"item_id":"item_b","idempotency_key":"batch-b","payload":{"lead_id":"lead_b"}}),
        ];
        let results = client.submit_leads_batch(&items, Some("req-batch-1")).unwrap();
        server.join().unwrap();
        assert_eq!(results[0]["item_id"], "item_a");
        assert_eq!(results[0]["status"], "rejected");
        assert_eq!(results[0]["error"]["code"], "LCP-100");
        assert_eq!(results[1]["status"], "accepted");
        let over_cap: Vec<Value> = (0..101)
            .map(|index| json!({"item_id": format!("item_{index}"), "idempotency_key": format!("key_{index}"), "payload": {}}))
            .collect();
        assert!(client.submit_leads_batch(&over_cap, None).is_err());
    }

    #[test]
    fn history_read_sends_only_supplied_filters_and_surfaces_errors() {
        use std::io::{Read, Write};

        fn serve(status_line: &'static str, body: &'static str) -> (String, std::thread::JoinHandle<String>) {
            let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
            let address = listener.local_addr().unwrap();
            let handle = std::thread::spawn(move || {
                let (mut stream, _) = listener.accept().unwrap();
                let mut buffer = Vec::new();
                let mut chunk = [0u8; 4096];
                loop {
                    let read = stream.read(&mut chunk).unwrap();
                    buffer.extend_from_slice(&chunk[..read]);
                    if read == 0 || buffer.windows(4).any(|window| window == b"\r\n\r\n") {
                        break;
                    }
                }
                let text = String::from_utf8_lossy(&buffer).to_string();
                let target = text.lines().next().unwrap_or_default().split(' ').nth(1).unwrap_or_default().to_string();
                write!(stream, "{status_line}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body).unwrap();
                target
            });
            (format!("http://{address}"), handle)
        }

        let (endpoint, server) = serve(
            "HTTP/1.1 200 OK",
            r#"{"requests":[{"request_id":"req-hist-1","message_type":"lead","direction":"inbound","status":"OPEN","created_at":"2026-11-01T00:00:00Z"}]}"#,
        );
        let body = Client::new(endpoint)
            .request_history(&HistoryParams {
                limit: Some(2),
                cursor: Some(""),
                status: Some("OPEN"),
                to: Some("2026-11-01T00:00:00Z"),
                ..Default::default()
            })
            .unwrap();
        let target = server.join().unwrap();
        assert_eq!(body["requests"][0]["request_id"], "req-hist-1");
        assert_eq!(body["requests"][0]["status"], "OPEN");
        // Supplied filters only, in declaration order; the empty cursor is absent.
        assert_eq!(
            target,
            "/v1/lcp/history/requests?limit=2&status=OPEN&to=2026-11-01T00%3A00%3A00Z"
        );

        let (endpoint, server) = serve(
            "HTTP/1.1 400 Bad Request",
            r#"{"errors":[{"code":"LCP-001","message":"limit out of range"}]}"#,
        );
        let error = Client::new(endpoint)
            .request_history(&HistoryParams { limit: Some(500), ..Default::default() })
            .unwrap_err();
        server.join().unwrap();
        assert!(error.to_string().contains("LCP HTTP 400"), "{error}");
    }
}
