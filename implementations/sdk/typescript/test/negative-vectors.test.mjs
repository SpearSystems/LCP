// Negative security vector runner (test-vectors/sdk/negative/, README.md format).
//
// Mirrors the Python reference runner
// (implementations/sdk/python/tests/test_negative_vectors.py): format-lint the
// shared suite first, then execute every applicable vector through the real
// TypeScript SDK primitives — `verifyHmac` with the verifier clock pinned to
// the vector's `now` (raw bytes, never a re-serialization) and
// `LcpSchemaValidator` for envelope/offer validation. Vector outcome semantics:
//
//   * verify_hmac      -> verifyHmac(secret, sig, ts, key, utf8(body), window, now)
//   * verify_raw_body  -> same, but over `verify_body` bytes while the signature
//                         covers `signed_body` (invariant 2: proves no re-serialization)
//   * envelope_validate-> LcpSchemaValidator.validateEnvelope (schema name
//                         "schemas/offer.json"-free full chain: envelope + dispatched
//                         payload + vertical + ping-safe)
//   * offer_validate   -> validator.validate("offer", offer)
//   * production_mode_verify -> no verifier-side mode state in this SDK
//                         (same finding as Python, invariant 8); the vector ships
//                         sdk_exercisable=false and we print its gap_note loudly.
//
// Gap policy: non-exercisable vectors are reported as GAP lines on stderr, never
// silently passed; an exercisable vector whose check has no adapter, or an
// unknown check, FAILS the run. The TS primitives surface plain Error messages
// (no structured LCP code), so per README we assert the reject/accept outcome
// only — the error_code in the vectors is the platform-level protocol code.
//
// Run via `npm test` (appended to the package test chain).

import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { LcpSchemaValidator, verifyHmac } from "../dist/index.js";

const repoRoot = fileURLToPath(new URL("../../../..", import.meta.url));
const negativeDir = fileURLToPath(new URL("../../../../test-vectors/sdk/negative/", import.meta.url));

const SUITE_NAME = "lcp-sdk-negative";
const SUITE_VERSION = 1;
const REQUIRED_KEYS = [
  "id", "invariant", "spec_ref", "description", "applies_to",
  "check", "inputs", "expect", "sdk_exercisable",
];
// SPEC.md §5 codes each check is allowed to expect on rejection (format lint).
const CHECK_CODES = {
  verify_hmac: new Set(["LCP-012"]),
  verify_raw_body: new Set(["LCP-012"]),
  envelope_validate: new Set(["LCP-006", "LCP-008", "LCP-100"]),
  offer_validate: new Set(["LCP-100"]),
  production_mode_verify: new Set(["LCP-013"]),
};
// Closed message.type enum (SPEC.md §6); kept inline — the validator is
// schema-driven and exposes no enum accessor.
const MESSAGE_TYPES = new Set(["lead", "call", "ping", "post", "ack", "event", "bid"]);

function loadSuites() {
  const files = readdirSync(negativeDir).filter((name) => name.endsWith(".json")).sort();
  assert.ok(files.length > 0, `no vector files found under ${negativeDir}`);
  return files.map((name) => ({ ...JSON.parse(readFileSync(negativeDir + name, "utf8")), _file: name }));
}

