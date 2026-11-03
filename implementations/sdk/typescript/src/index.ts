export { LcpSchemaValidationError, LcpSchemaValidator, type SchemaBundle } from "./validation.js";
export { SLOT_FIELDS, receiverAdvertisesSlotFields, slotCapabilityErrors, slotFieldsPresent } from "./capability.js";

export interface LcpMessage {
  id: string;
  type: string;
  timestamp: string;
  sender_id: string;
  receiver_id: string;
  correlation_id: string | null;
  idempotency_key: string;
  test: boolean;
}

export interface LcpEnvelope<T = Record<string, unknown>> {
  lcp: {
    version: string;
    message: LcpMessage;
    payload: T;
  };
}

export function utcTimestamp(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function buildEnvelope<T extends Record<string, unknown>>(
  type: string,
  senderId: string,
  receiverId: string,
  payload: T,
  options: Partial<Pick<LcpMessage, "id" | "correlation_id" | "idempotency_key" | "timestamp" | "test">> & { version?: string } = {},
): LcpEnvelope<T> {
  const id = options.id ?? crypto.randomUUID();
  return {
    lcp: {
      version: options.version ?? "1.0.0",
      message: {
        id,
        type,
        timestamp: options.timestamp ?? utcTimestamp(),
        sender_id: senderId,
        receiver_id: receiverId,
        correlation_id: options.correlation_id ?? null,
        idempotency_key: options.idempotency_key ?? `${senderId}-${type}-${crypto.randomUUID()}`,
        test: options.test ?? false,
      },
      payload,
    },
  };
}

function toBytes(value: string | Uint8Array): Uint8Array {
  return typeof value === "string" ? new TextEncoder().encode(value) : value;
}

function asBufferSource(value: Uint8Array): BufferSource {
  return value as unknown as BufferSource;
}

export function canonicalSigningBytes(timestamp: string, idempotencyKey: string | undefined, body: Uint8Array): Uint8Array {
  const prefix = new TextEncoder().encode(`${timestamp}\n${idempotencyKey ?? ""}\n`);
  const result = new Uint8Array(prefix.length + body.length);
  result.set(prefix);
  result.set(body, prefix.length);
  return result;
}

function hex(bytes: ArrayBuffer): string {
  return [...new Uint8Array(bytes)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function hmacHex(secret: string | Uint8Array, data: Uint8Array): Promise<string> {
  const key = await globalThis.crypto.subtle.importKey("raw", asBufferSource(toBytes(secret)), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
  return hex(await globalThis.crypto.subtle.sign("HMAC", key, asBufferSource(data)));
}

export async function signHmac(secret: string | Uint8Array, timestamp: string, idempotencyKey: string | undefined, body: Uint8Array): Promise<string> {
  return hmacHex(secret, canonicalSigningBytes(timestamp, idempotencyKey, body));
}

export type ContactHashDomain = "phone" | "email";

/**
 * The SPEC.md §9 pinned preimage for a contact value. Phone: the wire value
 * verbatim, byte-for-byte — no folding, stripping, or reformatting (the
 * reference platform's phone_hash builder is the executed baseline). Email:
 * the §9 pinned candidate canonical — trim surrounding whitespace,
 * ASCII-lowercase the domain, keep the local-part bytes verbatim,
 * plus-addresses distinct. Email-form contacted_hashes ENTRIES stay
 * emission-gated until #153 T9 executed vectors publish; this is the pinned
 * compute rule the SDK must reproduce every contact-hash vector row under.
 */
export function normalizeContact(value: string, domain: ContactHashDomain): string {
  if (domain === "phone") return value;
  if (domain === "email") {
    const trimmed = value.trim();
    const separator = trimmed.lastIndexOf("@");
    if (separator < 0) throw new Error("email contact preimage must contain '@'");
    const local = trimmed.slice(0, separator);
    const host = trimmed.slice(separator + 1).replace(/[A-Z]/g, (letter) => letter.toLowerCase());
    return `${local}@${host}`;
  }
  throw new Error(`unknown contact domain: ${String(domain)} (expected phone or email)`);
}

/**
 * Per-pair contact hash (SPEC.md §9, LEP-0003 §3): lowercase-hex
 * HMAC-SHA256(pair_secret, normalized_preimage) keyed with the secret shared
 * with the message's receiver. phone_hash, email_hash, and contacted_hashes
 * all ride this one construction — the same digest path as signHmac, never an
 * unsalted digest, never SHA-1.
 */
export async function contactHash(secret: string | Uint8Array, value: string, domain: ContactHashDomain = "phone"): Promise<string> {
  return hmacHex(secret, toBytes(normalizeContact(value, domain)));
}

export async function verifyHmac(secret: string | Uint8Array, signature: string, timestamp: string, idempotencyKey: string | undefined, body: Uint8Array, maxSkewSeconds = 300, now = new Date()): Promise<void> {
  const signedAt = new Date(timestamp);
  if (Number.isNaN(signedAt.valueOf()) || Math.abs(now.getTime() - signedAt.getTime()) / 1000 > maxSkewSeconds) {
    throw new Error("X-LCP-Timestamp is invalid or outside the replay window");
  }
  const key = await globalThis.crypto.subtle.importKey("raw", asBufferSource(toBytes(secret)), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
  const signatureBytes = new Uint8Array(signature.match(/.{1,2}/g)?.map((pair) => Number.parseInt(pair, 16)) ?? []);
  if (!(await globalThis.crypto.subtle.verify("HMAC", key, asBufferSource(signatureBytes), asBufferSource(canonicalSigningBytes(timestamp, idempotencyKey, body))))) {
    throw new Error("Invalid X-LCP-Signature");
  }
}

export function validateEnvelope(envelope: unknown): asserts envelope is LcpEnvelope {
  const value = envelope as Partial<LcpEnvelope>;
  const message = value?.lcp?.message;
  if (!value?.lcp || typeof value.lcp.version !== "string" || !message || typeof message !== "object") throw new Error("Invalid LCP envelope");
  for (const field of ["id", "type", "timestamp", "sender_id", "receiver_id", "idempotency_key"]) {
    if (typeof (message as unknown as Record<string, unknown>)[field] !== "string") throw new Error(`Missing lcp.message.${field}`);
  }
  if (!value.lcp.payload || typeof value.lcp.payload !== "object") throw new Error("Missing lcp.payload");
}

export class LcpHttpError extends Error {
  constructor(public readonly status: number, public readonly body: unknown) {
    super(`LCP HTTP ${status}`);
  }
}

export interface LcpClientOptions {
  senderId?: string;
  apiKey?: string;
  hmacSecret?: string | Uint8Array;
  timeoutMs?: number;
  maxRetries?: number;
}

/** LEP-0001 W2-01 caps one batch at 100 items (schemas/batch.json). */
export const MAX_BATCH_ITEMS = 100;

export interface LcpBatchItem {
  item_id: string;
  idempotency_key: string;
  payload: Record<string, unknown>;
}

export interface LcpBatchResult {
  item_id: string;
  status: "accepted" | "duplicate" | "rejected" | "error";
  lead_id?: string | null;
  error?: unknown;
}

export class LcpClient {
  private readonly endpoint: string;
  private readonly options: Required<Pick<LcpClientOptions, "timeoutMs" | "maxRetries">> & LcpClientOptions;

  constructor(endpoint: string, options: LcpClientOptions = {}) {
    this.endpoint = endpoint.replace(/\/$/, "");
    this.options = { timeoutMs: 30_000, maxRetries: 2, ...options };
  }

  async request<T = Record<string, unknown>>(method: string, path: string, payload?: unknown, options: { idempotencyKey?: string; test?: boolean; maxRetries?: number } = {}): Promise<T> {
    const body = payload === undefined ? new Uint8Array() : new TextEncoder().encode(JSON.stringify(payload));
    const idempotencyKey = options.idempotencyKey ?? (payload as LcpEnvelope | undefined)?.lcp?.message?.idempotency_key;
    const maxRetries = Math.max(options.maxRetries ?? this.options.maxRetries, 0);
    const headers = new Headers({ "Content-Type": "application/json", Accept: "application/json" });
    if (this.options.senderId) headers.set("X-LCP-Sender-Id", this.options.senderId);
    if (this.options.apiKey) headers.set("Authorization", `Bearer ${this.options.apiKey}`);
    if (idempotencyKey) headers.set("X-LCP-Idempotency-Key", idempotencyKey);
    if (options.test) headers.set("X-LCP-Test", "true");
    if (this.options.hmacSecret) {
      const timestamp = utcTimestamp();
      headers.set("X-LCP-Timestamp", timestamp);
      headers.set("X-LCP-Signature", await signHmac(this.options.hmacSecret, timestamp, idempotencyKey, body));
    }
    let lastError: unknown;
    for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.options.timeoutMs);
      try {
        const response = await fetch(`${this.endpoint}/${path.replace(/^\//, "")}`, { method, headers, body: body.length ? body : undefined, signal: controller.signal });
        const text = await response.text();
        let data: unknown;
        try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
        if (response.ok) return data as T;
        if (![429, 500, 502, 503, 504].includes(response.status) || attempt === maxRetries) throw new LcpHttpError(response.status, data);
        await new Promise((resolve) => setTimeout(resolve, 2 ** attempt * 1000));
      } catch (error) {
        lastError = error;
        if (error instanceof LcpHttpError || attempt === maxRetries) throw error;
        await new Promise((resolve) => setTimeout(resolve, 2 ** attempt * 1000));
      } finally { clearTimeout(timer); }
    }
    throw lastError ?? new Error("LCP request failed");
  }

  submitLead<T = Record<string, unknown>>(envelope: LcpEnvelope, options?: { test?: boolean }): Promise<T> { validateEnvelope(envelope); return this.request("POST", "/v1/lcp/leads", envelope, options); }
  submitCall<T = Record<string, unknown>>(envelope: LcpEnvelope, options?: { test?: boolean }): Promise<T> { validateEnvelope(envelope); return this.request("POST", "/v1/lcp/calls", envelope, options); }
  submitBid<T = Record<string, unknown>>(envelope: LcpEnvelope, options?: { test?: boolean }): Promise<T> { validateEnvelope(envelope); return this.request("POST", "/v1/lcp/bids", envelope, options); }

  /**
   * Submit up to MAX_BATCH_ITEMS lead items in one request (LEP-0001 W2-01).
   *
   * Each item carries its own `item_id`, `idempotency_key`, and lead `payload`
   * (schemas/batch.json). Returns the per-item receipts in input order; HTTP 207
   * partial success is a successful response, and per-item `error` objects are
   * preserved. The batch is never retried as a whole — retrying only
   * `rejected`/`error` receipts is the caller's job.
   */
  async submitLeadsBatch(items: LcpBatchItem[], requestId?: string): Promise<LcpBatchResult[]> {
    if (items.length === 0 || items.length > MAX_BATCH_ITEMS) throw new Error(`batch supports 1..${MAX_BATCH_ITEMS} items, got ${items.length}`);
    const response = await this.request<{ results?: LcpBatchResult[] }>("POST", "/v1/lcp/leads/batch", { request_id: requestId ?? crypto.randomUUID(), items }, { maxRetries: 0 });
    if (!Array.isArray(response.results)) throw new Error("batch response missing results");
    const order = new Map(items.map((item, index) => [item.item_id, index]));
    return response.results
      .map((result, index) => ({ result, index }))
      .sort((left, right) => (order.get(left.result.item_id) ?? items.length) - (order.get(right.result.item_id) ?? items.length) || left.index - right.index)
      .map(({ result }) => result);
  }
  queryLeadStatus<T = Record<string, unknown>>(leadId: string): Promise<T> { return this.request("GET", `/v1/lcp/leads/${encodeURIComponent(leadId)}`); }
  getSchema<T = Record<string, unknown>>(name: string): Promise<T> { return this.request("GET", `/v1/lcp/schemas/${name.split("/").map(encodeURIComponent).join("/")}`); }
  getCapabilities<T = Record<string, unknown>>(): Promise<T> { return this.request("GET", "/v1/lcp/capabilities"); }
  listOffers<T = Record<string, unknown>>(vertical?: string): Promise<T> { return this.request("GET", "/v1/lcp/offers" + (vertical ? `?vertical=${encodeURIComponent(vertical)}` : "")); }
}

export async function verifyHttpRequest(secret: string | Uint8Array, headers: Headers | Record<string, string>, body: Uint8Array, maxSkewSeconds = 300): Promise<void> {
  const get = (name: string) => headers instanceof Headers ? headers.get(name) ?? undefined : Object.entries(headers).find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1];
  const signature = get("X-LCP-Signature");
  const timestamp = get("X-LCP-Timestamp");
  const key = get("X-LCP-Idempotency-Key");
  if (!signature || !timestamp || !key) throw new Error("Missing required LCP authentication headers");
  await verifyHmac(secret, signature, timestamp, key, body, maxSkewSeconds);
}
