# LEP-0005 — Request-history log lookup (paginated audit read surface)

- **Title:** Request-history log lookup (paginated audit read surface)
- **Author(s):** LCP maintainers (draft for adopter review)
- **Date:** 2026-11-03
- **Status:** **Draft** — this record is a numbered Draft proposal only. It
  authorizes **no** implementation: no route, schema file, OpenAPI edit, MCP
  tool, SDK model, or vector ships from this document until an independent
  dossier and a maintainer `Accepted` decision are recorded in
  `governance/LEP-REGISTRY.md`. The wire shapes below are drafts **validated
  by execution** (see "Verification runs") so the record is schema-honest at
  Draft, not merely plausible.
- **Target version:** v1.1.0 (MINOR; read-only additive surface — see
  Compatibility, versioning, and deprecation impact)
- **Discussion:** [#187](https://github.com/SpearSystems/LCP/issues/187)
  (`[LEP] Request-history log lookup (paginated audit read surface)`)
- **Roadmap trigger:** `docs/V1.1-ROADMAP.md`, Phase 6, "Request-history log
  lookup" (medium priority)

## Problem statement and evidence

**Trigger (verbatim, roadmap Phase 6).** "Platform operators and publishers
need to inspect past ping and post requests (for debugging, dispute
resolution, or compliance audits) without paging through raw database dumps."
The reference pattern the roadmap cites is a commercial US home-services
network's paginated `GET` log endpoint "filtering ping requests by ID,
response status, caller phone, response phone, and time range, with
pagination metadata in response headers," and the design constraint LCP
adopted: "expose a filtered, paginated read surface (REST and MCP) over
it. Never return raw envelope bodies or PII in the log surface — return
metadata (status, token, timestamps, sender, need/vertical, price) and
redact or hash contact fields. Additive; no core change."

**Internal evidence — the data already exists; the surface does not.** The
reference platform already records, in first-class tables, everything the
roadmap names as metadata:

- `leads` (`storage.py:512`) — `message_id`, `sender_id`, `receiver_id`,
  `message_type`, `status`, `vertical`, `test`, `created_at`/`updated_at`;
- `pings` (`storage.py:623`) — per-buyer ping records with `status`
  (`OPEN`/`EXPIRED`/`WON`/`SUPPRESSED`, e.g. `storage.py:2102`,
  `storage.py:2146`, `storage.py:2173`) and `expires_at`;
- `bids` (`storage.py:635`) — `decision` (`accept`/`reject`/`pass`,
  `schemas/bid.json`), `bid_price_cents`, `currency`, `received_at`;
- `deliveries` (`storage.py:650`) — outbound post/event delivery state
  (`PENDING`/`RETRY`/`DELIVERED`/`FAILED`, `REDACTED` after erasure per
  `storage.py:2726-2732`) with `attempts` and `last_error`;
- `match_decisions` (`storage.py:614`) — explainable routing decisions per
  `(lead, offer)` with reasons (already surfaced per-lead by
  `lead_status`, `router.py:1629`);
- `dead_letter_jobs` (`storage.py:718`) — exhausted jobs with recovery
  state, listable today only through the operator CLI
  (`admin.py:38-41` → `list_dead_letters`, `storage.py:1778-1803`, ordered
  `created_at, job_id`);
- `audit_events` (`storage.py:732`) — actor/action/resource records written
  by `insert_audit` (`storage.py:1048`) with the correlating
  `X-LCP-Request-Id` folded into `metadata_json` (`storage.py:1060-1062`);
  a reader exists internally (`list_audit_events`, `storage.py:1085`) but is
  keyed only by `(resource_type, resource_id)` and is exposed on **no**
  interop surface.

Today's only protocol-level read surfaces are `GET /v1/lcp/leads/{lead_id}`
(role-filtered detail view, `service.py:167-172`, `router.py:1615-1697`),
`GET /v1/lcp/offers` (+`/quota`), `GET /v1/lcp/capabilities`,
`GET /v1/lcp/schemas/{name}`, `GET /v1/lcp/attachments/{id}`, `/metrics`
(`platform:admin`-gated, `service.py:147-149`), and the health endpoints
(`service.py:137-174`). Enumerating them: **there is no way to ask "what
happened around lead X / between time A and B / for phone hash H" without
raw database access or shell access to `admin.py`.** `lead_status` answers
"this one lead, now"; the dead-letter CLI answers "this queue, all of it".
Neither answers a dispute ("this consumer called on Tuesday; what did each
party see and when?") that starts from anything other than a known
`lead_id`.

**External evidence — honest scope of the claim.** The comparison network
runs this mechanism in production today (roadmap Phase 6 header,
2026-08-18): a single-buyer ping/post network whose log lookup exists
precisely because *dispute resolution across two organizations* — "the
network says the ping went out, the publisher says it never came back" —
cannot ride one party's private SQL. That is one external production
datapoint plus the roadmap trigger wording. **No independent LCP adopter
has yet recorded evidence on this surface**, and this Draft is explicitly
soliciting it on #187 before Review closes. Contrast with LEP-0003:
there the interop necessity (slot dedup) rode a shipped core block
(`exclusivity`); here the necessity argument is operational. Note also the
decision-rule scope precisely: the two-adopter/interoperability-necessity
bar in `governance/LEP.md` gates a **new universal message type**. This
proposal adds **zero** message types, **zero** envelope fields, and **zero**
vertical changes — it is a REST/MCP read surface over existing stored
state. What makes it LEP material instead is the other list in `LEP.md`: it
touches a **PII boundary** (exposing contact-derived data as hashes), an
**authentication rule** (new scope), and a **retention interplay** (§9
erasure). Those boundaries are why "just ops tooling" cannot be waved
through as a normal PR (see Alternatives).

## Scope

- **Reference implementation + transport binding:** a new read-only
  endpoint `GET /v1/lcp/history/requests` on the reference platform's
  dispatch table, an MCP tool `get_request_history` on the existing thin
  adapter, and the OpenAPI path describing both. Row content is **metadata
  already stored** (status, ids, timestamps, sender/receiver, vertical,
  price) plus one contact-derived value under a strict per-pair rule;
  never raw envelope bodies.
- **New read scope:** `history:read`, enforced through the existing
  `_authenticate_read` gate (`service.py:174-181`, `auth.py:181-185`) plus
  **row-level scoping** (who may see which rows — the scope matrix below),
  mirroring the existing precedent that `lead:read` alone is not enough
  (`authorize_lead_read`, `router.py:1615-1627`, filters by counterparty).
- **SPEC text (implementation-phase, gated on acceptance):** one paragraph
  in §9 (PII subsection, `SPEC.md:938-945`) norming the history read
  surface's contact-hash rule, and a `history` entry in the §8 capability
  shape (`SPEC.md:694`) so callers can discover the surface.
- **Schemas:** a new response-shape schema
  (`schemas/history-request-row.json`, drafted below) — not a message
  payload schema; the eight `schemas/*.json` envelope files are untouched.
- **Conformance/vectors:** platform behavior tests named below; a row-shape
  vector family; MCP parity.
- **Not in scope:** any change to message envelopes, the closed `message`
  `type` enum, vertical schemas, the delivery/routing pipelines, replay or
  re-drive operations (dead-letter `quarantine`/`replay` stay CLI-only at
  `storage.py:1804-1816`), bulk export or archival download (Deferred
  questions), publisher-side visibility into other parties' ping/bid rows
  (Deferred questions), and any write surface (this endpoint mutates
  nothing, including `audit_events` aside from logging the query itself).

## Proposed wire shape

### Endpoint

```http
GET /v1/lcp/history/requests?from=...&to=...&message_type=...&limit=...
X-LCP-Sender-Id: <caller>
Authorization: Bearer <api-key>        # or the HMAC header trio
```

The path is namespaced under `/v1/lcp/` like every protocol read today
(`service.py:137-174`; the OpenAPI server base is `https://{host}/v1/lcp`,
`api/lcp-openapi.yaml:23-24`). The roadmap's shorthand "a paginated `GET`
log endpoint" does not fix a literal path; choosing a non-`/v1/lcp/` path
would be the only endpoint in the binding. Reads authenticate like existing
GETs — bearer or HMAC with empty body (`auth.py:109-185`; `mutating=False`
so no idempotency header is required — that gate, `auth.py:161-162`, is
mutating traffic only). The required scope is `history:read`.

### Query parameters (closed allowlist)

| Parameter | Shape | Matches |
|---|---|---|
| `request_id` | UUID | the envelope `message.id` of the stored request (`schemas/envelope.json`, `message.id` is `format: uuid`) |
| `lead_id` | string | `leads.lead_id` / `pings.lead_id` / `bids.lead_id` / `deliveries.lead_id` |
| `message_type` | one of `lead call ping bid post ack event` | the closed §4 type set |
| `status` | string, ≤64 chars | the row's stored status value (§3 lead statuses; `OPEN`/`WON`/`EXPIRED`/`SUPPRESSED` for pings; `accept`/`reject`/`pass` for bids; delivery statuses). **Open enum** — an unrecognized value is a valid query returning the (possibly empty) matching set, per §6's store-and-pass rule; it MUST NOT be a validation error |
| `vertical` | string | registered vertical id (`capabilities().verticals`) |
| `caller_phone_hash` | `^[a-f0-9]{64}$` | per-pair HMAC membership test, rule below |
| `from` / `to` | `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$` | whole-second UTC, matching `now_iso()`'s canonical stored form (`storage.py:21-22`, `storage.py:42-44` and the LEP-0002 whole-second emission discipline); `to` defaults to server time; sub-second or offset forms → 400 `LCP-001` |
| `limit` | integer `1..200`, default 50 | page size; out of range → 400 `LCP-001` (explicit, not clamped — silent clamping hides caller bugs) |
| `cursor` | opaque base64url keyset token | returned by the server in `Link`; clients MUST NOT construct it |

Unknown parameters, a `caller_phone` (plaintext) parameter, or a
`caller_phone_hash` failing the pattern → `400 LCP-001` with the standard
`errors` shape (§5). The allowlist is **closed on purpose**: a read surface
that ignores a mistyped filter silently returns a *broader* (more disclosing)
result than the caller asked for — the same strict-allowlist posture §12
praises for pings.

### Response

Body (paginated metadata rows, newest-last ordering by
`(created_at, request_id)` mirroring the shipped dead-letter listing's
deterministic `(created_at, job_id)` order, `storage.py:1784`):

```text
{ "requests": [ { …row… }, … ] }
```

Pagination metadata rides **response headers** per the roadmap reference
pattern (`X-LCP-` prefix follows the shipped header family,
`service.py:89`, `auth.py:121-160`):

| Header | Meaning |
|---|---|
| `X-LCP-Result-Count` | rows in this page (integer ≥ 0) |
| `X-LCP-Total-Results` | total matching rows **within the caller's visible scope** (exact integer; deployments MAY return `unknown` only when an exact count is infeasible — reference platform returns exact) |
| `Link` | `rel="next"` with the cursor URL when more pages exist; absent otherwise (RFC 8288 typed links) |
| `X-LCP-Request-Id` | echoed correlation id on every response (existing, `service.py:89`) |

The MCP adapter MUST surface the three pagination values inside the tool
result object (see impact section) because MCP results are content blocks,
not HTTP headers — recording today's fact that `client.get()` drops response
headers entirely (`implementations/mcp-server/lcp_mcp_server/client.py:73-80`).

### The row shape (draft `schemas/history-request-row.json`)

Closed object (strict allowlist; future fields are MINOR bumps negotiated
the same way §6 closed objects already are — no v1.0 receiver is exposed
because the endpoint itself is capability-discoverable, see §8 below):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://lcp.dev/schemas/history-request-row.json",
  "title": "LCP History Request Row (LEP-0005 draft overlay)",
  "type": "object",
  "additionalProperties": false,
  "required": ["request_id", "message_type", "direction", "status", "created_at"],
  "properties": {
    "request_id":   {"type": "string", "format": "uuid", "description": "The stored envelope's message.id — the reference pattern's 'token'."},
    "lead_id":      {"type": "string"},
    "message_type": {"enum": ["lead", "call", "ping", "bid", "post", "ack", "event"]},
    "direction":    {"enum": ["received", "sent"], "description": "From the platform's side: an inbound envelope it received, or an outbound record it sent/delivered."},
    "status":       {"type": "string", "minLength": 1, "maxLength": 64, "description": "Stored record status (open pass-through per §6)."},
    "sender_id":    {"type": "string"},
    "receiver_id":  {"type": "string"},
    "vertical":     {"type": ["string", "null"]},
    "test":         {"type": "boolean"},
    "price_cents":  {"type": "integer", "minimum": 0, "description": "Minor units only (LEP-0003 units discipline); currency travels separately."},
    "currency":     {"type": "string", "pattern": "^[A-Z]{3}$"},
    "caller_phone_hash": {"type": "string", "pattern": "^[a-f0-9]{64}$", "description": "Per-pair HMAC keyed to the REQUESTING credential. Absent — never blank — when the rule in the next section omits it."},
    "expires_at":   {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$"},
    "created_at":   {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$"},
    "updated_at":   {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$"}
  }
}
```

Rows carry **no** `consumer`, `location`, `attributes`, or `envelope`
members — the roadmap's "never return raw envelope bodies or PII" is the
schema's `additionalProperties: false`, not a scrub pass.

### What "redact or hash" means, precisely

The roadmap constraint says "redact or hash contact fields". The two are
different privacy guarantees and this record picks one per field instead of
leaving the choice to implementers:

1. **`caller_phone_hash` is a *hash with the pair secret*, not a redaction.**
   Exactly the §9 `phone_hash` model (`SPEC.md:855-869`) that LEP-0003 §3
   extended to contact hashes (`SPEC.md:871-901`):
   `caller_phone_hash = HMAC-SHA256(pair_secret, verbatim wire
   consumer.phone)` — the platform-side instance already shipped as
   `pair_phone_hash` (`messages.py:48-62`), keyed per pair so the same phone
   yields a different digest per credential and an intercepted digest is
   non-reversible without that pair's secret. **Computed at read time from
   plaintext the platform still holds, never stored in history rows** — the
   re-key-from-held-plaintext-only rule of LEP-0003 §3 / the shipped
   contacted-digest re-key path (`router.py:718-747`) is the precedent.
   The read-time rule is what makes erasure correct by construction (see
   Retention below).
2. **Absent where no pair secret exists.** A caller credential with no
   bilateral HMAC secret (e.g. API-key-only, `hmac_secret NULL`,
   `storage.py:504`) cannot receive a keyed hash for anyone's phone — the
   field is **omitted** from every row for that caller, not zeroed and not
   plaintext.
3. **Absent where no plaintext remains.** Erased/redacted leads carry no
   computable hash (`storage.py:2688-2742` redacts envelopes; the preimage
   is gone) — rows persist for auditability of the erasure itself
   (`storage.py:2690-2693`, `lead.erased` audit event at `storage.py:2778-2787`)
   but every contact-derived field is absent.
4. **Everything else is redaction-by-omission.** No email, name, address,
   or postal-derived field appears on a row in any form; a keyed email
   filter parameter stays deferred behind the §9 email gate (Deferred
   questions).
5. **Never unsalted digests** — same MUST as LEP-0003 (roadmap: "Adopt the
   mechanism, not the hash"; §14 blocker B3 lineage). SHA-1-shaped values
   in parameters or rows are conformance failures (invalid C below).

The `caller_phone_hash` **filter parameter** uses the identical rule: the
caller submits a digest it computed under its own pair secret (a value it
could only have obtained from that pair's pings/posts), and the platform
tests membership against the plaintext it holds. Plaintext never leaves;
the platform learns nothing it can attribute; a non-pair caller's digest
matches nothing. A reviewer should note the cost honestly: the reference
platform evaluates the HMAC per candidate row inside the caller's visible
scope (O(rows-in-scope) HMACs per filtered query — fine at reference scale;
an index-able keyed-lookup scheme is an implementation-phase optimization,
not a protocol question).

### Authorization scope matrix

Row visibility is **per row**, not just per endpoint — `history:read`
alone is necessary, not sufficient. The precedent is `lead:read` +
`authorize_lead_read` (`router.py:1615-1627`), which answers "not found"
404 rather than "forbidden" so existence itself is not disclosed. For a
list surface the analogue is **filtering, not erroring**: out-of-scope rows
simply do not appear; a `request_id`/`lead_id` filter that exists but is
not yours returns `200` with an empty set.

| Caller | Rows visible | Row field restrictions |
|---|---|---|
| **Publisher** (sender of `lead`/`call` messages) | `direction=received` rows where `sender_id == caller` (its own sends: leads, calls, events it posted) + `direction=sent` rows where `receiver_id == caller` (the acks/status responses addressed to it) | `price_cents`/`currency` absent (bids it may see are never its rows); `caller_phone_hash` computable only for rows whose lead still holds plaintext |
| **Buyer** (sender of `bid`, receiver of `ping`/`post`) | rows where `sender_id == caller` (its bids/events) or `receiver_id == caller` (pings addressed to it, posts delivered to it, acks) | `price_cents` only on rows whose record carries a price it is party to (its own bids' `bids.bid_price_cents`, `storage.py:635-648`) — never another buyer's; `caller_phone_hash` as above |
| **Platform operator** (`platform:admin` credential, or the wildcard — see critique) | all rows within the operator's tenant | `caller_phone_hash` omitted unless the operator credential itself is the row's counterparty pair (operator holds the plaintext; a hash under nobody's pair key carries no information the operator can compare against a pair's stream); per-pair confidentiality of *prices* is NOT extended to operators (they already compute payables, `payable_records`, `storage.py:598`) |
| **Any caller, any role** | additionally scoped to `tenant_id` equality with the credential (`credentials.tenant_id`/`leads.tenant_id`, `storage.py:501`, `storage.py:514`; the shipped multi-tenant list pattern, `storage.py` offer listing per tenant, exercised by `implementations/reference-platform/tests/test_platform.py:792-805`) | — |

**The `'*'` precedent, critiqued rather than inherited.** `has_scope`
(`auth.py:187-189`) treats both `*` and `platform:admin` as satisfying
*every* scope check, and credentials are created with default
`scopes_json = '["*"]'` (`storage.py:502`; migration default `storage.py:826-828`).
LEP-0003's dossier routed this as observation O2: acceptance "rides the
default credential's wildcard scope … which is not a conformance-grade
path" (LEP-0003 §4, disposition table O1/O2/O3; #168 findings). This LEP
makes the posture explicit:

- A conformant implementation MUST grant `history:read` by **name**;
  conformance vectors MUST be executed with a scoped credential, never the
  wildcard default (mirroring the T6 requirement #168 landed for
  `event:submit`).
- Every credential that holds `*` or `platform:read`-class scope
  automatically gets this endpoint — that is today's shipped behavior and
  this Draft does not shrink it under a MINOR. The *critique* recorded
  here: a read surface disclosing **commercial metadata (prices) and
  timing graphs (who was pinged when)** inherits the coarsest scope in the
  system. Deployments SHOULD provision operator credentials deliberately,
  and the protocol's eventual fine-grained operator-scope refactor is
  recorded as a Deferred question rather than silently depended on.
- The row-scoping rules above apply **inside** every scope including
  operator (except the operator's own price visibility) — a wildcard
  credential broadens *which rows* the org-level operator can see, never
  *what fields* a pair-scoped value can become.

### Errors (all existing codes, §5; no new code needed)

| Condition | Response |
|---|---|
| malformed/unknown parameter, plaintext phone, non-canonical timestamp, bad `limit` | `400 LCP-001` |
| missing/unknown sender | `401 LCP-002` (`auth.py:129-137`) |
| bearer/HMAC failure, or sender lacking `history:read` | `401 LCP-002` (identity ruling #142 lineage, `auth.py:141-185`) — signature/timestamp failures on the HMAC path raise `LCP-012` (`auth.py:153-159`, `auth.py:179`) |
| rate limited | `429 LCP-011` + `Retry-After: 60` (shipped 429 header behavior, `service.py:72`) |
| filtered-but-out-of-scope ids | `200` + empty set (no existence disclosure) |

### Valid wire example — publisher time-range query

*(Execution-validated 2026-11-03 against the row schema above; see
"Verification runs". Headings in this record deliberately read "Valid wire
example" rather than "Valid example" because `tools/tests/test_lep_wire_examples.py:34`
parses every `### Valid example` block as a full LCP **envelope** — this
surface has no envelope; the implementation-phase obligation to extend the
T1 tool to row-shape examples is named in the conformance section.)*

Request (publisher `publisher_001`, API-key bearer; HMAC path equally valid
with empty body):

```http
GET /v1/lcp/history/requests?vertical=home_services&from=2026-11-03T10:00:00Z&to=2026-11-03T10:30:00Z&limit=50 HTTP/1.1
Host: api.example.com
X-LCP-Sender-Id: publisher_001
Authorization: Bearer <api-key>
```

Response:

```http
HTTP/1.1 200 OK
Content-Type: application/json; charset=utf-8
X-LCP-Request-Id: req_3fa85f64b10c4e8fa1c9d2e7f6a5b4c3
X-LCP-Result-Count: 3
X-LCP-Total-Results: 3
Link: <https://api.example.com/v1/lcp/history/requests?vertical=home_services&from=2026-11-03T10:00:00Z&to=2026-11-03T10:30:00Z&limit=50&cursor=czI2LDIwMjYtMTEtMDNUMTA6MjE6MDA>; rel="next"
```

```json
{
  "requests": [
    {
      "request_id": "770e8400-0000-4000-8000-000000000201",
      "lead_id": "lead_def456",
      "message_type": "lead",
      "direction": "received",
      "status": "PINGED",
      "sender_id": "publisher_001",
      "receiver_id": "platform_001",
      "vertical": "home_services",
      "test": true,
      "expires_at": "2026-11-04T10:20:05Z",
      "created_at": "2026-11-03T10:20:05Z",
      "updated_at": "2026-11-03T10:20:06Z"
    },
    {
      "request_id": "bb1e8400-0000-4000-8000-000000000202",
      "lead_id": "lead_def456",
      "message_type": "bid",
      "direction": "received",
      "status": "accept",
      "sender_id": "buyer_002",
      "receiver_id": "platform_001",
      "vertical": "home_services",
      "test": true,
      "price_cents": 2900,
      "currency": "USD",
      "created_at": "2026-11-03T10:20:08Z",
      "updated_at": "2026-11-03T10:20:09Z"
    },
    {
      "request_id": "c20e8400-0000-4000-8000-000000000203",
      "lead_id": "lead_def456",
      "message_type": "post",
      "direction": "sent",
      "status": "DELIVERED",
      "sender_id": "platform_001",
      "receiver_id": "buyer_002",
      "vertical": "home_services",
      "test": true,
      "created_at": "2026-11-03T10:21:00Z",
      "updated_at": "2026-11-03T10:21:02Z"
    }
  ]
}
```

*(Honest scope note on the example: rows 2–3 are shown here as what the
**buyer** would see for the same lead in a query under its own credential —
they appear here for illustration of the row shape only; a `publisher_001`
bearer response would contain just row 1 under the matrix above. The
separate buyer-scoped example below shows the real filtered response,
including the pair-keyed hash.)*

### Valid wire example — buyer `caller_phone_hash` filter

Buyer `buyer_002` tests "did I see this consumer?" using the digest it
computes under **its own pair secret** with the platform. The example pins
byte-recomputable values from the shipped vector fixture
(`test-vectors/sdk/contact-hash.json`, `lcp-contact-hash-phone-001`: preimage
`+15125550147` under secret `pair-secret-for-vectors-0123456789` →
`c47aff331606437528b34b7f919eb37704e87ee35eed80e52cfa36826a4a2e12`):

```http
GET /v1/lcp/history/requests?caller_phone_hash=c47aff331606437528b34b7f919eb37704e87ee35eed80e52cfa36826a4a2e12&message_type=post&from=2026-11-03T00:00:00Z&to=2026-11-03T23:59:59Z HTTP/1.1
X-LCP-Sender-Id: buyer_002
Authorization: Bearer <api-key>
```

Response rows (one match; the keyed hash echoed **under the caller's own
key** so the caller can confirm the match without re-resolving preimages):

```json
{
  "requests": [
    {
      "request_id": "c20e8400-0000-4000-8000-000000000203",
      "lead_id": "lead_def456",
      "message_type": "post",
      "direction": "sent",
      "status": "DELIVERED",
      "sender_id": "platform_001",
      "receiver_id": "buyer_002",
      "vertical": "home_services",
      "test": true,
      "caller_phone_hash": "c47aff331606437528b34b7f919eb37704e87ee35eed80e52cfa36826a4a2e12",
      "created_at": "2026-11-03T10:21:00Z",
      "updated_at": "2026-11-03T10:21:02Z"
    }
  ]
}
```

### Valid wire example — erased lead (contact fields structurally absent)

Same buyer or publisher querying `request_id=770e8400-…-000000000201`'s
lead **after** `erase_lead` (`storage.py:2688-2790`): the row survives with
the platform's erasure state, with no contact-derived field — the plaintext
preimage is gone, so no keyed hash exists to emit:

```json
{
  "requests": [
    {
      "request_id": "770e8400-0000-4000-8000-000000000201",
      "lead_id": "lead_abc123",
      "message_type": "lead",
      "direction": "received",
      "status": "ERASED",
      "sender_id": "publisher_001",
      "receiver_id": "platform_001",
      "vertical": "mortgage",
      "test": false,
      "created_at": "2026-10-02T09:00:00Z",
      "updated_at": "2026-10-05T12:00:00Z"
    }
  ]
}
```

### Invalid wire example A — plaintext consumer phone in a row

```json
{
  "request_id": "e40e8400-0000-4000-8000-000000000205",
  "message_type": "lead",
  "direction": "received",
  "status": "PINGED",
  "created_at": "2026-11-03T10:20:05Z",
  "consumer": { "phone": "+15125550147" }
}
```

REJECTED by the row schema: `consumer` is not an allowlisted member
(`additionalProperties: false` — measured in the "Verification runs",
invalid[0]). This is the roadmap's "never return … PII in the log surface"
made mechanical: the shape cannot carry a contact at all, so there is no
scrub step to forget. A receiver detecting this shape from a conformant-claiming
platform treats it as a conformance failure, not data.

### Invalid wire example B — plaintext phone in the filter

`GET …/history/requests?caller_phone=%2B15125550147` → `400 LCP-001`. The
parameter does not exist (closed allowlist); the only phone-shaped filter
accepts `^[a-f0-9]{64}$`. Passing the number itself as
`caller_phone_hash` also `400`s (pattern — measured, invalid[1] shows the
digest field rejecting the plaintext string identically).

### Invalid wire example C — SHA-1-shaped keyed hash

`caller_phone_hash=a94a8fe5ccb19ba61c4c0873d391e987982fbbd3` (40 hex, the
reference network's unsalted-SHA1 form) → `400 LCP-001`: fails the
64-hex pattern, and 40-hex SHA-1 over enumerable phone keyspaces is the
exact weakness §14 B3 / LEP-0003 excluded from the protocol. Same
mechanical REJ on the row field (invalid[2], "Verification runs").

### Invalid wire example D — non-canonical units

A row with `"currency": "usd"` (lowercase — violates ISO-4217 pattern,
invalid[3]) or `"created_at": "2026-11-03T10:20:05.123Z"` (sub-second —
violates the whole-second canonical of LEP-0002 / `now_iso()`, invalid[4]),
or `"price_cents": "29.00"` (float money — `REJ "is not of type
'integer'"`, invalid[5]; the row schema is integer minor units, so this is
a type error before it is a units error).

### Invalid behavior example E — scope leak (not schema-rejectable)

An operator response that included, for `sender_id=buyer_002`, a **second**
bid row from `buyer_009` on the same `ping_id` **with `price_cents` visible
to a `buyer_002`-credential request** is not a schema violation — the rows
validate — it is the authorization-matrix violation class. Like LEP-0003's
invalid C (oversell), rejection is a platform-behavior obligation with a
named blocking test below (`plat: test_history_scope_matrix_*`), and the
record says plainly: **this class is invisible to shape validation**, which
is exactly why the vectors must be platform tests, not corpus entries.

## Compatibility, versioning, and deprecation impact

- **Additive MINOR, zero wire impact.** No envelope field, no message type,
  no vertical, no §5 change (all error codes reused), no §3 status change
  (the row's `status` is the *stored record's* status — open pass-through
  per §6). A v1.0 endpoint and a v1.0 SDK never see this surface unless they
  call it; nothing they receive changes byte-for-byte.
- **Discovery.** §8 capabilities gains an optional
  `"history": {"request_lookup": true}` member at implementation. The
  shipped shape already models nested optional objects (`attachments`,
  `router.py:1699-1737`), and §8's capacity/offer-restriction precedent
  ("If `capacity` is absent, no caps are declared") gives the pattern:
  absent member = endpoint not hosted; clients MUST NOT probe blindly.
  This is a **REST-response** optional member, not a closed envelope object
  — the §6 ignore-unknown rule question does not arise the way it did for
  LEP-0003's ping fields, but the negotiation discipline is mirrored.
- **Closed row object.** Future row fields are MINOR version bumps
  negotiated by the full-semver `lcp_versions` rule LEP-0003 F6 normalized
  (`SPEC.md:698-712`): strict row validators pin the minor they were built
  against; unknown fields on a *response* are the receiver's problem only
  if it chose `additionalProperties: false` — the draft schema does, and
  that is a deliberate trade: response strictness today over silent drift,
  with version negotiation paying for it, same calculus as the ping
  allowlist.
- **No deprecation introduced.** Nothing is removed or renamed. When
  LEP-0001 (event subscriptions) eventually revives, a push feed of
  lifecycle events **complements** this pull surface; neither supersedes
  the other.
- **Auth-compat note.** Deployments that provisioned credentials before any
  scope existed (default `["*"]`, `storage.py:502`) gain the endpoint
  implicitly, exactly as they implicitly gained `lead:read`/`offer:read`
  checks today (`auth.py:187-189`). This is inherited shipped behavior,
  recorded in the scope-matrix critique, not a new weakening.

## Privacy, security, and universal-core audit

**Privacy.**

- The row set is the roadmap's metadata list — status, ids, timestamps,
  sender/receiver, vertical, price — plus one contact-derived value under
  the per-pair rule. `sender_id`/`receiver_id`/`lead_id` are organization-
  and record-level identifiers the caller already holds for in-scope rows;
  **cross-party rows are never returned**, so the surface discloses no new
  relationship graph beyond the caller's own transactions (contrast: an
  operator wildcard does, by design, and is critiqued in the matrix).
- `caller_phone_hash` is keyed per credential pair. The information it
  carries is exactly "does this lead's consumer match a phone **you can
  already compute** this digest for" — the membership oracle §9 has
  provided since v1.0 pings; the log surface adds timing and record state
  around it, not identifiability. Enumeration inside a pair is that pair's
  pre-existing insider threat model (`SPEC.md:855-869`), unchanged.
- Hashes remain personal data ("hashes of contact data used for matching
  are still personal data under most regimes" — carried honestly from
  LEP-0003's privacy section, issue-tracked there): mitigations are the
  read-time-only rule (nothing new is *stored*), per-pair keying (no
  cross-pair linkage), and erasure (below).
- **PII discipline vs §9:** ping-side data is untouched (strict allowlist
  still excludes everything, §12); this surface exposes contact *hashes*
  only over TLS-authenticated GETs, never on pings, never on webhooks.

**Erasure/retention integration (the §9 interplay the trigger names).**

- §9: "PII retention policies are deployment-defined" (`SPEC.md:945`). The
  history surface introduces **no second copy of anything**: it projects
  the platform's own operational tables, so retention, deletion, and
  suppression already normed per-deployment apply unchanged; the surface
  MUST NOT be implemented by snapshotting envelopes into new long-lived
  storage (that would *extend* retention through a read API — prohibited).
- Erased consumers must not reappear: `erase_lead` redacts envelopes and
  marks rows `ERASED`/`REDACTED` (`storage.py:2694-2742`) and deletes
  `contacted_parties` (`storage.py:2771-2775`); because the keyed hash is
  computed from held plaintext at read time, post-erasure rows are
  **structurally** contact-free (valid example C) and a
  `caller_phone_hash` filter cannot match an erased lead — there is no
  preimage to compare against. This is asserted as a blocking vector, not
  trusted to prose: `plat: test_history_phone_hash_filter_misses_erased_leads`
  and `plat: test_history_rows_after_erase_have_no_contact_derived_fields`
  (the shipped redaction suite is the baseline these extend).
- Row *existence* after erasure (ids, statuses, timings) is the
  erasure-proofing audit trail §9's compliance posture needs to *prove* an
  erasure happened — the same trade the shipped code documented
  ("Immutable routing/audit identifiers remain so operators can prove that
  an erasure occurred without retaining the consumer payload",
  `storage.py:2690-2693`). Deployments whose retention profile requires
  purging even those identifiers MAY delete rows; the history surface
  loses them identically. No protocol floor beyond §9 is added.

**Security.**

- *Query integrity:* the HMAC canonical covers
  `<timestamp>\n<idempotency-key>\n<raw-request-body>` (`SPEC.md:817-827`)
  — for GETs the body is empty, so **query parameters are not signed**.
  Recorded honestly as an inherited property of every existing read
  (`/v1/lcp/offers?vertical=`, attachments, quota), protected by TLS §9;
  filters disclose nothing to an attacker who can tamper (tampering yields
  errors or *fewer* rows), and authorization is computed from the
  authenticated credential, never from query text.
- *Abuse cases:* (a) cross-tenant enumeration — tenant scoping + authz
  matrix + per-sender §9 rate limits (`storage.py:694`,
  `service.py:72` 429); (b) timing/price intelligence aggregation —
  the strictest rule in the matrix: a buyer never sees another buyer's
  rows, so repeated queries reconstruct only its own history; (c) the
  operator wildcard — mitigated by field rules inside operator scope and
  the critique above; (d) log side-channels — the platform MUST audit the
  query itself (`insert_audit` action `history.query`) with filter
  *names*, result count, and `X-LCP-Request-Id` correlation, but MUST NOT
  log `caller_phone_hash` values or returned rows (row contents are
  partially personal data; re-duplicating them into logs breaks the
  no-second-copy rule); (e) DoS via unbounded scans — `limit ≤ 200`,
  cursor keyset (no OFFSET rescans), deployment MAY cap `from` span.
- **Universal-core audit (§12, item by item where this proposal touches):**
  no vertical-specific or market-specific fields in core (the row's
  `vertical` is an opaque id from the registry; a `gutter` lead in AU, a
  solar lead in US, and a `call` lead in NZ all project identically — the
  endpoint is vertical-agnostic and country-agnostic); phone E.164 /
  `phone_hash` HMAC-per-pair line: honored by reusing the §9 primitive
  (`messages.py:48-62`), no new hash construct; ping allowlist untouched;
  `message.type` enum untouched; open/closed enum policy: `status` filter
  is open (§6), `message_type`/`direction` closed, stated;
  "legacy production wire contract fields map 1:1": the reference network's
  log parameters (ID → `request_id`, response status → `status`, caller
  phone → `caller_phone_hash` keyed, response phone → same field, time
  range → `from`/`to`, pagination in headers → `X-LCP-Result-Count` /
  `X-LCP-Total-Results` / `Link`) — adopted **as mechanism**, with the
  unsalted-hash inversion of §9/LEP-0003 applied: the network's plaintext
  phone filter is not adopted. Checklist re-runs at implementation merge.

## SDK, OpenAPI, MCP, conformance, and operational impact

Draft status authorizes **none** of this; it is the implementation plan
that acceptance would unlock, under the roadmap change gates
(`docs/V1.1-ROADMAP.md:372-382`, items 1–7) and the `LEP.md` traceability
gate.

- **Schemas:** `schemas/history-request-row.json` exactly as drafted (plus
  the `{"requests": [ … ]}` list wrapper in the same file or its sibling),
  SHA-256 bundle manifest + reserved-fields checker as applicable; no
  envelope-schema or vertical change.
- **OpenAPI diff sketch (implementation-phase obligation, not a shipped
  edit):**

  ```yaml
  # api/lcp-openapi.yaml — sketch under paths:, server base /v1/lcp
  /history/requests:
    get:
      tags: [History]
      summary: Paginated request-history metadata lookup
      operationId: getRequestHistory
      description: >
        Read-only audit surface (LEP-0005). Returns stored request metadata
        rows for the calling credential's scope; never envelope bodies or
        contact plaintext. Contact-derived fields are per-pair HMAC digests
        computed at read time.
      parameters:
        - { name: request_id,          in: query, schema: { type: string, format: uuid } }
        - { name: lead_id,             in: query, schema: { type: string } }
        - { name: message_type,        in: query, schema: { enum: [lead, call, ping, bid, post, ack, event] } }
        - { name: status,              in: query, schema: { type: string, maxLength: 64 } }
        - { name: vertical,            in: query, schema: { type: string } }
        - { name: caller_phone_hash,   in: query, schema: { type: string, pattern: '^[a-f0-9]{64}$' } }
        - { name: from,                in: query, schema: { type: string, pattern: '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$' } }
        - { name: to,                  in: query, schema: { type: string, pattern: '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$' } }
        - { name: limit,               in: query, schema: { type: integer, minimum: 1, maximum: 200, default: 50 } }
        - { name: cursor,              in: query, schema: { type: string } }
      responses:
        '200':
          description: Metadata rows; pagination metadata in headers.
          headers:
            X-LCP-Result-Count:  { schema: { type: integer, minimum: 0 } }
            X-LCP-Total-Results: { schema: { type: integer, minimum: 0 } }
            Link:                { schema: { type: string } }
          content:
            application/json:
              schema:
                type: object
                additionalProperties: false
                required: [requests]
                properties:
                  requests: { type: array, items: { $ref: '#/components/schemas/HistoryRequestRow' } }
        '400': { $ref: '#/components/responses/LCP001' }
        '401': { $ref: '#/components/responses/LCP002Or012' }
        '429': { $ref: '#/components/responses/LCP011' }
  ```

- **MCP:** new tool on the existing thin adapter, alongside
  `query_lead_status` (`server.py:111-121`): `get_request_history`,
  argument schema mirroring the query allowlist (same patterns enforced
  client-side before the network call, the shipped `_validated_envelope`
  discipline at `server.py:47-69` adapted to parameters); client method
  `get(path="/history/requests", params=…)` (`client.py:73-80`).
  **Recorded gap:** `client.get()` discards response headers today
  (`client.py:73-80` returns only the body) — the adapter lane MUST either
  pass headers through `_with_metadata` or fold pagination values into the
  result object (`{"requests": […], "pagination": {"result_count": n,
  "total_results": t, "next_cursor": "…"}}`); this LEP names the fold
  because MCP has no header channel.
- **SDKs:** the read surface is transport, not envelope — **no generated
  model change** (SDK bundles/manifests untouched by the row schema until
  an SDK chooses to type it; the schema-sync gate therefore does not fire).
  Each SDK MAY gain a typed client method in its API surface at
  implementation review; conformance vectors are language-neutral.
- **Conformance vectors (named, blocking at implementation):**
  `vec: history-request-row` positive/negative shapes (invalid A–D as
  corpus entries, mirroring the `slot-fields.json` precedent);
  `plat: test_history_scope_matrix_publisher_buyer_operator` (each role
  against the same fixture dataset, asserting the matrix rows, including
  `price_cents` visibility and the 401 path executed with an explicitly
  scoped `history:read` credential — **not** the wildcard default, the O2
  rule); `plat: test_history_rejects_plaintext_phone_filter` (400 class);
  `plat: test_history_phone_hash_filter_misses_erased_leads` and
  `plat: test_history_rows_after_erase_have_no_contact_derived_fields`
  (erasure pair); `plat: test_history_pagination_headers` (Link/cursor
  round-trip over >1 page, totals exact); `plat: test_history_unknown_status_filter_passthrough_empty`
  (§6 open-enum posture); MCP parity `mcp: test_get_request_history_*`
  (header→body fold). **T1 tool extension:** the wire-example gate
  (`tools/tests/test_lep_wire_examples.py:29-35`) parses only envelopes;
  implementation adds a row-shape lane so every fenced
  `### Valid wire example` JSON block in LEPs validating *rows* gets the
  same recurrence protection this record hand-ran.
- **Operational:** DB indexes for the projections (`pings.created_at`,
  `bids.received_at`, per-tenant lead lookups — SQLite + Postgres parity,
  `postgres.py` lane); metrics counters `history_queries_total{role}`,
  `history_query_duration_seconds`, `history_result_count` histogram;
  alert if the `history.query` audit write fails (audit-before-serve);
  the §9 per-sender rate-limit window shared with ingest — deployments
  SHOULD provision a separate limit class for scan-heavy operator
  credentials. `admin.py` gains nothing (the CLI stays the fallback when
  the surface is disabled).

## Alternatives considered

- **Keep it deployment-specific ops tooling (SQL views / extend `admin.py`
  only).** The decision rules demand this alternative be analyzed, so
  seriously: `admin.py` already ships `dead-letter list` (`admin.py:38-41`)
  and `list_audit_events` already exists internally (`storage.py:1085`),
  proving the *data* answerable today without any protocol change — this
  LEP does not claim otherwise. It fails on **who is asking.** The trigger
  names two audiences split across organizational lines: "platform
  operators **and publishers**". A publisher is a different legal entity
  from the platform; a CLI on the platform's box is unreachable by the
  party with the dispute, and each publisher building a bespoke side-channel
  against one deployment multiplies the disclosure risk (ad-hoc exports
  routinely leak plaintext where this design structurally cannot). The
  reference network's log exists precisely because dispute resolution is
  **cross-organization** — both parties must reconcile against the same
  authenticated record — which is interop, not operations. Honest balance
  sheet: the evidence base today is one external production mechanism plus
  roadmap wording, **no LCP adopter has spoken**; the #187 evidence ask
  is the gate that resolves this, and if adopters answer "SQL suffices,"
  `Deferred` is the legitimate disposition (LEP-0001's deferral shows the
  precedent with teeth). Note what the two-adopter rule literally gates: a
  *new universal message type*; this adds none — the interop-necessity
  framing above is the honest substitute for the mechanical bar, not a way
  around one.
- **Registered extension namespace (`{org}.ops.history`).** Structurally
  impossible for this feature: extensions travel in `payload.extensions`
  inside **messages** (EXTENSION-REGISTRY.md "Extension payload location"),
  and there is no message here — the feature *is* a read API; and LEP-0003's
  alternatives analysis already measured that the closed message objects
  (`ping`/`bid`/`post`, `additionalProperties: false`) reject
  extension-framed fields outright. Nothing to register.
- **Grow `GET /v1/lcp/leads/{lead_id}` into the search surface.** Rejected:
  it is a single-resource detail view keyed by an id the disputing party
  often *does not have* (a call-center log has a phone and a timestamp; the
  lead id is what you're trying to find), returns per-lead event payloads
  this record excludes, and paginating it (id-range scans) per lead breaks
  both the time-range filter and the header-pagination pattern the roadmap
  names. `lead_status` remains the right *detail* view; history rows point
  at it (`lead_id`) rather than duplicating it.
- **Push subscriptions instead of pull (LEP-0001).** Different capability
  (streams of *transitions* vs. lookup of *current stored state* including
  records from before any subscriber existed); LEP-0001 is Deferred with
  an unmet trigger; adopting pull now and optionally push later is
  composition, not competition.
- **Return redacted envelope bodies ("the JSON minus PII fields").**
  Rejected — a scrubber is a bug farm (nested free-text fields carry phones:
  `notes`, vertical `attributes` strings), and "return metadata" is the
  roadmap constraint's actual word. The closed row allowlist is the
  un-forgettable version of "redact."
- **Return unsalted SHA-256/SHA-1 phone digests so any party can match.**
  Rejected outright (roadmap: "LCP must use its existing per-pair
  HMAC-SHA256 … cannot be brute-forced"; §14 B3; LEP-0003 alternatives —
  mechanism yes, that hash never).

## Rollout, migration, observability, and rollback

1. **Nothing-before-endpoint:** row schema + OpenAPI path + platform route
   + scope + vectors land together under the change gates; MCP tool and the
   §8/§9 spec-text paragraphs in the same release (gates 2–5). No data
   migration exists: the surface projects tables already written, so there
   is no backfill, no stored-shape change, and **no new copy of any value**
   to migrate.
2. **Optional by construction — the rollout lever is deployment choice.**
   Deployments MAY omit the endpoint entirely (`LCP-001`/404 "Endpoint not
   found" per `service.py:174`, no §8 `history` member — today's reality
   for every deployment, unchanged). Reference platform enables it behind
   the v1.1 release with the §8 advertisement so clients negotiate rather
   than probe. There is no traffic that must be migrated because no v1.0
   caller exists for a brand-new read.
3. **Observability (what "working" means before flipping trust):**
   `history_queries_total{scope_role}` — expecting publisher/buyer roles to
   dominate if the dispute-resolution thesis is true (this is also the
   adopter-evidence instrument: a surface with zero non-operator queries
   after two quarters is *evidence against* the cross-party premise, feed
   it into the Deferred questions); `X-LCP-Total-Results` distribution;
   p95 query duration (index regression alarm); 400-class rate on the
   filter allowlist (integrator-education signal); audit write failures
   alert at severity error (a query that succeeded un-audited is a privacy
   event); zero-tolerance alert on any row emission violating the closed
   allowlist (defense-in-depth serialization check in the route — if it
   ever fires, a code path leaked a field).
4. **Rollback:** remove the route (endpoint 404s — the shape clients of a
   new surface already handle by discovery), drop the §8 `history` member.
   No stored state to unwind (read-time hashing wrote nothing), no SDK or
   envelope surface to re-pin, no wire-format commitment. A rolled-back
   deployment is byte-identical to pre-LEP operation. If only the MCP tool
   regresses, drop the tool; the REST path stands alone.

## Deferred questions

- **Adopter evidence — the gating one.** No independent LCP adopter has
  confirmed need for a cross-party history surface over per-deployment
  ops tooling. #187 asks publishers/buyers: what was your last dispute
  you could not resolve with `lead_status` + your own logs, and what would
  you have filtered by? **Trigger:** Review window; if no first-hand
  adopter evidence posts by the deadline, `Deferred` (LEP-0001 pattern)
  with the roadmap trigger wording cited unchanged as the reopen key.
- **Publisher visibility into platform↔buyer ping/bid rows.** Today's
  matrix gives publishers only its own counterparty rows; the reference
  network's log tells the publisher "pinged N networks, M answered."
  Aggregated, buyer-anonymized counters on a publisher's own lead
  (`pings_count`, `bids_count`) are withheld pending evidence — competitive
  disclosure rules between platform and buyers, and per-pair price
  confidentiality cuts across them. Trigger: first publisher adopter
  describing a dispute that requires ping-count visibility; decide with the
  marketplace `final_pricing` item (roadmap Phase 6 sibling).
- **`X-LCP-Request-Id` as a first-class history key.** The correlating
  request id is minted per dispatch (`service.py:19-24`) and persisted only
  inside `audit_events.metadata_json` (`storage.py:1060-1062`);
  `list_audit_events` requires a known resource id (`storage.py:1085-1097`).
  Whether request-log lookup joins `audit_events` (no schema change, no
  cross-record index today) or adds a `request_id` column to the request
  tables (new stored correlation) is an implementation-phase decision —
  neither shape blocks the wire contract. Trigger: first implementation
  attempt at `request_id=` lookups spanning >1 table with acceptable cost.
- **Fine-grained operator scopes.** `platform:admin`/`*` granting
  everything (`auth.py:187-189`) is a known coarse instrument (LEP-0003
  O2; #168 findings). Splitting `metrics:read`/`history:read-all`/
  `dead-letter:*` into real orthogonal scopes is a security-model change
  with its own LEP bar. Trigger: a deployment that cannot onboard
  dispute-resolution support staff without granting metrics-level scope,
  i.e. the coarseness first *costs* something.
- **Email-form contact filters (`caller_email_hash`).** Same gate as
  LEP-0003's email path: the platform hashes phone only today
  (`messages.py:48-62` docstring: "Email is NOT hashed here"; the §9 pin +
  the T9 dedup-hash vector wave are the precondition). Trigger: T9 wave
  lands email digests → the parameter is a trivially additive MINOR (open
  enum? no — closed allowlist; `caller_email_hash` added as new row
  property + parameter).
- **Bulk export / archival retrieval.** Long-window compliance pulls
  (audit spans years; cursors churn; personal-data-in-transit volume) are
  a different mechanism with its own retention analysis — explicitly NOT
  this surface's job (recorded in Alternatives as the deployment-tooling
  boundary). Trigger: a regulator-facing requirement an adopter cannot
  satisfy from `lead_status` + history pages + their own storage.
- **Cross-tenant operator aggregation.** `platform:admin` is scoped within
  a tenant here; a group-level operator view spanning tenants is a
  deployment/instance topology question with no protocol content today.
  Trigger: first multi-tenant adopter with a group-level compliance team.

**Revisit trigger (umbrella):** adopter evidence on #187 during Review;
first reference-platform implementation attempt of the scope matrix; or
revival of the LEP-0001 subscription track (composes with, never replaces,
this pull surface).

## Verification runs (executed 2026-11-03)

No implementation is authorized, so "schema-honest" for this record means:
the drafted row schema and every row-shaped example were executed against
the draft, and every pinned value recomputed. Environment: repository
venv, `jsonschema 4.26.0` (matches `requirements-ci.txt`),
`Draft202012Validator` with format checking.

1. **Digest recomputation.** `hmac.new(b"pair-secret-for-vectors-0123456789",
   b"+15125550147", hashlib.sha256).hexdigest()` →
   `c47aff331606437528b34b7f919eb37704e87ee35eed80e52cfa36826a4a2e12` —
   byte-equal to `test-vectors/sdk/contact-hash.json`
   `lcp-contact-hash-phone-001`; every example digest in this record is
   this value and any reviewer can reproduce it (per the vectors file's
   stated recomputation rule).
2. **Schema validity:** `Draft202012Validator.check_schema` on the drafted
   row schema (embedded above verbatim) → OK.
3. **Positive shapes:** all five example rows across the three positive
   examples (publisher `PINGED` lead row; buyer `accept` bid row with
   `price_cents`/`currency`; delivered `post` row with `caller_phone_hash`
   — appearing in both the publisher-illustration and buyer-filter examples;
   erased-lead row `ERASED` without contact fields) →
   `row[0..4] PASS` — `jsonschema 4.26.0`, format checking on.
4. **Negative shapes:** invalid A (`consumer` object on a row) →
   `REJ "Additional properties are not allowed ('consumer' was
   unexpected)"`; invalid B (plaintext phone as `caller_phone_hash` value)
   → `REJ "'+15125550147' does not match '^[a-f0-9]{64}$'"`; invalid C
   (40-hex SHA-1 form) → `REJ` same pattern; invalid D (`currency: "usd"`)
   → `REJ "'usd' does not match '^[A-Z]{3}$'"`; invalid D'
   (`created_at` sub-second `"2026-11-03T10:20:05.123Z"`) → `REJ` (pattern);
   invalid D'' (`price_cents: "29.00"`) → `REJ "'29.00' is not of type
   'integer'"` → `invalid[0..5] REJ` as quoted.
5. **Status-vocabulary grounding:** example `status` literals taken from
   shipped code, not invented: §3 lead statuses (`SPEC.md:255-259`);
   pings `'OPEN'`/`'EXPIRED'`/`'WON'`/`'SUPPRESSED'`
   (`storage.py:2102`, `storage.py:2146`, `storage.py:2173`,
   `storage.py:1534`); bids `accept` (`schemas/bid.json` decision enum);
   deliveries `DELIVERED`/`REDACTED` (`storage.py:2726-2732`); leads
   `ERASED` (`storage.py:2704-2708`).
6. **Baseline repository gates** on this record + registry row:
   `tools/check_lep_registry.py`, `tools/check_doc_citations.py`,
   `tools/check_reserved_fields.py`, `test-vectors/conformance.py`,
   `tools/tests/test_lep_wire_examples.py` — recorded in the PR's CI.
   (Heading note repeated: this record uses `### Valid wire example`
   headings; `test_lep_wire_examples` consumes only `### Valid example`
   envelope blocks, so Draft envelopes-in-disguise cannot sneak past it
   *and* row examples intentionally do not enter its envelope lane until
   the named tool extension lands.)
