#!/usr/bin/env node
// example — not for production. Durable idempotency, TLS/WAF, secret
// management, and durable CRM handoff belong to the deployment (README.md).
/**
 * Minimal buyer-side webhook receiver for LCP deliveries, node stdlib only
 * (node:http + node:crypto). Same behaviour and flags as the Python receiver.
 *
 * Verifies the two signed surfaces the platform sends and answers each with
 * an `ack` envelope:
 *
 *  - `post` delivery — HMAC-SHA256 over
 *    `<X-LCP-Timestamp>\n<X-LCP-Idempotency-Key>\n<raw body>`
 *    (spec/01-transport.md §9).
 *  - event subscription delivery — HMAC-SHA256 over
 *    `<X-LCP-Timestamp>\n<X-LCP-Event-Id>\n<raw body>`
 *    (docs/OPERATIONS.md "Event subscriptions"); the event id is the
 *    signature's idempotency term, so `X-LCP-Idempotency-Key` is not the
 *    envelope key there.
 *
 * Rejections log a reason only — never the body, the secret, or consumer
 * data — and answer 401 with an `LCP-012` error before any PII is touched.
 *
 * Usage:
 *   node receiver.mjs --port 8090 --secret "$LCP_WEBHOOK_SECRET"
 *                     [--print-body]
 *
 * Secret may also come from `LCP_WEBHOOK_SECRET`.
 */
import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";

const MAX_BODY_BYTES = 1_000_000;
const REPLAY_WINDOW_SECONDS = 300;
const MAX_SEEN_IDS = 4096;

