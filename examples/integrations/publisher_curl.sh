#!/usr/bin/env bash
# Submit an existing LCP JSON envelope with canonical HMAC signing.
# Requires: curl, openssl, and python3.
# Required: LCP_ENDPOINT, LCP_SENDER_ID, LCP_HMAC_SECRET.
# Optional: LCP_BODY (default: examples/lead.json), LCP_TEST_MODE=true.
set -euo pipefail

: "${LCP_ENDPOINT:?Set LCP_ENDPOINT}"
: "${LCP_SENDER_ID:?Set LCP_SENDER_ID}"
: "${LCP_HMAC_SECRET:?Set LCP_HMAC_SECRET}"
body_file="${LCP_BODY:-examples/lead.json}"
body_sender_id="$(python3 - "$body_file" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["lcp"]["message"]["sender_id"])
PY
)"
if [[ "$body_sender_id" != "$LCP_SENDER_ID" ]]; then
  printf 'LCP_SENDER_ID (%s) must match the envelope sender_id (%s); use a matching body or set LCP_BODY.\n' \\
    "$LCP_SENDER_ID" "$body_sender_id" >&2
  exit 2
fi
timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
idempotency_key="$(python3 - "$body_file" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["lcp"]["message"]["idempotency_key"])
PY
)"

# Sign exactly: timestamp + LF + idempotency key + LF + raw body bytes.
signature="$({
  printf '%s\n%s\n' "$timestamp" "$idempotency_key"
  cat "$body_file"
} | openssl dgst -sha256 -hmac "$LCP_HMAC_SECRET" -hex | awk '{print $2}')"

args=(
  -X POST "${LCP_ENDPOINT%/}/v1/lcp/leads"
  -H 'Content-Type: application/json'
  -H "X-LCP-Sender-Id: $LCP_SENDER_ID"
  -H "X-LCP-Timestamp: $timestamp"
  -H "X-LCP-Idempotency-Key: $idempotency_key"
  -H "X-LCP-Signature: $signature"
  --data-binary "@$body_file"
)
if [[ "${LCP_TEST_MODE:-false}" =~ ^(1|true|yes|on)$ ]]; then
  args+=( -H 'X-LCP-Test: true' )
fi

curl --fail-with-body "${args[@]}"
printf '\n'
