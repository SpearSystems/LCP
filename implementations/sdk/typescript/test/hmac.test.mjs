import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { LcpSchemaValidator, signHmac, verifyHmac } from "../dist/index.js";

const vector = JSON.parse(await readFile(new URL("../../../../test-vectors/sdk/hmac.json", import.meta.url), "utf8"));
const body = new TextEncoder().encode(vector.body_utf8);
const signature = await signHmac(vector.secret, vector.timestamp, vector.idempotency_key, body);
assert.equal(signature, vector.signature_hex);
await verifyHmac(vector.secret, signature, vector.timestamp, vector.idempotency_key, body, vector.replay_window_seconds, new Date(vector.verification_now));
const repoRoot = fileURLToPath(new URL("../../../../", import.meta.url));
const validator = await LcpSchemaValidator.fromDirectory(repoRoot);
const lead = JSON.parse(await readFile(new URL("../../../../examples/lead.json", import.meta.url), "utf8"));
validator.validateEnvelope(lead);
console.log("TypeScript SDK HMAC and full JSON Schema vectors passed");
