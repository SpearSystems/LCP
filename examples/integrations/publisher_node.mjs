#!/usr/bin/env node
/**
 * Send one synthetic/example lead to an LCP endpoint.
 * Requires Node.js 18+ (built-in fetch and crypto only).
 * Required: LCP_ENDPOINT, LCP_SENDER_ID, LCP_RECEIVER_ID, LCP_HMAC_SECRET.
 */

import crypto from "node:crypto";

const required = (name) => {
  const value = process.env[name];
  if (!value) throw new Error(`Missing ${name}`);
  return value;
};

const endpoint = required("LCP_ENDPOINT").replace(/\/$/, "");
const senderId = required("LCP_SENDER_ID");
const receiverId = required("LCP_RECEIVER_ID");
const secret = required("LCP_HMAC_SECRET");
const test = /^(1|true|yes|on)$/i.test(process.env.LCP_TEST_MODE ?? "");
const now = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
const messageId = crypto.randomUUID();
const idempotencyKey = `${senderId}-lead-${messageId}`;

const envelope = {
  lcp: {
    version: "1.0.0",
    message: {
      id: messageId,
      type: "lead",
      timestamp: now,
      sender_id: senderId,
      receiver_id: receiverId,
      correlation_id: null,
      idempotency_key: idempotencyKey,
      test,
    },
    payload: {
      lead_id: `example-lead-${messageId}`,
      external_id: `source-${messageId}`,
      submitted_at: now,
      status: "NEW",
      channel: "form",
      consumer: {
        full_name: "Synthetic Example",
        phone: "+61412345678",
        email: "synthetic@example.invalid",
      },
      location: {
        country_code: "AU",
        state_region: "NSW",
        postal_code: "2000",
      },
      compliance: {
        consent_timestamp: now,
        consent_purposes: ["calls", "email", "share_with_partners"],
        consent_evidence: [{
          type: "example_consent",
          provider: "synthetic_fixture",
          token_or_url: "fixture-consent-001",
        }],
      },
      provenance: {
        source_type: "publisher",
        acquisition_method: "paid_ad",
        platform_source: "synthetic_fixture",
      },
      attributes: {
        vertical: "mortgage",
        schema_version: "1.0.0",
        loan_type: "refinance",
        loan_amount_band: "500k_750k",
      },
    },
  },
};

const body = Buffer.from(JSON.stringify(envelope));
const signingInput = Buffer.concat([
  Buffer.from(`${now}\n${idempotencyKey}\n`, "utf8"),
  body,
]);
const signature = crypto.createHmac("sha256", secret).update(signingInput).digest("hex");
const headers = {
  "Content-Type": "application/json",
  Accept: "application/json",
  "X-LCP-Sender-Id": senderId,
  "X-LCP-Timestamp": now,
  "X-LCP-Idempotency-Key": idempotencyKey,
  "X-LCP-Signature": signature,
};
if (test) headers["X-LCP-Test"] = "true";

const response = await fetch(`${endpoint}/v1/lcp/leads`, {
  method: "POST",
  headers,
  body,
});
const text = await response.text();
console.log(text);
if (!response.ok) process.exitCode = 1;