/** Whole-second UTC wire form (LEP-0002): YYYY-MM-DDTHH:MM:SSZ. */
function nowIso() {
 return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

/**
 * Lenient clock parsing for the replay window only — the digest is always
 * recomputed over the received header string verbatim.
 * Returns epoch milliseconds, or null when the value is not a usable
 * timezone-aware timestamp.
 */
function parseTimestamp(value) {
 if (!value) return null;
 if (!/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null; // must be timezone-aware
 const parsed = Date.parse(value);
 return Number.isNaN(parsed) ? null : parsed;
}

/** Lowercase hex HMAC-SHA256 over `<timestamp>\n<term>\n<body>`. */
function sign(secret, timestamp, term, body) {
 return createHmac("sha256", secret)
  .update(`${timestamp}\n${term}\n`, "utf8")
  .update(body)
  .digest("hex");
}

function verifies(secret, signature, timestamp, term, body) {
 const expected = Buffer.from(sign(secret, timestamp, term, body), "utf8");
 const received = Buffer.from(signature, "utf8");
 return expected.length === received.length && timingSafeEqual(expected, received);
}

/** Bounded in-memory de-duplication set (demo only; use a durable claim). */
class SeenIds {
 #ids = new Map();
 #limit;

 constructor(limit = MAX_SEEN_IDS) {
  this.#limit = limit;
 }

 /** True the first time `key` is seen, false for duplicates. */
 claim(key) {
  if (this.#ids.has(key)) return false;
  this.#ids.set(key, true);
  while (this.#ids.size > this.#limit) {
   this.#ids.delete(this.#ids.keys().next().value);
  }
  return true;
 }
}

/**
 * The `ack` envelope a buyer returns for a verified delivery.
 *
 * `sender_id` is the buyer this delivery was addressed to and `receiver_id`
 * the platform that sent it, so the ack travels back on the same pair. The
 * ack's own idempotency key follows the platform convention
 * `<sender_id>-ack-<message id>`.
 */
function buildAck(envelope, status) {
 const message = envelope.lcp.message;
 const buyerId = message.receiver_id;
 const ackId = randomUUID();
 const payload = {
  original_message_id: message.id,
  status,
  request_id: `req_${ackId.replaceAll("-", "").slice(0, 12)}`,
 };
 const leadId = envelope.lcp.payload?.lead_id;
 if (typeof leadId === "string" && leadId) payload.lead_id = leadId;
 return {
  lcp: {
   version: "1.0.0",
   message: {
    id: ackId,
    type: "ack",
    timestamp: nowIso(),
    sender_id: buyerId,
    receiver_id: message.sender_id,
    correlation_id: message.id,
    idempotency_key: `${buyerId}-ack-${ackId}`,
    test: Boolean(message.test),
   },
   payload,
  },
 };
}

class Rejection extends Error {
 constructor(reason, status = 401, code = "LCP-012") {
  super(reason);
  this.reason = reason;
  this.status = status;
  this.code = code;
 }
}

const ERROR_NAMES = {
 "LCP-001": "INVALID_FORMAT",
 "LCP-003": "MISSING_REQUIRED_FIELD",
 "LCP-006": "UNKNOWN_MESSAGE_TYPE",
 "LCP-012": "SIGNATURE_INVALID",
 "LCP-500": "INTERNAL_ERROR",
};

function sendJson(res, status, payload) {
 const data = Buffer.from(JSON.stringify(payload), "utf8");
 res.writeHead(status, {
  "Content-Type": "application/json",
  "Content-Length": String(data.length),
 });
 res.end(data);
}

function sendError(res, status, code, message) {
 sendJson(res, status, {
  errors: [{ code, name: ERROR_NAMES[code] ?? "INVALID_FORMAT", message }],
 });
}

/** Read the exact request bytes, refusing an oversized body. */
function readBody(req) {
 const rawLength = req.headers["content-length"];
 if (rawLength === undefined || !/^\d+$/.test(rawLength)) {
  throw new Rejection("missing or invalid Content-Length", 400, "LCP-001");
 }
 const length = Number(rawLength);
 if (length <= 0) throw new Rejection("empty body", 400, "LCP-001");
 if (length > MAX_BODY_BYTES) {
  throw new Rejection(`body exceeds ${MAX_BODY_BYTES} bytes`, 413, "LCP-001");
 }
 return new Promise((resolve, reject) => {
  const chunks = [];
  let received = 0;
  let aborted = false;
  req.on("data", (chunk) => {
   if (aborted) return;
   received += chunk.length;
   if (received > MAX_BODY_BYTES) {
    // A lying Content-Length: stop buffering, drain the rest, and refuse.
    aborted = true;
    chunks.length = 0;
    reject(new Rejection(`body exceeds ${MAX_BODY_BYTES} bytes`, 413, "LCP-001"));
    req.resume();
    return;
   }
   chunks.push(chunk);
  });
  req.on("end", () => {
   if (aborted) return;
   if (received !== length) {
    reject(new Rejection("truncated body", 400, "LCP-001"));
    return;
   }
   resolve(Buffer.concat(chunks, received));
  });
  req.on("error", () => {
   if (aborted) return;
   aborted = true;
   reject(new Rejection("request stream failed", 400, "LCP-001"));
  });
 });
}

/** Structural checks only — the receiver never needs consumer fields. */
function parseEnvelope(body) {
 let envelope;
 try {
  envelope = JSON.parse(body.toString("utf8"));
 } catch {
  throw new Rejection("body is not JSON", 400, "LCP-001");
 }
 const message = envelope?.lcp?.message;
 if (typeof message !== "object" || message === null || Array.isArray(message)) {
  throw new Rejection("body has no lcp.message object", 400, "LCP-003");
 }
 for (const field of ["id", "type", "sender_id", "receiver_id"]) {
  if (typeof message[field] !== "string" || !message[field]) {
   throw new Rejection(`lcp.message.${field} is missing`, 400, "LCP-003");
  }
 }
 if (message.type !== "post" && message.type !== "event") {
  throw new Rejection("delivery is not a post or event", 400, "LCP-006");
 }
 return envelope;
}

/** Verify a delivery and return what the ack needs. */
function verifyDelivery(secret, headers, body) {
 const signature = headers["x-lcp-signature"] ?? "";
 const timestamp = headers["x-lcp-timestamp"] ?? "";
 const eventId = headers["x-lcp-event-id"] ?? "";
 const idempotencyKey = headers["x-lcp-idempotency-key"] ?? "";
 if (!signature) throw new Rejection("missing X-LCP-Signature");
 if (!timestamp) throw new Rejection("missing X-LCP-Timestamp");

 // Event deliveries sign the stable event id; post deliveries sign the
 // envelope's idempotency key.
 const surface = eventId ? "event" : "post";
 const term = eventId || idempotencyKey;
 if (!term) throw new Rejection("missing X-LCP-Idempotency-Key");

 const signedAt = parseTimestamp(timestamp);
 if (signedAt === null) {
  throw new Rejection("X-LCP-Timestamp is not a timezone-aware timestamp");
 }
 const skewSeconds = Math.abs(Date.now() - signedAt) / 1000;
 if (skewSeconds > REPLAY_WINDOW_SECONDS) {
  throw new Rejection("X-LCP-Timestamp is outside the replay window");
 }
 if (!verifies(secret, signature, timestamp, term, body)) {
  throw new Rejection("signature mismatch");
 }

 const envelope = parseEnvelope(body);
 if (surface === "post" && envelope.lcp.message.idempotency_key !== idempotencyKey) {
  throw new Rejection("X-LCP-Idempotency-Key disagrees with the envelope");
 }
 return { envelope, surface, term };
}

/** Build the receiver server for the given options. */
function createReceiver({ secret, printBody = false, log = console }) {
 if (!secret) throw new Error("a webhook secret is required");
 const seen = new SeenIds();

 return createServer(async (req, res) => {
  const path = (req.url ?? "/").split("?", 1)[0];
  if (req.method !== "POST") {
   if (req.method === "GET" && path === "/healthz") {
    sendJson(res, 200, { status: "ok" });
    return;
   }
   sendError(res, 405, "LCP-001", "only POST deliveries and GET /healthz are served");
   return;
  }

  let verified;
  try {
   verified = verifyDelivery(secret, req.headers, await readBody(req));
  } catch (error) {
   const rejection =
    error instanceof Rejection
     ? error
     : new Rejection(`internal error: ${error?.name ?? "Error"}`, 500, "LCP-500");
   log.warn(`rejected delivery: ${rejection.reason}`);
   // A refused request may still have an unread body (a lying
   // Content-Length); drain it so the connection is not left half-open.
   req.resume();
   if (res.headersSent) return;
   const message =
    rejection.status === 401 ? "signature verification failed" : rejection.reason;
   sendError(res, rejection.status, rejection.code, message);
   return;
  }

  let ack;
  try {
   // Explicit opt-in debug dump; stdout only, never a log stream.
   if (printBody) process.stdout.write(`${JSON.stringify(verified.envelope, null, 2)}\n`);
   const status = seen.claim(verified.term) ? "RECEIVED" : "DUPLICATE";
   ack = buildAck(verified.envelope, status);
   log.info?.(`accepted ${verified.surface} delivery (status=${status})`);
  } catch (error) {
   log.error(`delivery handling failed: ${error?.name ?? "Error"}`);
   if (!res.headersSent) sendError(res, 500, "LCP-500", "internal error");
   return;
  }
  sendJson(res, 200, ack);
 });
}

function parseArgs(argv) {
 const options = {
  port: 8090,
  host: "0.0.0.0",
  secret: process.env.LCP_WEBHOOK_SECRET ?? "",
  printBody: false,
 };
 for (let index = 0; index < argv.length; index += 1) {
  const flag = argv[index];
  if (flag === "--port") {
   options.port = Number(argv[++index]);
  } else if (flag === "--host") {
   options.host = argv[++index] ?? options.host;
  } else if (flag === "--secret") {
   options.secret = argv[++index] ?? "";
  } else if (flag === "--print-body") {
   options.printBody = true;
  } else if (flag === "--help" || flag === "-h") {
   console.log(
    "usage: node receiver.mjs [--port 8090] [--host 0.0.0.0] " +
    "[--secret <hmac-secret>] [--print-body]",
   );
   process.exit(0);
  } else {
   console.error(`unknown flag: ${flag}`);
   process.exit(2);
  }
 }
 if (!Number.isInteger(options.port) || options.port < 0 || options.port > 65535) {
  console.error("error: --port must be an integer in 0..65535");
  process.exit(2);
 }
 if (!options.secret) {
  console.error("error: pass --secret or set LCP_WEBHOOK_SECRET");
  process.exit(2);
 }
 return options;
}

function main() {
 const options = parseArgs(process.argv.slice(2));
 const server = createReceiver(options);
 server.listen(options.port, options.host, () => {
  const address = server.address();
  console.log(`listening on http://${address.address}:${address.port}`);
 });
 for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.close(() => process.exit(0)));
 }
}

main();
