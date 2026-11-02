# SPEC → Test Coverage Matrix

Machine-readable ledger mapping every normative clause of `SPEC.md` to the
tests that exercise it, per the ARB plan (§3.1). `tools/check_coverage_matrix.py`
parses the block below and fails on structural violations (unmapped vector
ids, test-less green rows, missing exempt reasons). Statuses:

* **green** — at least one named, deterministic test exercises the clause.
* **gap** — not (fully) exercised; counted in the gap backlog. Partial
  coverage keeps the existing ids listed and stays a gap until the negative
  or behavioural proof lands (many are earmarked for the `W1NegVectors` /
  `W1PlatformE2E` lanes — the gist says so).
* **exempt(reason)** — not testable at protocol level (governance process,
  deployment posture, advisory SHOULD, or structurally impossible).

Test-id families: `L1-*`/`L2-*`/`L3-*` = conformance vectors
(`test-vectors/*.json`), `sdk-NNN` = shared validation corpus
(`test-vectors/sdk/validation-corpus.json`, run by all 10 SDK harnesses),
`lcp-sdk-hmac-NNN` = shared HMAC vector, `plat:` = reference-platform
unittest (`implementations/reference-platform/tests/`), `sdk-py:` = Python
SDK unittest, `mcp:` = MCP adapter unittest, `tool:` = repo checker tool.
Rows that must be updated when `W1NegVectors` lands: SEC-04b, SEC-05b,
VER-03, LOC-02, CMP-04, MSG-PING-03, ERR-006.

Known ledger findings (filed as issues linked from the introducing PR): ATR-04
reserved-core-field shadowing is **not enforced by any tool** — planted
proof in the PR body; ERR-012's `test_bearer_with_wrong_api_key_is_rejected`
asserts LCP-012 for a wrong bearer key, where §5 maps identity failures to
LCP-002 — recorded under ERR-002.

