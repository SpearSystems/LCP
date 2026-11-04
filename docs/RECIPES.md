# No-code and low-code recipes

> **Integration recipes · for marketers.** Five platform recipes for getting a
> lead from a form builder, CRM, automation tool, or spreadsheet into an LCP
> platform without hand-writing the payload. Each recipe names the fields to
> map, the click path, the headers, a test-mode first run, and the three
> failures people hit most often.
>
> Working files for these recipes live in
> [`examples/integrations/no-code/`](../examples/integrations/no-code/).
> They are reviewed starters, not one-click marketplace apps — see the
> [README](../examples/integrations/no-code/README.md) for what is and is not
> shipped.

## 0. What you need before you touch any tool

Ask the platform operator for these seven facts and write them down once:

| # | Fact | Example | Where it goes |
|---:|---|---|---|
| 1 | LCP base URL | `https://platform.example` | request URL, plus `/v1/lcp/leads` |
| 2 | Sandbox base URL | `https://sandbox.example` | same, for the first run |
| 3 | Your sender id (publisher id) | `publisher_001` | `lcp.message.sender_id` |
| 4 | Their receiver id | `platform_001` | `lcp.message.receiver_id` |
| 5 | Auth method | Bearer API key, or HMAC secret | `Authorization` header, or `X-LCP-Signature` |
| 6 | Vertical and schema version | `mortgage`, `1.0.0` | `payload.attributes` |
| 7 | Accepted countries and consent purposes | `AU`, `calls,email,share_with_partners` | `location`, `compliance` |

The credentials are per environment. A sandbox key is not a production key,
and mixing them is how test leads reach real buyers.

### The envelope, in one picture

Every recipe sends the same outer shape. Message type is always `lead`; the
`payload` is the lead itself.

```json
{
  "lcp": {
    "version": "1.0.0",
    "message": {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "type": "lead",
      "timestamp": "2026-11-04T10:20:00Z",
      "sender_id": "publisher_001",
      "receiver_id": "platform_001",
      "correlation_id": null,
      "idempotency_key": "submission-8891",
      "test": true
    },
    "payload": {
      "lead_id": "lead-submission-8891",
      "external_id": "submission-8891",
      "submitted_at": "2026-11-04T10:19:50Z",
      "status": "NEW",
      "channel": "form",
      "consumer": {
        "first_name": "Jane",
        "last_name": "Smith",
        "email": "jane.smith@example.com",
        "phone": "+61412345678"
      },
      "location": {
        "country_code": "AU",
        "state_region": "NSW",
        "postal_code": "2000"
      },
      "compliance": {
        "consent_timestamp": "2026-11-04T10:19:45Z",
        "consent_source_url": "https://example.com/quote",
        "consent_purposes": ["calls", "email", "share_with_partners"]
      },
      "provenance": {
        "source_type": "publisher",
        "campaign_id": "camp_mortgage_nsw"
      },
      "attributes": {
        "vertical": "mortgage",
        "schema_version": "1.0.0"
      }
    }
  }
}
```

Validated against [`schemas/envelope.json`](../schemas/envelope.json) and
[`schemas/lead.json`](../schemas/lead.json). Fields not shown are optional;
fields shown are the smallest set that passes validation for a form lead.

### The field map every recipe reuses

| Your form field | LCP field | Rule |
|---|---|---|
| Email | `payload.consumer.email` | Plain address. One of email, phone, or a name is required. |
| Phone | `payload.consumer.phone` | **E.164 only**: `+61412345678`. Spaces, brackets, dashes, and `0` prefixes must be converted before sending. |
| First / last name | `payload.consumer.first_name`, `.last_name` | Send both, or send `full_name` instead. |
| Country | `payload.location.country_code` | ISO 3166-1 alpha-2, uppercase: `AU`, `US`, `GB`. |
| State / region | `payload.location.state_region` | Required when there is no postal code. |
| Postal code | `payload.location.postal_code` | Required when there is no state/region. |
| Consent checkbox | `payload.compliance.consent_timestamp` | The moment it was ticked. |
| Consent page | `payload.compliance.consent_source_url` | The page the consent text appeared on. |
| Campaign / UTM | `payload.provenance.campaign_id` | Attribution, not credentials. |
| Submission / row id | `lcp.message.idempotency_key`, `payload.external_id` | **The same stable value** both places. This is what makes a retry safe. |

