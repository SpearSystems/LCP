// Runtime log-cleanliness tests (SDK-ROADMAP invariant 7, dynamic half).
//
// Static counterpart: tools/check_logging_hygiene.py proves the TypeScript
// SDK has no console.* output or logger hooks. This file pins the runtime
// behaviour: around signing/verifying the realistic post fixture, a
// schema-validation failure, and webhook verification failures, NOTHING the
// SDK writes to stdout/stderr — nor what an application logs when it prints
// the SDK's own error text (framework default, simulated here via
// console.error) — may contain a consumer field substring (name, phone
// digits, email localpart) or the serialized envelope. Deterministic and
// network-free: fixed fixture timestamps, injected `now`, no HTTP.
//
// Run via `npm test` (appended to the package test chain).

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import {
  LcpSchemaValidator,
  LcpSchemaValidationError,
  signHmac,
  utcTimestamp,
  verifyHttpRequest,
  verifyHmac,
} from "../dist/index.js";

const SECRET = "log-capture-shared-secret";
const postUrl = new URL("../../../../examples/post.json", import.meta.url);
const post = JSON.parse(await readFile(postUrl, "utf8"));
const wireBody = new TextEncoder().encode(JSON.stringify(post));
const { timestamp, idempotency_key: idempotencyKey } = post.lcp.message;
// Fixed instant inside the 300 s replay window of the fixture timestamp.
const now = new Date("2026-08-15T10:20:40Z");

// Consumer identifiers present in examples/post.json. None may appear in
// captured output on any tested path.
const PII_MARKERS = ["Jane", "Smith", "Jane Smith", "+61412345678", "412345678", "jane.smith", "Sydney"];
const SERIALIZATIONS = [JSON.stringify(post)];

/** Run `action` with stdout/stderr writes swallowed into a combined buffer. */
async function capture(action) {
  const chunks = [];
  const originalStdout = process.stdout.write.bind(process.stdout);
  const originalStderr = process.stderr.write.bind(process.stderr);
  process.stdout.write = (chunk) => { chunks.push(String(chunk)); return true; };
  process.stderr.write = (chunk) => { chunks.push(String(chunk)); return true; };
  try {
    await action();
  } finally {
    process.stdout.write = originalStdout;
    process.stderr.write = originalStderr;
  }
  return chunks.join("");
}

function assertClean(output, label) {
  for (const marker of PII_MARKERS) {
    assert.ok(!output.includes(marker), `${label}: PII marker ${JSON.stringify(marker)} appeared in output`);
  }
  for (const serialization of SERIALIZATIONS) {
    assert.ok(!output.includes(serialization), `${label}: serialized envelope appeared in output`);
  }
}

// 1. Happy path: sign + verify the PII post fixture must be fully silent.
const silentOutput = await capture(async () => {
  const signature = await signHmac(SECRET, timestamp, idempotencyKey, wireBody);
  await verifyHmac(SECRET, signature, timestamp, idempotencyKey, wireBody, 300, now);
});
assert.equal(silentOutput, "", "SDK wrote to stdout/stderr on the sign+verify happy path");

// 2. Error path: malformed post envelope rejected without echoing PII — even
// when the application logs the SDK error message (framework default).
const repoRoot = fileURLToPath(new URL("../../../../", import.meta.url));
const validator = await LcpSchemaValidator.fromDirectory(repoRoot);
{
  const broken = structuredClone(post);
  delete broken.lcp.payload.delivered_at;
  const errorOutput = await capture(async () => {
    try {
      validator.validateEnvelope(broken);
      assert.fail("malformed post envelope must throw LcpSchemaValidationError");
    } catch (error) {
      if (!(error instanceof LcpSchemaValidationError)) throw error;
      assert.match(error.message, /schemas\/post\.json/);
      console.error(`validation failed: ${error.message}`); // simulate app logging the SDK error
    }
  });
  assertClean(errorOutput, "malformed-envelope path");
}

// 3. Webhook verify failure paths over the PII body stay clean.
{
  const webhookOutput = await capture(async () => {
    // (a) Tampered signature: headers present, bytes mismatch → reject.
    try {
      await verifyHttpRequest(SECRET, {
        "X-LCP-Signature": "0".repeat(64),
        "X-LCP-Timestamp": utcTimestamp(),
        "X-LCP-Idempotency-Key": idempotencyKey,
      }, wireBody);
      assert.fail("tampered webhook signature must be rejected");
    } catch (error) {
      assert.match(error.message, /Invalid X-LCP-Signature/);
      console.error(`webhook rejected: ${error.message}`);
    }
    // (b) Missing signature header → reject.
    try {
      await verifyHttpRequest(SECRET, { "X-LCP-Timestamp": utcTimestamp() }, wireBody);
      assert.fail("webhook without signature header must be rejected");
    } catch (error) {
      assert.match(error.message, /Missing required LCP authentication headers/);
      console.error(`webhook rejected: ${error.message}`);
    }
  });
  assertClean(webhookOutput, "webhook-failure path");
}

console.log("TypeScript SDK log-capture invariant tests passed");
