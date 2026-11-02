//! Rust SDK must refuse every attack in `test-vectors/sdk/negative/`.
//! Python reference: `implementations/sdk/python/tests/test_negative_vectors.py`.

use std::path::{Path, PathBuf};
use std::time::Duration;

use lcp_sdk::SchemaValidator;
use serde_json::Value;
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

fn load_suites(dir: &Path) -> Vec<Value> {
    let mut files: Vec<PathBuf> = std::fs::read_dir(dir)
        .unwrap_or_else(|error| panic!("cannot read vector dir {dir:?}: {error}"))
        .map(|entry| entry.unwrap().path())
        .filter(|path| path.extension().and_then(|value| value.to_str()) == Some("json"))
        .collect();
    files.sort();
    assert!(!files.is_empty(), "no vector files found under {dir:?}");
    files
        .iter()
        .map(|path| serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap())
        .collect()
}

fn execute(validator: &SchemaValidator, vector: &Value) -> (String, String) {
    if vector["sdk_exercisable"].as_bool() == Some(false) {
        return ("gap".into(), vector["gap_note"].as_str().unwrap_or_default().into());
    }
    let check = vector["check"].as_str().unwrap();
    let inputs = &vector["inputs"];
    match check {
        "verify_hmac" | "verify_raw_body" => {
            let body = if check == "verify_hmac" { &inputs["body"] } else { &inputs["verify_body"] };
            let result = lcp_sdk::verify_hmac(
                inputs["secret"].as_str().unwrap(),
                inputs["signature"].as_str().unwrap(),
                inputs["timestamp"].as_str().unwrap(),
                inputs["idempotency_key"].as_str(),
                body.as_str().unwrap().as_bytes(),
                Duration::from_secs(inputs["replay_window_seconds"].as_u64().unwrap()),
                OffsetDateTime::parse(inputs["now"].as_str().unwrap(), &Rfc3339)
                    .expect("vector now must be RFC3339"),
            );
            match result {
                Ok(()) => ("accept".into(), String::new()),
                Err(detail) => ("reject".into(), detail),
            }
        }
        "envelope_validate" => match validator.validate_envelope(&inputs["envelope"]) {
            Ok(()) => ("accept".into(), String::new()),
            Err(error) => ("reject".into(), error.to_string()),
        },
        "offer_validate" => match validator.validate_offer(&inputs["offer"]) {
            Ok(()) => ("accept".into(), String::new()),
            Err(error) => ("reject".into(), error.to_string()),
        },
        other => (
            // Exercisable but unmapped check: fail loudly, never skip.
            "error".into(),
            format!("no runner adapter for check {other:?}"),
        ),
    }
}

#[test]
fn negative_security_vectors() {
    let root = repo_root();
    let validator = SchemaValidator::from_directory(root.join("schemas")).unwrap();
    let mut mismatches: Vec<String> = Vec::new();
    let mut ran = 0usize;
    for suite in load_suites(&root.join("test-vectors/sdk/negative")) {
        for vector in suite["vectors"].as_array().unwrap() {
            let (outcome, detail) = execute(&validator, vector);
            if outcome == "gap" {
                eprintln!(
                    "GAP (reported, never silently passed): {}: {detail}",
                    vector["id"].as_str().unwrap()
                );
                continue;
            }
            ran += 1;
            let expected = vector["expect"]["outcome"].as_str().unwrap();
            if outcome != expected {
                mismatches.push(format!(
                    "{} ({}): expected {expected}, got {outcome} - {detail} - {}",
                    vector["id"].as_str().unwrap(),
                    vector["check"].as_str().unwrap(),
                    vector["description"].as_str().unwrap()
                ));
            }
        }
    }
    assert!(ran > 0, "no negative vector was actually executed");
    assert!(mismatches.is_empty(), "negative vector mismatches: {mismatches:?}");
}