Two mapping rules that cause most of the trouble:

- **Never invent consent.** If the form does not collect a real consent
  checkbox with a real page URL, leave the `compliance` block out and tell the
  operator. A fabricated consent block is a compliance failure on their side,
  and buyers reject those leads.
- **Never send an empty string.** An empty `phone`, `email`, or `country_code`
  fails format validation. Delete the key instead.

### The headers (both auth styles)

HMAC, the recommended server-side style. The signature is
`HMAC-SHA256(secret, timestamp + "\n" + idempotency_key + "\n" + raw body)`,
lowercase hex:

```text
Content-Type: application/json
X-LCP-Sender-Id: publisher_001
X-LCP-Timestamp: 2026-11-04T10:20:00Z
X-LCP-Idempotency-Key: submission-8891
X-LCP-Signature: 9f2c...64 hex chars...
X-LCP-Test: true
```

Bearer, for tools that cannot compute an HMAC:

```text
Content-Type: application/json
Authorization: Bearer <api-key>
X-LCP-Idempotency-Key: submission-8891
X-LCP-Test: true
```

Rules that apply to both:

- `X-LCP-Idempotency-Key` must equal `lcp.message.idempotency_key` in the body.
- While testing, `lcp.message.test` must be `true` **and** `X-LCP-Test: true`
  must be sent; the two must agree.
- The HMAC timestamp is signed exactly as transmitted: whole seconds, UTC,
  trailing `Z`, no fractional part. Never re-format it between signing and
  sending.
- `X-LCP-Test` filters traffic at the infrastructure layer. Sending it while
  the body says `"test": false` is a rejection.

---

## 1. Lovable + Supabase

**Use this when** your form is built in Lovable and its submissions land in a
Supabase table. The browser must not hold the platform key, so the form calls
a Supabase Edge Function, and the function signs and forwards the lead.

### What you need

| Item | Where it comes from |
|---|---|
| Supabase project with Edge Functions enabled | Your Supabase account |
| Lovable form writing to a Supabase table | The Lovable Supabase integration |
| `LCP_ENDPOINT`, `LCP_SENDER_ID`, `LCP_RECEIVER_ID` | Platform operator |
| `LCP_HMAC_SECRET` | Platform operator (or `LCP_API_KEY` if they only issue Bearer) |
| Vertical and schema version | Platform operator |

### Fields to map

The function reads flat column names from the submission JSON:

| Form column | Envelope field |
|---|---|
| `email` | `payload.consumer.email` |
| `phone` | `payload.consumer.phone` (E.164) |
| `first_name`, `last_name` | `payload.consumer.*` |
| `country_code` | `payload.location.country_code` |
| `state_region` **or** `postal_code` | `payload.location.*` |
| `city` | `payload.location.city` |
| `consent_timestamp`, `consent_source_url`, `consent_purposes` | `payload.compliance.*` |
| `submission_id` (or the table's `id`) | `lcp.message.idempotency_key`, `payload.external_id` |
| `campaign_id`, `form_id`, `source_url` | `payload.provenance.*` |
| `attributes` (optional object) | merged into `payload.attributes` |

Client IP and user agent are captured **server-side** by the function from the
request headers; do not try to send them from the browser. `lead_id` and
`message.id` are generated by the function.

### Click path

Supabase dashboard labels move between releases; the names below are the ones
in the current UI, and the CLI commands are stable.

1. **Lovable:** connect Supabase, publish the form, submit one test entry, and
   confirm the row exists in the Supabase table editor. Note the exact column
   names — the function reads those names.
2. **Supabase → Edge Functions → Deploy a new function** (or in a terminal,
   from the project directory, `supabase functions deploy lcp-lead`).
   Paste [`supabase-edge-function.ts`](../examples/integrations/no-code/supabase-edge-function.ts)
   as the function source (the function is named `lcp-lead`). The default
   deploy keeps Supabase's JWT gate, so the browser must also send the project
   anon key; `--no-verify-jwt` opens the function and leaves the origin check
   and platform-side rate limiting as the only guards.
3. **Set secrets**, never in the function file:
   `supabase secrets set LCP_ENDPOINT=... LCP_SENDER_ID=... LCP_RECEIVER_ID=... LCP_HMAC_SECRET=... LCP_TEST_MODE=true`
   set these per project; a secret set locally does nothing to the deployed
   function.
4. **Set the vertical** by editing the two constants at the top of the file
   (`VERTICAL`, `SCHEMA_VERSION`) before deploying, or send `vertical` and
   `schema_version` in the submission JSON.
5. **Set `LCP_ALLOWED_ORIGIN`** to your Lovable site origin before going live,
   so the function is not open to every website.
6. **Lovable:** point the form submit action at the function URL
   `https://<project-ref>.supabase.co/functions/v1/lcp-lead`.

### Headers

The browser sends `Content-Type: application/json`, plus the Supabase anon key
as a Bearer token when the function keeps its JWT gate on. The function itself
adds the LCP headers from its secrets — see
[the headers section](#the-headers-both-auth-styles).

### Test-mode first run

1. Deploy with `LCP_TEST_MODE=true` (the default) and the **sandbox**
   `LCP_ENDPOINT`.
2. Submit the form once, or call the function directly:
   `curl -i -X POST "<function-url>" -H "Content-Type: application/json" -d '{"submission_id":"test-1","email":"you@example.com","phone":"+61412345678","first_name":"Test","last_name":"Lead","country_code":"AU","state_region":"NSW","consent_timestamp":"2026-11-04T10:00:00Z","consent_source_url":"https://example.com/quote","campaign_id":"test"}'`
3. Accept only an HTTP 2xx whose ack `status` is `RECEIVED`, `VALIDATED`,
   `ACCEPTED`, or `PROCESSED`.
4. Submit again with the **same** `submission_id`. The ack should now say
   `DUPLICATE` — that proves the idempotency key is stable. A second `ACCEPTED`
   means two different keys were generated; check that `submission_id` is
   actually present in the row.
5. Only then set `LCP_TEST_MODE=false` and switch `LCP_ENDPOINT` to production.

### Three failures you will hit

1. **`401` with `LCP-012 SIGNATURE_INVALID`.** The timestamp signed is not
   byte-identical to the one sent: a formatter added milliseconds, or the body
   was re-serialized (key order or whitespace changed) after signing. Fix: sign
   the exact string you transmit, in the order transmitted — the shipped
   function builds the body once, then signs that same string. If you rewrote
   the function, keep the `timestamp + "\n" + key + "\n" + body` order.
2. **`400` with `LCP-009 INVALID_PHONE`.** Lovable forms often store local
   numbers (`0412 345 678`). The function rejects them on purpose instead of
   guessing a country prefix. Fix: store E.164 in the table — a
   `phone_country_code` column plus a normalization step before insert, or
   convert in the form validation. See `LCP-009` in the
   [error guide](ERROR-GUIDE.md).
3. **`500` from the function, or `401` from the platform on a valid payload.**
   The secrets are on the local machine, not on the deployed function, or the
   deployed function still holds a sandbox key while the URL points at
   production. Fix: re-run `supabase secrets set` for that project and check
   `supabase secrets list`, then redeploy. A missing secret fails loudly with
   the function's own message; a wrong secret fails on the platform as
   `LCP-002` or `LCP-012`.

See the [error guide](ERROR-GUIDE.md) for the full code list.

---

## 2. GoHighLevel

**Use this when** the lead lives in a GHL contact (form, survey, funnel,
chat widget) and a workflow should hand it to the platform.

### What you need

| Item | Where it comes from |
|---|---|
| GHL sub-account with Workflows enabled | Your GHL account |
| LCP base URL and `/v1/lcp/leads` | Platform operator |
| Sender id, receiver id | Platform operator |
| Bearer API key, or HMAC secret | Platform operator |

### Fields to map

GHL stores names in three separate fields and phones already in E.164 in most
locales — verify that, do not assume it.

| GHL field | Envelope field |
|---|---|
| `{{contact.email}}` | `payload.consumer.email` |
| `{{contact.phone}}` | `payload.consumer.phone` |
| `{{contact.first_name}}`, `{{contact.last_name}}` | `payload.consumer.first_name`, `.last_name` |
| `{{contact.country}}` | `payload.location.country_code` (convert to the two-letter code) |
| `{{contact.state}}` | `payload.location.state_region` |
| `{{contact.postal_code}}` | `payload.location.postal_code` |
| `{{contact.city}}` | `payload.location.city` |
| Custom field `LCP submission id` | `lcp.message.idempotency_key`, `payload.external_id` |
| Custom field `LCP consent timestamp` | `payload.compliance.consent_timestamp` |
| Custom field `LCP consent page` | `payload.compliance.consent_source_url` |
| `{{contact.tags}}` or a custom field | `payload.provenance.campaign_id` |

If GHL does not already hold a submission id, create a custom field and set it
once when the contact is created (a workflow "Set custom field" action with a
timestamp or the GHL contact id is enough). Without a stable value, every
retry creates a second lead.

### Click path

GHL renames workflow actions between releases ("Webhook" vs "Custom Webhook"),
so match on intent:

1. **Automation → Workflows → Create workflow**, with a trigger of
   **Form submitted** (or **Customer replied**, **Survey submitted**).
2. Add the action **Webhook** / **Custom Webhook**.
3. Set the URL to `<base>/v1/lcp/leads` and the method to `POST`.
4. Add the headers if your GHL plan exposes custom headers; if the action only
   offers URL and body, stop here and front it with Make or Zapier (see the next
   two recipes) — a Bearer key still needs an `Authorization` header.
5. Paste the envelope into the body field, replacing merge fields with GHL
   tokens (`{{contact.email}}`, `{{contact.phone}}`, custom field tokens).
6. Publish the workflow and test with a contact you create yourself.

### Headers

`Content-Type: application/json`, `Authorization: Bearer <key>`, and
`X-LCP-Idempotency-Key: {{contact.lcp_submission_id}}` when the action allows
headers. GHL cannot compute an HMAC; if your operator requires HMAC, route
through the Supabase function or Make's HTTP module.

### Test-mode first run

1. Point the webhook at the sandbox URL, send `"test": true` in the body and
   `X-LCP-Test: true` if headers are available.
2. Create a test contact with a real name, a valid E.164 phone, and a ticked
   consent custom field.
3. Open the workflow's execution history and inspect the response body: a 2xx
   with an ack status is the pass condition. GHL shows the response only for
   webhook actions; if you cannot see it, add a second action that writes the
   response to a custom field or a Google Sheet.
4. Re-run the same contact through the workflow. Expect `DUPLICATE`, not a
   second lead.

### Three failures you will hit

1. **`422` with `LCP-010 INVALID_TERRITORY`.** GHL's `{{contact.country}}` is
   often the full country name ("Australia"), and the state field can be blank.
   Fix: map to the two-letter code and require a state or postal code — see
   `LCP-010` in the [error guide](ERROR-GUIDE.md).
2. **Duplicates on workflow re-entry.** GHL re-enters contacts when a workflow
   is republished or a contact is edited. Without a stable idempotency key each
   entry is a new lead. Fix: set the submission-id custom field exactly once
   and reference it in both the header and the body.
3. **`401 LCP-002 UNKNOWN_SENDER`.** Either the key was pasted with the word
   `Bearer` already in front of it (so the header reads `Bearer Bearer ...`),
   or the key belongs to another sub-account. Fix: paste only the key value.

See the [error guide](ERROR-GUIDE.md) for the full code list.

---

## 3. Make

**Use this when** you want a visual scenario: a trigger from a form, sheet, or
webhook, an HTTP request to LCP, and branching on the ack.

### What you need

| Item | Where it comes from |
|---|---|
| Make account with the HTTP app available | Your Make account |
| A trigger module that outputs the lead fields | Your form tool, Sheets, or a webhook |
| LCP endpoint, sender id, receiver id | Platform operator |
| Bearer API key | Platform operator (HMAC needs a signer, see below) |

[`make-blueprint.json`](../examples/integrations/no-code/make-blueprint.json)
holds the module configuration: the URL, the header set, and the raw JSON body
template with `{{1...}}` tokens. It is **not** an importable blueprint — Make
blueprints carry app versions and designer metadata that only Make can export,
so this repository ships the configuration to recreate instead.

### Fields to map

Inside the body template, `{{1.field}}` refers to the trigger module (module 1
in a fresh scenario). Map:

| Trigger output | Envelope field |
|---|---|
| `email` | `payload.consumer.email` |
| `phone` | `payload.consumer.phone` |
| `first_name`, `last_name` | `payload.consumer.*` |
| `country_code` | `payload.location.country_code` |
| `state_region` / `postal_code` | `payload.location.*` |
| `consent_timestamp`, `consent_source_url` | `payload.compliance.*` |
| `submission_id` | `lcp.message.idempotency_key`, `payload.external_id`, header |
| `campaign_id` | `payload.provenance.campaign_id` |
| `vertical`, `schema_version` | `payload.attributes.*` |

`lcp.message.id` must be a UUID. Most Make plans offer a UUID function in the
function picker; if yours does not, store a UUID on the source record (a
Supabase `gen_random_uuid()` column, for example) and map that column.

### Click path

1. **Scenarios → Create a new scenario.** Add your trigger.
2. Add the **HTTP** app and choose **Make a request**.
3. **URL:** `<base>/v1/lcp/leads`. **Method:** `POST`.
4. **Headers:** add each name/value pair from the configuration file. Mark the
   key as sensitive rather than typing it into a shared template.
5. **Body type:** Raw, **Content type:** JSON (`application/json`), then paste
   the body template.
6. Run once, open the HTTP module's output bundle, and read the ack.
7. Add a router if you want branching: `ACCEPTED`/`PROCESSED` continues,
   `DUPLICATE` is success, `4xx` raises an alert.

### Headers

`Authorization: Bearer <key>`, `Content-Type: application/json`,
`X-LCP-Idempotency-Key` (same value as the body), and `X-LCP-Test: true` while
testing.

Make cannot compute an HMAC-SHA256 over the exact request body with text
functions, and a hand-assembled signature fails whenever Make reorders keys.
If your operator requires HMAC, keep Make for the trigger and send through the
Supabase edge function, which signs server-side.

### Test-mode first run

1. Point the URL at the sandbox and set the body `"test"` value to `true`, plus
   `X-LCP-Test: true`.
2. **Run once** with a real submission from your test form.
3. Read the ack in the module output. Pass condition: 2xx and a status that is
   not `ERROR` or `REJECTED`.
4. **Run once more unchanged.** Pass condition: `DUPLICATE`, proving the
   idempotency key comes from the source record rather than from the clock.
5. Switch the URL and `test` flag for production only after the buyer side
   confirms they saw a test lead.

### Three failures you will hit

1. **`400 LCP-001 INVALID_FORMAT`.** The HTTP module sent a form-encoded body
   because "Content type" was left at the default. Fix: set Raw + JSON, and
   keep the body a single JSON object — no wrapping array, no leading text.
2. **`400 LCP-009` or `422 LCP-010` only for some rows.** Make maps a missing field
   to an empty string, so the body carries `"phone": ""` or `"country_code": ""`.
   Fix: add a filter before the HTTP module so rows without a phone/email and a
   country/state/postal code never reach it, or split the mapping into a Set
   variable step that drops empty keys.
3. **`401 LCP-002 UNKNOWN_SENDER`.** The `Authorization` header reads
   `Bearer Bearer <key>`, or the key belongs to the sandbox while the URL is
   production. Fix: the header value is `Bearer ` plus the raw key, exactly
   once.

See the [error guide](ERROR-GUIDE.md) for the full code list.

---

## 4. Zapier

**Use this when** your lead source is a Zapier trigger (Typeform, Facebook Lead
Ads, a CRM) and you want a no-server handoff.

### What you need

| Item | Where it comes from |
|---|---|
| Zapier account with Webhooks by Zapier, or Code by Zapier | Your Zapier plan |
| LCP endpoint, sender id, receiver id | Platform operator |
| Bearer API key | Platform operator (HMAC needs a signer) |

### Fields to map

Same map as every other recipe. Zapier step outputs are referenced as
`{{1__email}}` style tokens in the task UI; the mapping is identical:

| Trigger output | Envelope field |
|---|---|
| Email | `payload.consumer.email` |
| Phone | `payload.consumer.phone` (E.164) |
| First/last name | `payload.consumer.*` |
| Country, state, postal code | `payload.location.*` |
| Consent timestamp and page | `payload.compliance.*` |
| Submission id | `lcp.message.idempotency_key`, `payload.external_id` |
| Campaign | `payload.provenance.campaign_id` |

### Click path

Zapier's Custom Request builds a **flat** key/value JSON object, which cannot
express the nested envelope. Two honest options:

**Option A — Code by Zapier (recommended).** A Code step can build the nested
object and post it. Use the shape from the Lovable example, minus the signing:

```javascript
const endpoint = "https://platform.example"; // sandbox first
const senderId = "publisher_001";
const receiverId = "platform_001";
const apiKey = "read-from-a-secret"; // Zapier: use an app connection/secret, not inline

const now = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
const submissionId = inputData.submission_id; // stable per submission
const envelope = {
  lcp: {
    version: "1.0.0",
    message: {
      id: crypto.randomUUID(),
      type: "lead",
      timestamp: now,
      sender_id: senderId,
      receiver_id: receiverId,
      correlation_id: null,
      idempotency_key: submissionId,
      test: true,
    },
    payload: {
      lead_id: `lead-${submissionId}`,
      external_id: submissionId,
      submitted_at: now,
      status: "NEW",
      channel: "form",
      consumer: {
        first_name: inputData.first_name,
        last_name: inputData.last_name,
        email: inputData.email,
        phone: inputData.phone,
      },
      location: {
        country_code: inputData.country_code,
        state_region: inputData.state_region,
        postal_code: inputData.postal_code,
      },
      compliance: {
        consent_timestamp: inputData.consent_timestamp,
        consent_source_url: inputData.consent_source_url,
        consent_purposes: ["calls", "email", "share_with_partners"],
      },
      attributes: { vertical: "mortgage", schema_version: "1.0.0" },
    },
  },
};

const response = await fetch(`${endpoint}/v1/lcp/leads`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Authorization: `Bearer ${apiKey}`,
    "X-LCP-Idempotency-Key": submissionId,
    "X-LCP-Test": "true",
  },
  body: JSON.stringify(envelope),
});

output = { status: response.status, body: await response.text() };
```

Code by Zapier runs Node. If your step runtime does not expose `crypto`,
replace the generated id with a UUID stored on the submission; `lcp.message.id`
only has to be unique per lead, while `idempotency_key` has to stay stable.

**Option B — Webhooks by Zapier, Custom Request.** Set the method to POST,
payload type JSON, and the URL to `<base>/v1/lcp/leads`. Only use this if your
plan's Custom Request accepts a raw body; with flat data fields the body will
not match the envelope and the platform returns `LCP-003` or `LCP-100`.

### Headers

`Content-Type: application/json`, `Authorization: Bearer <key>`,
`X-LCP-Idempotency-Key`, and `X-LCP-Test: true` while testing. Zapier's
Custom Request cannot compute the HMAC; if your operator requires it, send
through the Supabase edge function instead of adding a signing step to the Zap.

### Test-mode first run

1. Build the Zap with the sandbox URL and `test: true`.
2. **Test** the step with a real submission (Zapier replays the last trigger
   data). Read the `status` and `body` outputs.
3. Run the same submission again — `DUPLICATE` is the pass condition for the
   second run.
4. Turn the Zap on only after switching the URL and the test flags.

### Three failures you will hit

1. **`400 LCP-001 INVALID_FORMAT`.** The Custom Request step sent form-encoded
   data or a JSON string nested inside another string. Fix: use Option A, or
   the raw-body mode if your plan has it.
2. **`401 LCP-002 UNKNOWN_SENDER`.** An API key stored in a Zap note or a
   plain-text field was rotated, or the header includes `Bearer` twice. Fix:
   keep the key in the app connection and re-test after any rotation.
3. **Duplicates after a Zap replay.** Zapier's replay and error-retry features
   re-send the same trigger; a random idempotency key turns that into a second
   lead. Fix: derive the key from the source submission id, never from the
   clock or a UUID generated inside the step.

See the [error guide](ERROR-GUIDE.md) for the full code list.

---

## 5. Google Sheets

**Use this when** the lead is a row — a form's response sheet, an exported
list, or a sheet your team maintains by hand. Apps Script posts the row.

### What you need

| Item | Where it comes from |
|---|---|
| A sheet with a header row | Your Google account |
| Apps Script bound to that sheet | Extensions → Apps Script |
| LCP endpoint, sender id, receiver id, HMAC secret | Platform operator |
| Vertical and schema version | Platform operator |

[`apps-script.gs`](../examples/integrations/no-code/apps-script.gs) is the
complete script. Endpoint, ids, secret, vertical, and consent purposes are read
from **Script Properties**, never from cells.

### Fields to map

Edit the `COLUMNS` block at the top of the script to match your header row
exactly (case and spacing included): `Email`, `Phone`, `First name`,
`Last name`, `Country code`, `State / Region`, `Postal code`, `City`,
`Consent`, `Campaign ID`, `Submission ID`, `Vertical`.

| Sheet column | Envelope field |
|---|---|
| `Email` | `payload.consumer.email` |
| `Phone` | `payload.consumer.phone` |
| `First name`, `Last name` | `payload.consumer.*` |
| `Country code` | `payload.location.country_code` |
| `State / Region`, `Postal code` | `payload.location.*` |
| `Consent` | `payload.compliance` (truthy values: `true`, `yes`, `y`, `1`, `checked`, `x`) |
| `Campaign ID` | `payload.provenance.campaign_id` |
| `Submission ID` | `lcp.message.idempotency_key`, `payload.external_id` |

Apps Script cannot read the consumer's IP address or user agent, so
`provenance.ip_address` and `provenance.user_agent_hash` are omitted. That is
a platform limitation, not a configuration mistake — send from a server if the
operator requires them.

### Click path

1. **Extensions → Apps Script**, delete the stub, paste the script.
2. **Project Settings → Script properties → Add script property** for each of:
   `LCP_ENDPOINT`, `LCP_SENDER_ID`, `LCP_RECEIVER_ID`, `LCP_HMAC_SECRET`,
   `LCP_VERTICAL`, `LCP_SCHEMA_VERSION`, `LCP_CONSENT_PURPOSES`,
   `LCP_CONSENT_SOURCE_URL`, `LCP_TEST_MODE`.
3. **Run `testSend`** and approve the authorization prompt. The execution log
   shows the ack.
4. **Triggers (clock icon) → Add trigger:** function `onFormSubmit`, event
   source **From spreadsheet**, event type **On form submit**. The simple
   `onFormSubmit` name only fires when installed as a trigger.
5. For an existing sheet, run **`sendAllRows`**, which reads the header row and
   posts every data row.

### Headers

The script sets `X-LCP-Sender-Id`, `X-LCP-Timestamp`,
`X-LCP-Idempotency-Key`, `X-LCP-Signature`, `Accept`, and
`Content-Type: application/json`, plus `X-LCP-Test: true` while testing. The
signature is computed over `timestamp + "\n" + key + "\n" + body` with
`Utilities.computeHmacSha256Signature`, hex encoded.

### Test-mode first run

1. Leave `LCP_TEST_MODE` at `true` and point `LCP_ENDPOINT` at the sandbox.
2. Run `testSend` from the editor. Pass condition: an execution-log line with
   HTTP 200-299 and an ack status that is not `ERROR` or `REJECTED`.
3. Submit the form once, then open **Executions** and confirm the same result
   for the real trigger path.
4. Run `sendAllRows` twice on a two-row test sheet: the second run should log
   `DUPLICATE` for both rows.

### Three failures you will hit

1. **`Exception: You do not have permission to call UrlFetchApp`.** The script
   ran without an approval for the external-request scope — usually because it
   was triggered before anyone ran it manually. Fix: run `testSend` from the
   editor, approve the prompt, then re-run the trigger.
2. **The trigger never fires.** `onFormSubmit` was created as a simple trigger
   by naming a function, but form submissions need an **installable** trigger
   with the event type "On form submit". Fix: add it from the Triggers page.
3. **Phone arrives mangled (`61412345678`, or a date).** Sheets treats a phone
   column as a number or a date and drops the `+`, and the script rejects
   non-E.164 values rather than guessing. Fix: format the phone column as
   **Plain text** before entry, and have the form/sheet store E.164. A rejected
   row logs `Phone must be E.164` — see `LCP-009` in the
   [error guide](ERROR-GUIDE.md).

See the [error guide](ERROR-GUIDE.md) for the full code list.

---

## Screen the ack before you trust it

Whatever platform you used, the response is the only evidence the lead landed.
Use this table instead of reading the HTTP code alone:

| Response | Meaning | What to do |
|---|---|---|
| HTTP 2xx, ack `RECEIVED` / `VALIDATED` / `ACCEPTED` / `PROCESSED` | Lead stored | Done. Keep the ack with the source record. |
| HTTP 2xx, ack `DUPLICATE` | Same idempotency key seen before | Success. Do not create a second CRM record. |
| HTTP 2xx, ack `REJECTED` with `rejection_reason` | Refused by the receiver (duplicate, out of geography, consent expired) | Route by reason; `invalid_phone` and `invalid_email` are source-data fixes. |
| HTTP 400 or 422 with `errors[]` | The envelope itself is wrong | Read `field` in each error — it names the exact path to fix. |
| HTTP 401 `LCP-002` / `LCP-012` | Identity or signature | Check key, sender id, timestamp, and body bytes. |
| HTTP 409 `LCP-005` | Duplicate lead | Key reused for different bytes, or the same `lead_id` resent. For a retry, reuse the original key and expect the original result. |
| HTTP 429 `LCP-011` | Rate limited | Honor `Retry-After`, retry with the same idempotency key. |
| HTTP 5xx | Platform side | Retry with backoff and the same idempotency key; do not rebuild the payload. |

## Before you call it live

- [ ] Sandbox run passed, ack status recorded.
- [ ] Repeat run returned `DUPLICATE` (idempotency key is stable).
- [ ] `test: false` and no `X-LCP-Test` header for production.
- [ ] Secrets live in the platform's secret store, not in a cell, a note, or a
      template export.
- [ ] Consent comes from a real checkbox and a real page URL; no fabricated
      `compliance` block.
- [ ] Phone values are E.164 and country codes are two letters.
- [ ] Someone owns the failures: `4xx` rows are visible, not silently dropped.
- [ ] The operator confirmed the sender id and vertical before the first live
      lead.

Related: [publisher onboarding](PUBLISHER-ONBOARDING.md) ·
[publisher mapping](PUBLISHER-MAPPING.md) · [field dictionary](FIELD-DICTIONARY.md) ·
[integration examples](../examples/integrations/) ·
[errors](ERROR-GUIDE.md)