<!-- SPEC-COVERAGE-MATRIX:BEGIN version=1 columns=clause,gist,surface,tests,status -->
| clause | gist | surface | tests | status |
|---|---|---|---|---|
| SCOPE-01 | §1 scope: money movement out of protocol; lifecycle statuses describe lead state | governance | - | exempt(no wire behavior to test; scope statement) |
| ENV-01 | §2 envelope `lcp{version,message,payload}` closed object | schema+runner | L1-001, L1-002, L1-003, sdk-001 | green |
| ENV-02 | §2 lcp.version required | schema+runner | L1-005, sdk-003 | green |
| ENV-03 | §2 message.id must be a UUID | schema+corpus | L1-006, sdk-005 | green |
| ENV-04 | §2 message.timestamp ISO-8601 | schema+corpus | L1-008, sdk-006 | green |
| ENV-05 | §2 idempotency_key mandatory on mutating messages | schema+platform | L1-004, plat:test_http_requires_json_and_idempotency_headers | green |
| ENV-06 | §2 envelope `test` canonical; X-LCP-Test header MUST mirror on HTTP | platform | plat:test_production_endpoint_rejects_test_traffic | green |
| ENV-07 | §2 sender_id/receiver_id live in the envelope, self-describing transport | schema+runner | L1-001, L1-002 | green |
| CORE-01 | §3 core contains zero vertical- or market-specific fields | conformance-runner | - | gap(no mechanical universal-core audit; §12 checklist manual — see UNI-01) |
| CON-01 | §3 consumer requires at least one of name-pair/full_name/email/phone | schema+corpus | L2-010, sdk-010, sdk-001 | green |
| CON-02 | §3 consumer.phone E.164 | schema+runner | L2-011 | green |
| CON-03 | §3 consumer.email RFC-format email | schema+corpus | sdk-007 | green |
| CON-04 | §3 consumer.dob ISO date | schema+corpus | sdk-008 | green |
| CON-05 | §3 full_name usable without first/last (non-Western names) | schema+runner | L2-012 | green |
| CON-06 | §3 locale is a BCP-47 language tag | schema | - | gap(schema keeps plain string; no BCP-47 validation or vector) |
| CON-07 | §3 phone_hash/email_hash are HMAC-SHA256 64-hex strings | schema+runner | L3-003 | green |
| CON-08 | §3 gender removed from core (verticals define it in attributes) | schema | - | gap(no assert-absent test pins gender out of core) |
| LOC-01 | §3 location.country_code REQUIRED, ISO 3166-1 alpha-2 | schema+runner | L2-001 | green |
| LOC-02 | §3 location requires state_region OR postal_code | schema | - | gap(anyOf present in schemas/core.json; no negative vector proves it — pending W1NegVectors) |
| LOC-03 | §3 location.timezone is IANA | schema | - | gap(no IANA validation or vector) |
| CMP-01 | §3 compliance block carries consent evidence fields | schema+runner | L2-001, L3-004 | green |
| CMP-02 | §3 consent_purposes captures consent scope ("capture it") | schema | - | exempt(advisory guidance; field defined in schemas/core.json) |
| CMP-03 | §3 consent_evidence extensible to new frameworks without core change | schema+runner | L3-004 | green |
| CMP-04 | §3 OTP code MUST NOT be included (only verified hash/timestamp) | schema | - | gap(otp/compliance additionalProperties:false blocks it structurally; no planted-code vector proves rejection — pending W1NegVectors) |
| CMP-05 | §3 consent_expires_at: no contact after expiry | platform | plat:test_retention_boundaries_stop_expired_and_unconsented_routing | green |
| CMP-06 | §3 scrubs[] tri-state semantics (clean/flagged/blocked; blocked leads rejected) | schema | - | gap(no vectors or platform behavior for scrub results) |
| CMP-08 | §3 consent_evidence entry shape (type/provider/token_or_url; URI valid) | schema+corpus | sdk-009, L3-004 | green |
| PRV-01 | §3 provenance enums (source_type, acquisition_method, incentive_type, platform_source) open — pass through unknowns | schema+runner | - | gap(L3-006 covers event axis only; no unknown-provenance passthrough vector) |
| PRV-02 | §3 agent provenance {agent_id, acting_for, attestation} when source_type=agent | schema+runner | L3-001 | green |
| ATR-01 | §3 attributes require vertical + schema_version | schema+runner | L2-001 | green |
| ATR-02 | §3 vertical fields tagged ping_safe; ping with non-tagged field rejected (LCP-008) | conformance-runner | L2-009, L2-009b, L2-009c, L2-009d, sdk-012 | green |
| ATR-03 | §3 ping attributes only ping_safe:true values, nested objects included | conformance-runner | L2-003 | green |
| ATR-04 | §3 vertical schemas MUST NOT redefine core field names in attributes | tooling | - | gap(NOT ENFORCED — planted phone-in-mortgage passed conformance 30/30 and check_sdk_schema_sync exit 0; issue filed) |
| ATR-05 | §3 country-specific vertical data modeled as country-scoped objects (AGENTS rule 7) | governance | - | exempt(schema-authoring guidance; enforced by vertical design review, no runtime semantics) |
| ATR-06 | §3 performance metrics belong in extensions, never in core or vertical schemas | tooling | - | gap(no checker asserts metric absence in verticals) |
| ATT-01 | §3 attachments[] metadata shape, limits, raw-byte fidelity through upload/download | platform | plat:test_attachment_upload_uses_attachment_limit_not_json_limit, plat:test_http_attachment_endpoints_preserve_raw_bytes, plat:test_attachment_download_returns_binary_headers_and_bytes | green |
| ATT-02 | §3 production attachments require clean malware scan before release; fail closed | platform | plat:test_clamav_scanner_fails_closed_for_infected_content, plat:test_ping_or_pending_post_cannot_download_attachment | green |
| ATT-03 | §3 deployments enforce encryption, residency, ownership, expiry of attachments | platform | plat:test_s3_object_storage_uses_kms_and_enforces_residency, plat:test_mva_attachment_upload_is_encrypted_access_controlled_and_posted, plat:test_attachment_expiry_retires_bytes_and_metadata | green |
| ATT-04 | §6 raw file bytes MUST NOT be placed in an LCP JSON envelope or ping | schema | - | exempt(structural: closed envelope and ping schemas admit no byte-carrying fields) |
| STA-01 | §3 status set published: 11 values | schema+platform | L2-005, plat:test_legal_transition_table_matches_conformance_graph | green |
| STA-02 | §3 only table-declared transitions legal; illegal → LCP-004 | platform+runner | L3-002, plat:test_lifecycle_events_enforce_transitions_roles_and_idempotency | green |
| STA-03 | §3 terminal states (CONVERTED/REFUNDED/REJECTED/ARCHIVED/EXPIRED/DUPLICATE) have no outgoing edges | platform | plat:test_expiry_uses_legal_transition_graph | green |
| STA-04 | §3 dispute resolution paths DISPUTED→ACCEPTED and DISPUTED→REFUNDED | platform | - | gap(no platform lifecycle test exercises either resolution path) |
| STA-05 | §3 lead expiry: expires_at/ttl_seconds → platform transitions to EXPIRED | schema+platform | L3-007, plat:test_expiry_uses_legal_transition_graph | green |
| EXC-01 | §3 exclusivity exclusive or shared, default shared | schema+runner | L3-007 | green |
| EXC-02 | §3 max_buyers bounds how many buyers receive a shared lead (no over-award) | platform | - | gap(routing enforces but no test; adversarial over-award scenario pending W1PlatformE2E) |
| MSG-01 | §4 message.type closed set of 7 (lead/call/ping/bid/post/ack/event) | schema+runner | L1-007, sdk-004 | green |
| MSG-LEAD-01 | §4 lead payload = consumer+location+compliance+provenance+attributes+external_id+submitted_at+channel | schema+runner | L2-001, sdk-001 | green |
| MSG-LEAD-02 | §4 lead.channel ∈ form/chat/click/api/agent/referral (open enum — see ENU-03) | schema | L1-001 | green |
| MSG-LEAD-03 | §4 lead_quality signals (verified_phone/email, verification block, duplicate/spam/completeness) | schema+platform | plat:test_versioned_publisher_mapping_normalizes_otp_and_audits_without_source_pii | green |
| MSG-LEAD-04 | §4 contact_window publisher hint (timezone, available_from/to, days) | schema | - | gap(schema-only; no vector or routing behavior consumes it) |
| MSG-CALL-01 | §4 call message: call block (status, durations, recording, ivr, disposition, queue, agent) | schema+runner | L1-002, L2-002 | green |
| MSG-CALL-02 | §4 CALL_* lifecycle events carry offer_id/call_status/total_seconds/disposition/transfer_status in details | schema+platform | - | gap(call lifecycle event shapes unexercised end-to-end) |
| MSG-PING-01 | §4 ping is a strict allowlist (additionalProperties:false); anything else → LCP-008 | schema+runner | L2-003, L2-007, L2-008, sdk-002 | green |
| MSG-PING-02 | §4 ping never contains consumer/compliance/provenance blocks | conformance-runner | L2-007, L2-008 | green |
| MSG-PING-03 | §4 compliance_flags are presence indicators only — never tokens or evidence | schema+runner | - | gap(no planted-token vector for compliance_flags) |
| MSG-BID-01 | §4 bid decision accept/reject/pass; bid_price_cents required iff accept | schema+platform | plat:test_conditional_bid_fields_accept_reject_pass_without_price | green |
| MSG-BID-02 | §4 auction flow: platform collects bids in expiry window, posts winner, losers get no PII | platform | plat:test_ping_bid_post_and_delivery_event_flow, plat:test_indexed_offer_candidates_are_conservative_and_reindex_updates | green |
| MSG-POST-01 | §4 post delivers full PII to winning buyer | schema+runner | L2-004 | green |
| MSG-POST-02 | §4 post.pricing block (floor/premium/final/uncapped/guardrails/dispute window/payable) | schema | L2-004 | green |
| MSG-POST-03 | §4 pricing.is_duplicate_resub distinguishes accidental resubmission from exclusivity | platform | - | gap(no test asserts duplicate-resub marking) |
| MSG-ACK-01 | §4 ack uses errors[] array — one message can fail multiple fields | schema+runner | L1-003, L3-005 | green |
| MSG-ACK-02 | §4 ack.status ∈ RECEIVED/VALIDATED/ACCEPTED/REJECTED/DUPLICATE/ERROR | schema+runner | L2-006 | green |
| MSG-ACK-03 | §4 rejection_reason structured open enum enables publisher automation | schema+platform | - | gap(no emitted-rejection_reason assertion found) |
| MSG-EVT-01 | §4 event message: lead_id, event, timestamp, details, external_reference | schema+runner | L2-005 | green |
| MSG-EVT-02 | §4 CONVERTED/DISPUTED details SHOULD shapes (conversion_value_cents etc.) | schema | - | exempt(SHOULD-level recommendations; details is additionalProperties:true by design) |
| MSG-EVT-03 | §4 CONSENT_WITHDRAWN: buyers MUST update suppression list | platform | plat:test_consent_events_trigger_suppression_and_erasure | green |
| MSG-EVT-04 | §4 ERASURE_REQUEST: buyers MUST delete data and ack | platform | plat:test_erasure_redacts_pii_and_cancels_delivery, plat:test_erasure_deletion_retries_after_storage_failure, plat:test_postgres_encrypted_intake_idempotency_and_erasure | green |
| ERR-001 | §5 LCP-001 INVALID_FORMAT ↔ 400 | platform | plat:test_malformed_json_content_type_returns_json_error, plat:test_oversized_json_body_returns_json_413 | green |
| ERR-002 | §5 LCP-002 UNKNOWN_SENDER ↔ 401 (identity distinct from signature; ledger finding: test_bearer_with_wrong_api_key_is_rejected asserts LCP-012 where §5 implies LCP-002) | platform | plat:test_lifecycle_events_enforce_transitions_roles_and_idempotency | green |
| ERR-003 | §5 LCP-003 MISSING_REQUIRED_FIELD code emitted for schema-missing fields | platform | - | gap(corpus proves rejection; no assertion the platform stamps code LCP-003) |
| ERR-004 | §5 LCP-004 INVALID_STATUS_TRANSITION ↔ 422 | platform | plat:test_lifecycle_events_enforce_transitions_roles_and_idempotency | green |
| ERR-005 | §5 LCP-005 DUPLICATE_LEAD ↔ 409 | platform | plat:test_idempotent_intake_returns_duplicate_ack, plat:test_idempotency_key_reuse_with_different_body_is_rejected | green |
| ERR-006 | §5 LCP-006 UNKNOWN_MESSAGE_TYPE structured error on the wire | schema+platform | L1-007, sdk-004 | gap(rejection proven; structured LCP-006 emission unproven — pending W1NegVectors) |
| ERR-007 | §5 LCP-007 SCHEMA_VERSION_UNSUPPORTED ↔ 422 | platform | - | gap(no implementation-visible test of unsupported version handling) |
| ERR-008 | §5 LCP-008 PII_IN_PING ↔ 400 | conformance-runner | L2-007, L2-008, L2-009, L2-009b, L2-009c, L2-009d, sdk-012 | green |
| ERR-009 | §5 LCP-009 INVALID_PHONE ↔ 400 | schema+platform | L2-011 | gap(pattern proven at schema layer; platform code emission unasserted) |
| ERR-010 | §5 LCP-010 INVALID_TERRITORY ↔ 422 | platform | - | gap(no test emits the code) |
| ERR-011 | §5 LCP-011 RATE_LIMITED ↔ 429 with Retry-After | platform | - | gap(router.py raises 429 LCP-011; no test drives the limit or header) |
| ERR-012 | §5 LCP-012 SIGNATURE_INVALID ↔ 401 (recognized sender) | platform+sdk | plat:test_http_requires_json_and_idempotency_headers, plat:test_bearer_with_wrong_api_key_is_rejected, sdk-py:test_stale_signature_is_rejected, lcp-sdk-hmac-001 | green |
| ERR-013 | §5 LCP-013 TEST_TRAFFIC_POLICY (flag/header disagreement, wrong-side traffic) | platform | plat:test_production_endpoint_rejects_test_traffic | green |
| ERR-100 | §5 LCP-100 VALIDATION_ERROR ↔ 400 catch-all | platform | plat:test_conditional_bid_fields_accept_reject_pass_without_price | green |
| ERR-500 | §5 LCP-500 INTERNAL_ERROR ↔ 500 | platform | - | gap(no fault-injection test of the 500 envelope) |
| ERR-SHAPE | §5 one error shape (errors[] with code/name/message/field/details) everywhere | schema+platform | L3-005, plat:test_malformed_json_content_type_returns_json_error | green |
| VER-01 | §6 protocol semver + per-schema versions + SDK version coherence | tooling | tool:check_sdk_versions | green |
| VER-02 | §6 deprecation policy N+2 with 2-year grace + migration guidance | governance | - | exempt(policy governs future releases; nothing to test at v1.0) |
| VER-03 | §6 unknown optional fields ignored on open objects but rejected on closed allowlists | schema+runner | - | gap(allowlist half proven via L2-007/008; ignore-vs-reject vectors pending W1NegVectors) |
| VER-04 | §6 extensions namespaced org.division.purpose and registered | governance | - | exempt(EXTENSION-REGISTRY.md is format-only governance registration; human-reviewed) |
| ENU-01 | §6 message.type closed: unknown rejected | schema+runner | L1-007 | green |
| ENU-02 | §6 status open: unknown stored and passed through | platform | - | gap(no unknown-status passthrough vector) |
| ENU-03 | §6 channel open: unknown stored | schema+runner | - | gap(no unknown-channel passthrough vector) |
| ENU-04 | §6 event open: unknown stored | schema+runner | L3-006 | green |
| ENU-05 | §6 call.status open | schema+runner | - | gap(no unknown call.status vector) |
| ENU-06 | §6 consent_evidence.type open | schema+runner | L3-004 | green |
| CNF-01 | §7 L1 tier: envelope+required+idempotency+ack+timestamps | conformance-runner | L1-001, L1-002, L1-003, L1-004, L1-005, L1-006, L1-007, L1-008 | green |
| CNF-02 | §7 L2 tier: lifecycle + ping/post split + compliance records | conformance-runner | L2-003, L2-007, L2-009 | green |
| CNF-03 | §7 L3 tier: dedup fingerprints + capability discovery + attestation verification | conformance-runner | - | gap(L3 vectors cover agent+transitions+hashes+consent; dedup-fingerprint and capability-discovery checks absent from runner) |
| CAP-01 | §8 GET /v1/lcp/capabilities response shape (versions, types, verticals, countries, auth, events, conformance_level) | platform+mcp | - | gap(endpoint implemented in router/service; zero test asserts its shape) |
| CAP-02 | §8 capacity block advisory; over-cap may be rejected (LCP-011 or REJECTED ack) | platform | - | gap(no capacity test found) |
| CAP-04 | §8 delivery_windows filter vertical+channel time windows | platform | plat:test_delivery_window_filters_vertical_and_channel | green |
| CAP-05 | §8 countries is ISO 3166-1 alpha-2 and replaces legacy markets | platform | - | gap(passthrough — no capabilities test to assert it) |
| CAP-06 | §8 routing_mode direct posts at offer floor without auction | platform | plat:test_direct_offer_creates_post_without_auction | green |
| OFR-01 | §8 platform filters candidate offers against declared restrictions before pinging | platform | plat:test_matching_returns_explainable_reasons, plat:test_versioned_publisher_mapping_normalizes_otp_and_audits_without_source_pii, plat:test_versioned_profiles_match_and_explain_failures | green |
| OFR-02 | §8 restriction field battery (excluded_source_types, excluded_acquisition_methods, reject_incentivized, excluded_incentive_types, require_verified_email, max_spam_risk_score, excluded_claim_language, require_consent_evidence, reject_dnc_flagged, reject_litigator_flagged, reject_blacklist_flagged, min_data_completeness, allowed_publisher_ids, allowed_brand_ids) | schema+platform | - | gap(only require_verified_phone + attribute_equals + profiles asserted today) |
| OFR-03 | §8 monthly payable quotas: minimum, maximum, monitor/pace/hard_cap policy | platform | plat:test_call_outcome_marks_payable_and_reports_monthly_pacing, plat:test_postgres_offer_discovery_quota_and_candidate_filtering | green |
| VERT-01 | §3 attributes validated against the vertical schema (unknown product type rejected) | schema+corpus | sdk-011 | green |
| OFFER-01 | §8 offer discovery shape: floor price + currency required | schema+corpus | sdk-013, sdk-014 | green |
| SEC-01 | §9 TLS 1.3 required for all transports | platform+deployment | - | exempt(deployment-edge policy; enforced by reference-platform Kyverno admission and security profiles, not by protocol CI) |
| SEC-02 | §9 HMAC auth via X-LCP-Signature/Timestamp/Idempotency-Key headers | sdk+platform | lcp-sdk-hmac-001, plat:test_http_service_accepts_canonical_hmac_request | green |
| SEC-03 | §9 canonical signing input timestamp\nkey\nraw-body; lowercase hex HMAC-SHA256 | sdk | lcp-sdk-hmac-001, sdk-py:test_canonical_signature_round_trip | green |
| SEC-04 | §9 verifier uses the raw body bytes; re-serialized body must fail | sdk-python | sdk-py:test_webhook_verification_uses_raw_body_and_case_insensitive_headers | green |
| SEC-04b | §9 raw-body/canonical-string parity across all 10 SDKs | sdk-all | - | gap(only Python asserted; tamper/re-serialize vectors pending W1NegVectors) |
| SEC-05 | §9 requests outside the replay window rejected before PII processing | sdk-python | sdk-py:test_stale_signature_is_rejected | green |
| SEC-05b | §9 replay-window rejection in every SDK + platform pre-PII ordering | sdk-all+platform | - | gap(pending W1NegVectors; window default 300s encoded in lcp-sdk-hmac-001 only) |
| SEC-06 | §9 X-LCP-Idempotency-Key MUST match lcp.message.idempotency_key on mutating messages | platform | - | gap(presence tested; equality of header vs envelope never asserted) |
| SEC-07 | §9 key rotation: MAY accept previous secret bounded | platform | plat:test_upsert_rehashes_and_old_key_stops_working | green |
| SEC-08 | §9 phone_hash per-pair secret: different buyers see different hashes; rainbow-table resistant | platform+sdk | - | gap(format covered by L3-003; per-pair non-correlation never exercised) |
| SEC-09 | §9 agent attestation: signed JWT verifiable against published key; free text non-conformant | schema+platform | - | gap(L3-001 positive shape only; no verification or forged-token rejection test) |
| SEC-10 | §9 rate limits per sender enforced with LCP-011 + Retry-After | platform | - | gap(same uncovered path as ERR-011) |
| SEC-11 | §9 webhooks MUST carry the same HMAC signature; insecure webhook sinks refused | platform+sdk | plat:test_production_policy_rejects_insecure_webhook, sdk-py:test_webhook_verification_uses_raw_body_and_case_insensitive_headers | green |
| SEC-12 | §9 agent-as-consumer 30s response timeout → auto-EXPIRED and re-offer | platform | - | gap(timeout policy not exercised) |
| SEC-13 | §9 PII at rest: persisted envelopes authenticated+encrypted; credential digests salted | platform | plat:test_persisted_envelopes_are_authenticated_and_encrypted, plat:test_stored_digest_is_salted, plat:test_digest_is_not_plain_sha256, plat:test_each_credential_gets_its_own_salt, plat:test_missing_salt_or_digest_never_verifies, plat:test_hash_and_verify_round_trip | green |
| PII-01 | §1/§9 ping carries non-PII attributes + hashes only; full PII flows only in post | schema+runner | L2-003, L2-004, sdk-002 | green |
| PII-02 | §9 retention policies per contract; expired/unconsented routing stopped | platform | plat:test_retention_boundaries_stop_expired_and_unconsented_routing | green |
| IDEM-01 | §1 mandatory idempotency: key reuse returns cached outcome, never duplicate delivery | platform | plat:test_idempotent_intake_returns_duplicate_ack, plat:test_postgres_encrypted_intake_idempotency_and_erasure | green |
| DNR-01 | §9/ops dead letters durable, replayable, visible without PII | platform | plat:test_dead_letters_are_durable_replayable_and_visible_in_safe_metrics | green |
| SND-01 | §5/§10 sandbox endpoints accept ONLY test traffic (both LCP-013 directions) | platform | - | gap(production-side tested via ERR-013; sandbox-side rejects at router.py:1362-1366 untested) |
| MCP-01 | §10 reference MCP adapter: tools reject malformed payloads pre-network; schema loader path-safe | mcp | mcp:test_malformed_tool_payload_is_rejected_before_network, mcp:test_valid_tool_payload_reaches_the_endpoint, mcp:test_local_schema_loader_rejects_traversal_and_symlink_escape, mcp:test_bid_without_platform_id_is_rejected_before_network | green |
| MCP-02 | §10 binding rules: capability discovery → tool list; schemas → tool input schemas | mcp | - | gap(get_capabilities tool exists; no test of discovery mapping) |
| MCP-03 | §10 agent identity maps to binding identity (MCP/A2A) | mcp | - | gap(no provenance.agent passthrough test in adapter) |
| GOV-01 | §11 CLA + attribution required on contributions | tooling | tool:check_commit_attribution | green |
| GOV-02 | §11 LEP registry coherence (statuses, links, evidence rows) | tooling | tool:check_lep_registry | green |
| GOV-03 | §11 anti-capture: no mandatory fees, approvals, or proprietary dependencies | governance | - | exempt(governance charter text; adjudicated by maintainers, not tests) |
| DOC-01 | §6 machine-readable schemas ship synced with every SDK (bundle + manifest + typed models) | tooling | tool:check_sdk_schema_sync | green |
| DOC-02 | quality gate: every schema validates its examples | conformance-runner | L2-001, L2-002, L2-003, L2-004, L2-005, L2-006 | green |
| UNI-01 | §12 universal audit checklist executed mechanically before core changes | tooling | - | gap(checklist is manual today; CORE-01/CON-08/ATR-04/ATR-06 rows are its unautomated members) |
| UNI-02 | §12 legacy production wire-contract fields map 1:1 into LCP | platform | plat:test_versioned_publisher_mapping_normalizes_otp_and_audits_without_source_pii | green |
<!-- SPEC-COVERAGE-MATRIX:END -->

## Gap backlog (v1 snapshot — recomputed by the checker, listed here for reading)

The `gap` rows above are the work queue for ARB waves W1–W5: negative
signature/replay/allowlist vectors (W1NegVectors in flight), platform
adversarial E2E (W1PlatformE2E in flight), capabilities/offer-restriction
coverage, universal-audit mechanization, and the reserved-core-field
enforcement hole (ATR-04, filed as an issue with planted proof).

Wave-5 flip: `--fail-on-gap` exists on the checker; `spec-coverage.yml`
drops `continue-on-error` at the same time the gap count reaches zero.