/** Format lint mirroring the Python runner: run before trusting behaviour. */
function lintSuites(suites) {
  const problems = [];
  const seen = new Set();
  for (const suite of suites) {
    const source = suite._file;
    if (suite.suite !== SUITE_NAME) problems.push(`${source}: suite must be ${SUITE_NAME}`);
    if (suite.version !== SUITE_VERSION) problems.push(`${source}: unsupported suite version ${suite.version}`);
    for (const field of ["group", "description"]) if (!suite[field]) problems.push(`${source}: missing ${field}`);
    if (!Array.isArray(suite.vectors) || suite.vectors.length === 0) problems.push(`${source}: empty vectors array`);
    for (const vector of suite.vectors ?? []) {
      const vid = vector.id ?? "(no id)";
      const where = `${source}:${vid}`;
      for (const key of REQUIRED_KEYS) if (!(key in vector)) problems.push(`${where}: missing field '${key}'`);
      if (!vid.startsWith("lcp-neg-")) problems.push(`${where}: id must start with 'lcp-neg-'`);
      if (seen.has(vid)) problems.push(`${where}: duplicate vector id`);
      seen.add(vid);
      const invariant = vector.invariant;
      if (invariant !== null && !(Number.isInteger(invariant) && invariant >= 1 && invariant <= 8))
        problems.push(`${where}: invariant must be 1-8 or null, got ${JSON.stringify(invariant)}`);
      if (!vector.spec_ref) problems.push(`${where}: spec_ref required (even with invariant set)`);
      const applies = vector.applies_to;
      if (!Array.isArray(applies) || applies.length === 0) problems.push(`${where}: applies_to must be a non-empty list`);
      else if (applies.some((kind) => kind !== "*" && !MESSAGE_TYPES.has(kind)))
        problems.push(`${where}: applies_to has unknown kind ${JSON.stringify(applies)}`);
      const check = vector.check;
      if (!(check in CHECK_CODES)) { problems.push(`${where}: unknown check '${check}'`); continue; }
      if (typeof vector.inputs !== "object" || vector.inputs === null) problems.push(`${where}: inputs must be an object`);
      const outcome = vector.expect?.outcome;
      if (outcome !== "reject" && outcome !== "accept") problems.push(`${where}: expect.outcome must be reject|accept`);
      else if (outcome === "reject") {
        if (!CHECK_CODES[check].has(vector.expect.error_code))
          problems.push(`${where}: reject expects error_code in ${[...CHECK_CODES[check]].sort()} got ${vector.expect.error_code}`);
        if (!vector.expect.error_name) problems.push(`${where}: reject requires error_name`);
      } else if (vector.expect.error_code != null || vector.expect.error_name != null) {
        problems.push(`${where}: accept vectors must not declare an error`);
      }
      if (vector.sdk_exercisable === false) {
        if (typeof vector.gap_note !== "string" || vector.gap_note.length < 40)
          problems.push(`${where}: sdk_exercisable=false requires a substantive gap_note`);
      } else if ("gap_note" in vector) {
        problems.push(`${where}: gap_note only allowed when sdk_exercisable is false`);
      }
    }
  }
  assert.deepEqual(problems, [], "negative vector format lint failed");
}

async function execute(vector) {
  // Returns [outcome, detail] with outcome in reject|accept|gap.
  const inputs = vector.inputs;
  if (vector.sdk_exercisable === false) return ["gap", vector.gap_note];
  const run = async (action) => {
    try { await action(); return ["accept", ""]; }
    catch (error) { return ["reject", `${error.name || "Error"}: ${error.message}`]; }
  };
  switch (vector.check) {
    case "verify_hmac":
      return run(() => verifyHmac(
        inputs.secret, inputs.signature, inputs.timestamp, inputs.idempotency_key,
        new TextEncoder().encode(inputs.body), inputs.replay_window_seconds, new Date(inputs.now),
      ));
    case "verify_raw_body":
      // Signature covers `<timestamp>\n<key>\n<signed_body>`; verification MUST run
      // over `verify_body` bytes — a re-serializing verifier accepts sig-003.
      return run(() => verifyHmac(
        inputs.secret, inputs.signature, inputs.timestamp, inputs.idempotency_key,
        new TextEncoder().encode(inputs.verify_body), inputs.replay_window_seconds, new Date(inputs.now),
      ));
    case "envelope_validate":
      return run(() => validator.validateEnvelope(inputs.envelope));
    case "offer_validate":
      return run(() => validator.validate("offer", inputs.offer));
    case "production_mode_verify":
      // Exercisable=true with no adapter here would be a lane mapping gap; the
      // shared suite ships this vector exercisable=false (see gap handling below).
      return ["error", `no runner adapter for check '${vector.check}'`];
    default:
      return ["error", `unknown check '${vector.check}'`]; // never skip
  }
}

lintSuites(loadSuites());
const validator = await LcpSchemaValidator.fromDirectory(repoRoot);

const mismatches = [];
const gaps = [];
let ran = 0;
for (const suite of loadSuites()) {
  for (const vector of suite.vectors ?? []) {
    const [outcome, detail] = await execute(vector);
    if (outcome === "gap") { gaps.push(`${vector.id}: ${detail}`); continue; }
    ran += 1;
    if (outcome === "error" || outcome !== vector.expect.outcome) {
      mismatches.push(
        `${vector.id} (${vector.check}): expected ${vector.expect.outcome}, got ${outcome} — ${detail || "no detail"} — ${vector.description}`,
      );
    }
  }
}
for (const gap of gaps) console.error(`GAP (reported, never silently passed): ${gap}`);
assert.deepEqual(mismatches, [], "negative vector outcomes mismatched");
assert.ok(ran > 0, "no negative vector was actually executed");
console.log(`TypeScript SDK negative vectors passed: ${ran} executed, ${gaps.length} gap-reported`);
