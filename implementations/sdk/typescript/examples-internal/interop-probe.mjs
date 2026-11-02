#!/usr/bin/env node
// TypeScript SDK interop probe for tools/sdk_interop_matrix.py (LEP-0002 step 1).
//
// Signs the fixed vector message through the SDK's own canonicalSigningBytes +
// signHmac and formats utcTimestamp() against a frozen clock (epoch
// 1786789200123 ms, non-zero sub-second). Emits one `LCP_INTEROP {json}` line;
// never asserts, never fails on shape.

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { existsSync } from "node:fs";
import path from "node:path";
import { canonicalSigningBytes, signHmac, utcTimestamp } from "../dist/index.js";

const FROZEN_MS = 1_786_789_200_123; // 2026-08-15T10:20:00.123Z

function resolveVectorPath() {
  const argvIndex = process.argv.indexOf("--vector");
  if (argvIndex !== -1 && process.argv[argvIndex + 1]) return process.argv[argvIndex + 1];
  if (process.env.LCP_VECTOR) return process.env.LCP_VECTOR;
  let directory = process.cwd();
  for (; ;) {
    const candidate = path.join(directory, "test-vectors", "sdk", "hmac.json");
    if (existsSync(candidate)) return candidate;
    const parent = path.dirname(directory);
    if (parent === directory) throw new Error("hmac.json vector not found (pass --vector)");
    directory = parent;
  }
}

// utcTimestamp() reads the wall clock via `new Date()`; freezing the global
// Date constructor (no-arg form only) drives the SDK's real generator at a
// frozen sub-second instant without changing its code.
const RealDate = Date;
class FrozenDate extends RealDate {
  constructor(...args) {
    if (args.length === 0) super(FROZEN_MS);
    else super(...args);
  }
}
globalThis.Date = FrozenDate;

const vector = JSON.parse(readFileSync(resolveVectorPath(), "utf8"));
const body = new TextEncoder().encode(vector.body_utf8);
const canonical = canonicalSigningBytes(vector.timestamp, vector.idempotency_key, body);
const signatureHex = await signHmac(vector.secret, vector.timestamp, vector.idempotency_key, body);
const emittedTimestamp = utcTimestamp();

globalThis.Date = RealDate;

const result = {
  sdk: "typescript",
  canonical_sha256: createHash("sha256").update(canonical).digest("hex"),
  signature_hex: signatureHex,
  emitted_timestamp: emittedTimestamp,
};
// Logging hygiene: implementations/** must not pass serialized payloads to
// print/console/logging calls (tools/check_logging_hygiene.py Rule 2 — no
// stderr allowance for console.*). A direct stderr stream write is the same
// channel the Go/Python probes use; LCP_INTEROP_OUT is the file channel.
const payload = JSON.stringify(result, Object.keys(result).sort());
process.stderr.write("LCP_INTEROP " + payload + "\n");
if (process.env.LCP_INTEROP_OUT) {
  writeFileSync(process.env.LCP_INTEROP_OUT, JSON.stringify(result) + "\n");
}
