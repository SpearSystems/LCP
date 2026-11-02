//! Rust SDK interop probe for tools/sdk_interop_matrix.py (LEP-0002 step 1).
//!
//! Signs the fixed hmac.json vector message through the SDK's own
//! canonical_signing_bytes + sign_hmac, then formats the SDK's timestamp
//! generation expression (lib.rs: OffsetDateTime::now_utc().format(&Rfc3339))
//! at a frozen instant with non-zero nanoseconds — `time`'s Rfc3339 emits the
//! subsecond fraction when present, the LEP-0002 evidence. The probe never
//! asserts; the matrix owns the checks.

use std::env;
use std::fs;
use std::path::PathBuf;

use lcp_sdk::{canonical_signing_bytes, sign_hmac};
use sha2::{Digest, Sha256};
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;

fn vector_path() -> PathBuf {
    let args: Vec<String> = env::args().collect();
    for i in 0..args.len().saturating_sub(1) {
        if args[i] == "--vector" {
            return PathBuf::from(&args[i + 1]);
        }
    }
    if let Ok(from_env) = env::var("LCP_VECTOR") {
        return PathBuf::from(from_env);
    }
    let mut directory = env::current_dir().expect("cwd");
    loop {
        let candidate = directory.join("test-vectors/sdk/hmac.json");
        if candidate.exists() {
            return candidate;
        }
        match directory.parent() {
            Some(parent) => directory = parent.to_path_buf(),
            None => panic!("hmac.json vector not found (pass --vector)"),
        }
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let raw: serde_json::Value = serde_json::from_str(&fs::read_to_string(vector_path())?)?;
    let timestamp = raw["timestamp"].as_str().unwrap();
    let idempotency_key = raw["idempotency_key"].as_str().unwrap();
    let secret = raw["secret"].as_str().unwrap();
    let body = raw["body_utf8"].as_str().unwrap().as_bytes().to_vec();
    let canonical = canonical_signing_bytes(timestamp, Some(idempotency_key), &body);
    let mut hasher = Sha256::new();
    hasher.update(&canonical);
    // Frozen instant 2026-08-15T10:20:00.123456789Z through the SDK's own
    // generation expression (lib.rs:51); now_utc() has no injectable seam.
    let frozen = OffsetDateTime::from_unix_timestamp_nanos(1_786_789_200_123_456_789)?;
    let result = serde_json::json!({
        "sdk": "rust",
        "canonical_sha256": hex::encode(hasher.finalize()),
        "signature_hex": sign_hmac(secret, timestamp, Some(idempotency_key), &body),
        "emitted_timestamp": frozen.format(&Rfc3339)?,
    });
    // Logging hygiene: no payload prints on stdout; stderr + LCP_INTEROP_OUT.
    eprintln!("LCP_INTEROP {}", serde_json::to_string(&result)?);
    if let Ok(out) = env::var("LCP_INTEROP_OUT") {
        fs::write(out, format!("{}\n", serde_json::to_string(&result)?))?;
    }
    Ok(())
}
