import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import { LcpClient, LcpHttpError, LcpSchemaValidator, signHmac, verifyHmac } from "../dist/index.js";

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

// LEP-0005 history read: only supplied, non-empty filters reach the query, the
// parsed body comes back unchanged, and a 4xx throws the normal LcpHttpError.
let historyMode = "ok";
const historyUrls = [];
const historyServer = createServer((request, response) => {
  historyUrls.push(request.url);
  if (historyMode === "reject") {
    response.writeHead(400, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ errors: [{ code: "LCP-001", message: "limit out of range" }] }));
    return;
  }
  response.writeHead(200, { "Content-Type": "application/json", "X-LCP-Result-Count": "1", "X-LCP-Total-Results": "1" });
  response.end(JSON.stringify({ requests: [{ request_id: "req-hist-1", message_type: "lead", direction: "inbound", status: "OPEN", created_at: "2026-11-01T00:00:00Z" }] }));
});
await new Promise((resolve) => historyServer.listen(0, "127.0.0.1", resolve));
try {
  const client = new LcpClient(`http://127.0.0.1:${historyServer.address().port}`, { maxRetries: 0 });
  const body = await client.requestHistory({ limit: 2, status: "OPEN", to: "2026-11-01T00:00:00Z" });
  assert.deepEqual(body.requests.map((row) => row.request_id), ["req-hist-1"]);
  assert.equal(body.requests[0].status, "OPEN");
  const sent = new URL(historyUrls[0], "http://stub.invalid");
  assert.equal(sent.pathname, "/v1/lcp/history/requests");
  assert.deepEqual([...sent.searchParams.keys()], ["limit", "status", "to"]);
  assert.deepEqual(Object.fromEntries(sent.searchParams), { limit: "2", status: "OPEN", to: "2026-11-01T00:00:00Z" });

  // An empty string is absent: no blank filter may widen the page.
  await client.requestHistory({ status: "", vertical: "" });
  assert.equal(historyUrls[1], "/v1/lcp/history/requests");

  historyMode = "reject";
  await assert.rejects(
    () => client.requestHistory({ limit: 500 }),
    (error) => error instanceof LcpHttpError && error.status === 400 && error.body.errors[0].code === "LCP-001",
  );
} finally {
  await new Promise((resolve) => historyServer.close(resolve));
}
console.log("TypeScript SDK history read passed");

// Verify parity with the Python reference verifier: fractional seconds and
// numeric offsets verify; an unzoned timestamp, a non-RFC 3339 form, an
// impossible date, and a non-hex signature are refused.
const verifyNow = new Date("2026-08-15T10:20:01Z");
const signedFor = (timestamp) => signHmac(vector.secret, timestamp, vector.idempotency_key, body);
for (const timestamp of ["2026-08-15T10:20:00.250Z", "2026-08-15T12:20:00+02:00", "2026-08-15T10:20:00.123456789Z"]) {
  await verifyHmac(vector.secret, await signedFor(timestamp), timestamp, vector.idempotency_key, body, 300, verifyNow);
}
await verifyHmac(vector.secret, signature.toUpperCase(), vector.timestamp, vector.idempotency_key, body, 300, verifyNow);
for (const timestamp of ["2026-08-15T10:20:00", "Sat, 15 Aug 2026 10:20:00 GMT", "2026-08-15", "2026-02-30T10:20:00Z", "2026-08-15T10:20:00+24:00"]) {
  await assert.rejects(
    async () => verifyHmac(vector.secret, await signedFor(timestamp), timestamp, vector.idempotency_key, body, 86_400 * 400, verifyNow),
    /X-LCP-Timestamp/,
    `${timestamp} must be refused`,
  );
}
// "0a" -> "az" used to parse back to 0x0a; any non-hex byte used to become 0x00.
const zeroByte = signature.match(/../g).findIndex((pair) => pair.startsWith("0"));
assert.ok(zeroByte >= 0, "the shared vector has a byte below 0x10");
const smuggled = signature.slice(0, zeroByte * 2) + signature[zeroByte * 2 + 1] + "z" + signature.slice(zeroByte * 2 + 2);
for (const bad of [smuggled, `${signature.slice(0, -2)}zz`, signature.slice(0, -1), `${signature}00`, ""]) {
  await assert.rejects(() => verifyHmac(vector.secret, bad, vector.timestamp, vector.idempotency_key, body, 300, verifyNow), /Invalid X-LCP-Signature/);
}
console.log("TypeScript SDK verify parity passed");

// Retry parity with the Python SDK: 429/5xx retry up to maxRetries, a
// delta-seconds Retry-After sets the wait, and a batch is never retried.
let retryStatuses = [];
let retryHits = 0;
const retryServer = createServer((request, response) => {
  request.resume();
  request.on("end", () => {
    const status = retryStatuses[Math.min(retryHits, retryStatuses.length - 1)];
    retryHits += 1;
    response.writeHead(status, { "Content-Type": "application/json", "Retry-After": "0" });
    response.end(JSON.stringify(status === 200 ? { results: [], ok: true } : { errors: [{ code: "LCP-011" }] }));
  });
});
await new Promise((resolve) => retryServer.listen(0, "127.0.0.1", resolve));
try {
  const retryUrl = `http://127.0.0.1:${retryServer.address().port}`;
  retryStatuses = [429, 503, 200]; retryHits = 0;
  const started = Date.now();
  assert.deepEqual(await new LcpClient(retryUrl).getCapabilities(), { results: [], ok: true });
  assert.equal(retryHits, 3);
  assert.ok(Date.now() - started < 900, "Retry-After: 0 must replace the 1 s / 2 s backoff");

  retryStatuses = [503]; retryHits = 0;
  await assert.rejects(() => new LcpClient(retryUrl, { maxRetries: 1 }).getCapabilities(), (error) => error instanceof LcpHttpError && error.status === 503);
  assert.equal(retryHits, 2);

  retryStatuses = [400]; retryHits = 0;
  await assert.rejects(() => new LcpClient(retryUrl).getCapabilities(), (error) => error instanceof LcpHttpError && error.status === 400);
  assert.equal(retryHits, 1);

  retryStatuses = [503]; retryHits = 0;
  await assert.rejects(() => new LcpClient(retryUrl).submitLeadsBatch([{ item_id: "a", idempotency_key: "k", payload: {} }]));
  assert.equal(retryHits, 1, "a batch is never retried as a whole");
} finally {
  await new Promise((resolve) => retryServer.close(resolve));
}
console.log("TypeScript SDK retry parity passed");
