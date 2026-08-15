export { LcpSchemaValidationError, LcpSchemaValidator, type SchemaBundle } from "./validation.js";

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

export async function signHmac(secret: string | Uint8Array, timestamp: string, idempotencyKey: string | undefined, body: Uint8Array): Promise<string> {
  const key = await globalThis.crypto.subtle.importKey("raw", asBufferSource(toBytes(secret)), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
  return hex(await globalThis.crypto.subtle.sign("HMAC", key, asBufferSource(canonicalSigningBytes(timestamp, idempotencyKey, body))));
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

export class LcpClient {
  private readonly endpoint: string;
  private readonly options: Required<Pick<LcpClientOptions, "timeoutMs" | "maxRetries">> & LcpClientOptions;

  constructor(endpoint: string, options: LcpClientOptions = {}) {
    this.endpoint = endpoint.replace(/\/$/, "");
    this.options = { timeoutMs: 30_000, maxRetries: 2, ...options };
  }

  async request<T = Record<string, unknown>>(method: string, path: string, payload?: unknown, options: { idempotencyKey?: string; test?: boolean } = {}): Promise<T> {
    const body = payload === undefined ? new Uint8Array() : new TextEncoder().encode(JSON.stringify(payload));
    const idempotencyKey = options.idempotencyKey ?? (payload as LcpEnvelope | undefined)?.lcp?.message?.idempotency_key;
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
    for (let attempt = 0; attempt <= this.options.maxRetries; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.options.timeoutMs);
      try {
        const response = await fetch(`${this.endpoint}/${path.replace(/^\//, "")}`, { method, headers, body: body.length ? body : undefined, signal: controller.signal });
        const text = await response.text();
        let data: unknown;
        try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
        if (response.ok) return data as T;
        if (![429, 500, 502, 503, 504].includes(response.status) || attempt === this.options.maxRetries) throw new LcpHttpError(response.status, data);
        await new Promise((resolve) => setTimeout(resolve, 2 ** attempt * 1000));
      } catch (error) {
        lastError = error;
        if (error instanceof LcpHttpError || attempt === this.options.maxRetries) throw error;
        await new Promise((resolve) => setTimeout(resolve, 2 ** attempt * 1000));
      } finally { clearTimeout(timer); }
    }
    throw lastError ?? new Error("LCP request failed");
  }

  submitLead<T = Record<string, unknown>>(envelope: LcpEnvelope, options?: { test?: boolean }): Promise<T> { validateEnvelope(envelope); return this.request("POST", "/v1/lcp/leads", envelope, options); }
  submitCall<T = Record<string, unknown>>(envelope: LcpEnvelope, options?: { test?: boolean }): Promise<T> { validateEnvelope(envelope); return this.request("POST", "/v1/lcp/calls", envelope, options); }
  submitBid<T = Record<string, unknown>>(envelope: LcpEnvelope, options?: { test?: boolean }): Promise<T> { validateEnvelope(envelope); return this.request("POST", "/v1/lcp/bids", envelope, options); }
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
