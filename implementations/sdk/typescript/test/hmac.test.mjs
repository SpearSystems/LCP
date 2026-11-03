import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import { LcpClient, LcpSchemaValidator, signHmac, verifyHmac } from "../dist/index.js";

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

// LEP-0001 W2-01 batch submission: 207 is a successful response, receipts come
// back in input order, and a batch over the cap is refused before any request.
const received = [];
const server = createServer((request, response) => {
  const chunks = [];
  request.on("data", (chunk) => chunks.push(chunk));
  request.on("end", () => {
    received.push({ url: request.url, body: JSON.parse(Buffer.concat(chunks).toString("utf8")) });
    const payload = JSON.stringify({
      request_id: "req-batch-1",
      summary: { total: 2, accepted: 1, duplicate: 0, rejected: 1, error: 0 },
      results: [
        { item_id: "item_b", status: "accepted", lead_id: "lead_b" },
        { item_id: "item_a", status: "rejected", error: { code: "LCP-100", message: "invalid lead" } },
      ],
    });
    response.writeHead(207, { "Content-Type": "application/json" });
    response.end(payload);
  });
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
try {
  const client = new LcpClient(`http://127.0.0.1:${server.address().port}`, { maxRetries: 0 });
  const items = [
    { item_id: "item_a", idempotency_key: "batch-a", payload: { lead_id: "lead_a" } },
    { item_id: "item_b", idempotency_key: "batch-b", payload: { lead_id: "lead_b" } },
  ];
  const results = await client.submitLeadsBatch(items, "req-batch-1");
  assert.deepEqual(results.map((result) => result.item_id), ["item_a", "item_b"]);
  assert.equal(results[0].status, "rejected");
  assert.deepEqual(results[0].error, { code: "LCP-100", message: "invalid lead" });
  assert.equal(results[1].status, "accepted");
  assert.equal(received.length, 1);
  assert.equal(received[0].url, "/v1/lcp/leads/batch");
  assert.equal(received[0].body.request_id, "req-batch-1");
  assert.deepEqual(received[0].body.items.map((item) => item.item_id), ["item_a", "item_b"]);
  assert.deepEqual(received[0].body.items[0].payload, { lead_id: "lead_a" });
  await assert.rejects(
    () => client.submitLeadsBatch(Array.from({ length: 101 }, (_, index) => ({ item_id: `item_${index}`, idempotency_key: `key_${index}`, payload: {} }))),
    /batch supports 1\.\.100 items/,
  );
  assert.equal(received.length, 1, "cap check must run before sending");
} finally {
  await new Promise((resolve) => server.close(resolve));
}
console.log("TypeScript SDK batch submission passed");
