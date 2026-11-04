// example — not for production without review: a reviewed starter for a
// Supabase Edge Function that turns a form submission into an LCP lead.
//
// Platform: Supabase (Lovable forms commonly store their submissions here)
// Runtime:  Deno (Supabase Edge Functions)
// Deploy:   supabase functions deploy lcp-lead --no-verify-jwt
//           (drop the flag to keep Supabase's JWT gate, in which case the
//           browser must also send the project anon key as a Bearer token)
//
// Secrets (never in the browser bundle, never in this file):
//   supabase secrets set \
//     LCP_ENDPOINT=https://platform.example \
//     LCP_SENDER_ID=publisher_001 \
//     LCP_HMAC_SECRET='read-from-a-secret-manager' \
//     LCP_RECEIVER_ID=platform_001
//
// LCP_HMAC_SECRET may be omitted only if you use the optional Bearer fallback
// below and your platform operator provisioned an API key for the sender.
//
// Wire contract: POST {LCP_ENDPOINT}/v1/lcp/leads with the envelope in the
// body and the canonical HMAC headers (spec/01-transport.md §9). The signature
// input is exactly:
//
//   <timestamp>\n<idempotency-key>\n<raw request body bytes>
//
// The timestamp is whole-second UTC with a trailing Z, byte-identical between
// the header and the signature input.

const LCP_VERSION = "1.0.0";

// EDIT THESE TWO LINES for your vertical (see verticals/*.json). Both are
// required by payload.attributes: { vertical, schema_version }.
const VERTICAL = "mortgage";
const SCHEMA_VERSION = "1.0.0";

// Receiver identity. Set DEFAULT_RECEIVER_ID when the platform told you a
// fixed receiver id (e.g. "spx"); otherwise leave it empty and supply
// LCP_RECEIVER_ID as a secret.
const DEFAULT_RECEIVER_ID = "";

// CORS: the browser calls this function, not the platform. Set
// LCP_ALLOWED_ORIGIN to your site origin in production.
const DEFAULT_ALLOWED_ORIGIN = "*";

type Json = Record<string, unknown>;

interface LcpError {
  code: string;
  name?: string;
  message: string;
  field?: string;
}

function ok(body: Json, status = 200, origin = DEFAULT_ALLOWED_ORIGIN): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json",
      "access-control-allow-origin": origin,
      "access-control-allow-headers": "content-type, authorization",
      "access-control-allow-methods": "POST, OPTIONS",
    },
  });
}

function fail(
  status: number,
  errors: LcpError[],
  origin = DEFAULT_ALLOWED_ORIGIN,
): Response {
  return ok({ errors }, status, origin);
}

function requireEnv(name: string): string {
  const value = Deno.env.get(name)?.trim();
  if (!value) throw new Error(`Missing required function secret ${name}`);
  return value;
}

function str(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed;
}

function hex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return hex(digest);
}

async function hmacHex(secret: string, value: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return hex(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value)));
}

/** Whole-second UTC wire form: YYYY-MM-DDTHH:MM:SSZ. */
function lcpTimestamp(date = new Date()): string {
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** Canonical date-time, or undefined when the value is not parseable. */
function dateTime(value: string | undefined): string | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? undefined : lcpTimestamp(parsed);
}

/** E.164 or nothing. Local formats are rejected rather than guessed. */
function e164(value: string | undefined): string | undefined {
  if (!value) return undefined;
  const compact = value.replace(/[\s().-]/g, "");
  return /^\+[1-9]\d{1,14}$/.test(compact) ? compact : undefined;
}

function attribution(source: Json): Json {
  const map: [string, string][] = [
    ["source_type", "source_type"],
    ["acquisition_method", "acquisition_method"],
    ["campaign_id", "campaign_id"],
    ["creative_id", "creative_id"],
    ["form_id", "form_id"],
    ["flow_key", "flow_key"],
    ["funnel_key", "funnel_key"],
    ["source_url", "landing_page_url"],
    ["landing_page_url", "landing_page_url"],
  ];
  const out: Json = {};
  for (const [from, to] of map) {
    const value = str(source[from]);
    if (value !== undefined) out[to] = value;
  }
  return out;
}

