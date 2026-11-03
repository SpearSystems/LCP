//! LEP-0003 SDK lane: contact-hash vectors, slot round-trip, §8 capability gate.

use std::path::{Path, PathBuf};

use lcp_sdk::{contact_hash, receiver_advertises_slot_fields, slot_capability_errors, SchemaValidator};
use serde_json::Value;

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

fn read_json(root: &Path, relative: &str) -> Value {
    serde_json::from_str(&std::fs::read_to_string(root.join(relative)).unwrap()).unwrap()
}

/// Executes every pinned preimage→digest row from test-vectors/sdk/contact-hash.json
/// (#153 T9 run). A row that cannot be reproduced is an interop failure, reported
/// with the exact preimage and the observed digest.
#[test]
fn every_pinned_contact_hash_vector_reproduces() {
    let root = repo_root();
    let suite = read_json(&root, "test-vectors/sdk/contact-hash.json");
    let vectors = suite["vectors"].as_array().unwrap();
    assert!(!vectors.is_empty(), "contact-hash.json exposed no vectors");
    let mut mismatches: Vec<String> = Vec::new();
    for vector in vectors {
        let observed = contact_hash(
            vector["secret"].as_str().unwrap(),
            vector["preimage"].as_str().unwrap(),
            vector["domain"].as_str().unwrap(),
        )
        .unwrap_or_else(|error| panic!("{}: {error}", vector["name"].as_str().unwrap()));
        let pinned = vector["digest"].as_str().unwrap();
        if observed != pinned {
            mismatches.push(format!(
                "{} ({}): preimage {:?} pinned {pinned} but Rust SDK produced {observed}",
                vector["name"].as_str().unwrap(),
                vector["domain"].as_str().unwrap(),
                vector["preimage"].as_str().unwrap(),
            ));
        }
    }
    assert!(mismatches.is_empty(), "contact-hash vector reproduction failed: {mismatches:?}");
}

#[test]
fn preimage_rules_match_spec_section_nine() {
    // Phone: verbatim wire bytes — formatting noise must change the digest.
    assert_ne!(
        contact_hash("pair-secret-for-vectors-0123456789", "+1 (512) 555-0147", "phone").unwrap(),
        "c47aff331606437528b34b7f919eb37704e87ee35eed80e52cfa36826a4a2e12"
    );
    let pinned_email = "d28da9b5424990cf7ac6000cf4e04537ca487929e71cef12f91191f29f78a6c4";
    assert_eq!(contact_hash("pair-secret-for-vectors-0123456789", "  Jordan@Example.COM  ", "email").unwrap(), pinned_email);
    assert_ne!(contact_hash("pair-secret-for-vectors-0123456789", "jordan@example.com", "email").unwrap(), pinned_email);
    assert_ne!(contact_hash("pair-secret-for-vectors-0123456789", "Jordan+crm@example.com", "email").unwrap(), pinned_email);
}

#[test]
fn version_string_membership_parses_full_semver() {
    assert!(!receiver_advertises_slot_fields(&["1.0.0"]));
    assert!(receiver_advertises_slot_fields(&["1.1.0"]));
    assert!(receiver_advertises_slot_fields(&["1.0.0", "1.1.0"]));
    assert!(receiver_advertises_slot_fields(&["1.10.0"])); // parsed minor 10 >= 1
    assert!(!receiver_advertises_slot_fields(&["2.0.0"])); // major must be 1
    assert!(!receiver_advertises_slot_fields(&["1.1"])); // literal never opens the gate
    assert!(!receiver_advertises_slot_fields(&["v1.1.0", "1.1.0.1", ""]));
}

#[test]
fn slot_round_trip_and_capability_gate() {
    let root = repo_root();
    let mut schemas = std::collections::HashMap::new();
    for directory in ["schemas", "verticals"] {
        let dir = root.join(directory);
        for entry in std::fs::read_dir(&dir).unwrap() {
            let path = entry.unwrap().path();
            if path.extension().and_then(|value| value.to_str()) != Some("json") {
                continue;
            }
            let name = format!("{directory}/{}", path.file_name().unwrap().to_string_lossy());
            schemas.insert(name, serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap());
        }
    }
    let validator = SchemaValidator::new(schemas);
    let mut envelopes = Vec::new();
    for name in ["ping-slots", "bid-slots", "post-contacted", "event-contacted"] {
        envelopes.push((name.to_string(), read_json(&root, &format!("examples/{name}.json"))));
    }

    // Slot-bearing ping/bid/post/event validate through the SDK schema path.
    for (name, envelope) in &envelopes {
        validator.validate_envelope(envelope).unwrap_or_else(|error| panic!("slot-bearing {name} failed validation: {error}"));
    }

    // The capability gate rejects them toward a receiver advertising v1.0 only…
    for (name, envelope) in &envelopes {
        let errors = slot_capability_errors(envelope, &["1.0.0"]);
        assert!(!errors.is_empty(), "slot-bearing {name} must be rejected for a v1.0-only receiver");
        let error = validator
            .validate_envelope_for_receiver(envelope, &["1.0.0"])
            .expect_err("capability gate must reject");
        assert!(error.to_string().contains("capability gate"), "{error}");
    }
    // …and accepts them toward a v1.1 advertisement.
    for (name, envelope) in &envelopes {
        validator
            .validate_envelope_for_receiver(envelope, &["1.0.0", "1.1.0"])
            .unwrap_or_else(|error| panic!("slot-bearing {name} must validate for a v1.1 receiver: {error}"));
    }
}