Deno.serve(async (req: Request): Promise<Response> => {
  const origin = Deno.env.get("LCP_ALLOWED_ORIGIN")?.trim() ||
    DEFAULT_ALLOWED_ORIGIN;

  if (req.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        "access-control-allow-origin": origin,
        "access-control-allow-headers": "content-type, authorization",
        "access-control-allow-methods": "POST, OPTIONS",
      },
    });
  }
  if (req.method !== "POST") {
    return fail(405, [{
      code: "LCP-001",
      name: "INVALID_FORMAT",
      message: "Use POST with a JSON form submission",
    }], origin);
  }

  let form: Json;
  try {
    const parsed = await req.json();
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new Error("body is not a JSON object");
    }
    form = parsed as Json;
  } catch {
    return fail(400, [{
      code: "LCP-001",
      name: "INVALID_FORMAT",
      message: "Body must be a single JSON object of form fields",
    }], origin);
  }

  // ---- configuration (fail loudly, never send a half-built lead) ----------
  let endpoint: string;
  let senderId: string;
  let receiverId: string;
  try {
    endpoint = requireEnv("LCP_ENDPOINT").replace(/\/+$/, "");
    senderId = requireEnv("LCP_SENDER_ID");
    receiverId = Deno.env.get("LCP_RECEIVER_ID")?.trim() || DEFAULT_RECEIVER_ID;
    if (!receiverId) {
      throw new Error(
        "Set LCP_RECEIVER_ID (or DEFAULT_RECEIVER_ID) to the platform receiver id",
      );
    }
  } catch (err) {
    return fail(500, [{
      code: "LCP-500",
      name: "INTERNAL_ERROR",
      message: err instanceof Error ? err.message : "Function is not configured",
    }], origin);
  }

  // ---- field mapping ------------------------------------------------------
  // Accept the flat column names a form table stores. Anything unrecognised
  // is ignored on purpose: an unknown key must not leak into the envelope,
  // whose schemas reject additional properties.
  const email = str(form.email);
  const phoneRaw = str(form.phone);
  const phone = e164(phoneRaw);
  if (phoneRaw && !phone) {
    return fail(400, [{
      code: "LCP-009",
      name: "INVALID_PHONE",
      message:
        "phone must be E.164, e.g. +61412345678. Convert local formats before sending.",
      field: "consumer.phone",
    }], origin);
  }

  const fullName = str(form.full_name) ?? str(form.name);
  const firstName = str(form.first_name);
  const lastName = str(form.last_name);
  if (!email && !phone && !(firstName && lastName) && !fullName) {
    return fail(400, [{
      code: "LCP-003",
      name: "MISSING_REQUIRED_FIELD",
      message:
        "Provide an email, an E.164 phone, or a first/last name (or full_name)",
      field: "consumer",
    }], origin);
  }

  const countryCode = (str(form.country_code) ?? "").toUpperCase();
  const stateRegion = str(form.state_region);
  const postalCode = str(form.postal_code);
  if (!/^[A-Z]{2}$/.test(countryCode)) {
    return fail(400, [{
      code: "LCP-003",
      name: "MISSING_REQUIRED_FIELD",
      message: "Provide country_code as ISO 3166-1 alpha-2, e.g. AU",
      field: "location.country_code",
    }], origin);
  }
  if (!stateRegion && !postalCode) {
    return fail(400, [{
      code: "LCP-010",
      name: "INVALID_TERRITORY",
      message: "Provide state_region or postal_code for the location block",
      field: "location",
    }], origin);
  }

  const submittedAt = dateTime(str(form.submitted_at)) ?? lcpTimestamp();
  const submissionId = str(form.submission_id) ?? str(form.id);
  const formId = str(form.form_id);
  // Stable per submission: a retry of the same row reuses the key and the
  // platform answers DUPLICATE instead of creating a second lead.
  const sourceKey = submissionId ??
    (await sha256Hex(
      [email, phone, fullName ?? `${firstName ?? ""} ${lastName ?? ""}`,
        submittedAt].join("|"),
    )).slice(0, 32);
  const idempotencyKey = `${senderId}-${formId ?? "form"}-${sourceKey}`;

  const consumer: Json = {};
  if (firstName) consumer.first_name = firstName;
  if (lastName) consumer.last_name = lastName;
  if (!firstName && !lastName && fullName) consumer.full_name = fullName;
  if (email) consumer.email = email;
  if (phone) consumer.phone = phone;
  const locale = str(form.locale)?.replace(/_/g, "-");
  // BCP-47 shape from schemas/core.json; an unparseable locale drops out
  // rather than failing the whole lead on a cosmetic field.
  if (locale && /^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$/.test(locale)) {
    consumer.locale = locale;
  }

  const location: Json = { country_code: countryCode };
  if (stateRegion) location.state_region = stateRegion;
  if (postalCode) location.postal_code = postalCode;
  const city = str(form.city);
  if (city) location.city = city;
  const timezone = str(form.timezone);
  if (timezone) location.timezone = timezone;

  const provenance: Json = {
    received_at: lcpTimestamp(),
    source_type: str(form.source_type) ?? "publisher",
    ...attribution(form),
  };
  if (!provenance.platform_source) provenance.platform_source = "supabase";

  // Captured server-side: the edge runtime sees the caller, the browser cannot
  // be trusted to report it.
  const forwarded = req.headers.get("x-forwarded-for") ?? "";
  const clientIp = forwarded.split(",")[0]?.trim();
  if (clientIp) provenance.ip_address = clientIp;
  const userAgent = req.headers.get("user-agent");
  if (userAgent) {
    // The spec does not pin a UA preimage; a plain SHA-256 keeps it comparable
    // without storing the raw string.
    provenance.user_agent_hash = await sha256Hex(userAgent);
  }

  const compliance: Json = {};
  const consentTimestamp = dateTime(str(form.consent_timestamp));
  const consentUrl = str(form.consent_source_url);
  if (consentTimestamp) compliance.consent_timestamp = consentTimestamp;
  if (consentUrl) compliance.consent_source_url = consentUrl;
  const consentTextVersion = str(form.consent_text_version);
  if (consentTextVersion) compliance.consent_text_version = consentTextVersion;
  const purposes = form.consent_purposes;
  if (Array.isArray(purposes) && purposes.every((p) => typeof p === "string")) {
    compliance.consent_purposes = purposes;
  }
  const evidenceRef = str(form.consent_evidence_id);
  if (evidenceRef) {
    compliance.consent_evidence = [{
      type: str(form.consent_type) ?? "verified_consent",
      provider: str(form.consent_provider) ?? "form",
      token_or_url: evidenceRef,
    }];
  }

  const attributes: Json = {
    vertical: str(form.vertical) ?? VERTICAL,
    schema_version: str(form.schema_version) ?? SCHEMA_VERSION,
  };
  const verticalAnswers = form.attributes;
  if (
    verticalAnswers && typeof verticalAnswers === "object" &&
    !Array.isArray(verticalAnswers)
  ) {
    for (const [key, value] of Object.entries(verticalAnswers as Json)) {
      if (key === "vertical" || key === "schema_version") continue;
      if (key in attributes) continue;
      attributes[key] = value;
    }
  }

  // Test mode: the envelope flag and the header must agree. Set
  // LCP_TEST_MODE=false only after the first sandbox round trip is clean.
  const testMode = (Deno.env.get("LCP_TEST_MODE") ?? "true").toLowerCase() !==
    "false";
  // One timestamp string, used for the header and signed byte-for-byte.
  const timestamp = lcpTimestamp();
  const messageId = crypto.randomUUID();
  const envelope = {
    lcp: {
      version: LCP_VERSION,
      message: {
        id: messageId,
        type: "lead",
        timestamp,
        sender_id: senderId,
        receiver_id: receiverId,
        correlation_id: null,
        idempotency_key: idempotencyKey,
        test: testMode,
      },
      payload: {
        lead_id: `lead_${messageId}`,
        external_id: submissionId ?? messageId,
        submitted_at: submittedAt,
        status: str(form.status) ?? "NEW",
        channel: str(form.channel) ?? "form",
        consumer,
        location,
        ...(Object.keys(compliance).length > 0 ? { compliance } : {}),
        provenance,
        attributes,
      },
    },
  };

  const body = JSON.stringify(envelope);

  const headers: Record<string, string> = {
    "content-type": "application/json",
    accept: "application/json",
    "X-LCP-Sender-Id": senderId,
    "X-LCP-Timestamp": timestamp,
    "X-LCP-Idempotency-Key": idempotencyKey,
  };
  if (testMode) headers["X-LCP-Test"] = "true";

  const secret = Deno.env.get("LCP_HMAC_SECRET")?.trim();
  if (secret) {
    headers["X-LCP-Signature"] = await hmacHex(
      secret,
      `${timestamp}\n${idempotencyKey}\n${body}`,
    );
  } else {
    const bearer = Deno.env.get("LCP_API_KEY")?.trim();
    if (!bearer) {
      return fail(500, [{
        code: "LCP-500",
        name: "INTERNAL_ERROR",
        message: "Set LCP_HMAC_SECRET (recommended) or LCP_API_KEY",
      }], origin);
    }
    headers["authorization"] = `Bearer ${bearer}`;
  }

  let response: Response;
  try {
    response = await fetch(`${endpoint}/v1/lcp/leads`, {
      method: "POST",
      headers,
      body,
    });
  } catch (err) {
    // The platform did not answer. Retrying with the same idempotency key is
    // safe; surface the failure so the caller can queue it.
    return fail(502, [{
      code: "LCP-500",
      name: "INTERNAL_ERROR",
      message: `Could not reach the LCP platform: ${err instanceof Error ? err.message : "network error"
        }. Retry with idempotency_key ${idempotencyKey}.`,
    }], origin);
  }

  // Return the platform ack verbatim: it is the authoritative answer, including
  // DUPLICATE, REJECTED with rejection_reason, and per-field errors[].
  const text = await response.text();
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    try {
      return ok(JSON.parse(text) as Json, response.status, origin);
    } catch {
      // fall through to the raw-body branch
    }
  }
  return ok({
    errors: [{
      code: "LCP-500",
      name: "INTERNAL_ERROR",
      message: `Platform returned HTTP ${response.status} with a non-JSON body`,
      details: { body: text.slice(0, 500) },
    }],
  }, 502, origin);
});
