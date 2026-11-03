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

**Trigger (verbatim, roadmap Phase 6).** "platform operators and publishers
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

**Internal evidence — the data already exists; the surface does not.** "Exists"
means **stored**: the row universe is exactly the backing tables named in the
projection mapping below, and response artifacts that are constructed but never
persisted (acks, `messages.py:65`) mint no rows and are cut from v1. With that
scoping, the reference platform already records, in first-class tables,
everything the roadmap names as metadata:

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
  `lead_status`, `router.py:1660`);
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
(role-filtered detail view, `service.py:167-172`, `router.py:1646-1728`),
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
  (`authorize_lead_read`, `router.py:1646-1658`, filters by counterparty).
  Honest delta the post-dossier revision records: the shipped authorizer
  binds the **counterparty**, never the **tenant** — the row-level tenant
  equality in the matrix is a *new* obligation of this surface, not
  inherited behavior (see "Tenant binding at query time", #202 F2).
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
| `lead_id` | string, 1–64 chars, anchored charset (row pin, rule 6); phone/email-shaped values → 400 | `leads.lead_id` / `pings.lead_id` / `bids.lead_id` / `deliveries.lead_id` |
| `message_type` | one of `lead call ping bid post ack event` | the closed §4 type set. `ack` is a legal filter value but matches **no stored record** in v1 — acks are response artifacts (`build_ack`, `messages.py:65`), never persisted; see the row universe and the Deferred question |
| `status` | string, 1–64 chars, anchored charset (row pin, rule 6) | the row's stored status value (§3 lead statuses; `OPEN`/`WON`/`EXPIRED`/`SUPPRESSED` for pings; `accept`/`reject`/`pass` for bids; delivery statuses). **Open enum** — an unrecognized *well-shaped* value is a valid query returning the (possibly empty) matching set, per §6's store-and-pass rule; it MUST NOT be a validation error. Shape hygiene is not vocabulary closure: §6 governs whether a status is *known*, rule 6 governs whether the string may *ride the surface* — contact-shaped text (phone runs, emails) is `400 LCP-001` like a plaintext phone in `caller_phone_hash` |
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
    "lead_id":      {"type": "string", "minLength": 1, "maxLength": 64, "pattern": "^[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}$", "not": {"pattern": "(?<![-\\w])\\d{10,15}|^[0-9 .()+-]{10,}$|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}"}, "description": "Publisher-supplied record handle, pinned at the projection (rule 6): anchored charset plus refusal of the §3 contact shapes — a leading 10-15 digit phone run, an all-digits/punctuation value, or an email address cannot ride a history row; hyphen-delimited digit segments (uuid-style ids) intentionally pass."},
    "message_type": {"enum": ["lead", "call", "ping", "bid", "post", "event"], "description": "Stored-record classes only. 'ack' is excluded: acks are response artifacts, no table backs them (row universe, below; Deferred questions)."},
    "direction":    {"enum": ["received", "sent"], "description": "From the platform's side: an inbound envelope it received, or an outbound record it sent/delivered."},
    "status":       {"type": "string", "minLength": 1, "maxLength": 64, "pattern": "^[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}$", "description": "Stored record status (open pass-through per §6 — the vocabulary stays open; the shape is pinned, rule 6)."},
    "sender_id":    {"type": "string", "minLength": 1, "maxLength": 64, "pattern": "^[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}$"},
    "receiver_id":  {"type": "string", "minLength": 1, "maxLength": 64, "pattern": "^[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}$"},
    "vertical":     {"type": ["string", "null"], "maxLength": 64, "pattern": "^[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}$"},
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
schema's `additionalProperties: false`, not a scrub pass. Post-dossier, the
claim is scoped to exactly what `additionalProperties` buys: it closes
**members**, not **text inside allowed strings**. `lead_id` is
publisher-supplied free text whose shipped constraint is literally
`{"type": "string"}` (`schemas/lead.json:16-18`, §4 lead), and `erase_lead`
retains it (`storage.py:2800-2805`), so a contact planted inside an
identifier can survive both the envelope validator and an erasure. The
honest property of the closed shape is therefore "no contact **fields**";
contact-shaped *text* is blocked by the identifier pins of rule 6 below, a
bounded heuristic rather than a structural impossibility — the residual risk
is named there, not waved away.

### Row universe and projection mapping

v1 mints rows only from tables that actually store records. This is the
post-dossier correction of #202 F3/F4: the matrix previously promised `ack`
rows and keyed child rows on columns the child tables do not have.

| Source table | Row class | `direction` | `sender_id` / `receiver_id` | `status` | `created_at` |
|---|---|---|---|---|---|
| `leads` (`storage.py:512`) | `lead`, `call` | `received` | own columns | own column | own column |
| `pings` (`storage.py:623`) | `ping` | `sent` (platform → buyer) | platform id / `buyer_id` column | own column (`OPEN`/`EXPIRED`/`WON`/`SUPPRESSED`) | own column |
| `bids` (`storage.py:635`) | `bid` | `received` (buyer → platform) | `buyer_id` column / platform id | `decision` column (`accept`/`reject`/`pass`) | `received_at` |
| `deliveries` (`storage.py:650`) | `post`, `event` (outbound webhook records; a `kind=ping` delivery attempt projects onto its `pings` row's `status`/timestamps rather than minting a duplicate row) | `sent` | platform id / `buyer_id` column | own column (`PENDING`/`RETRY`/`DELIVERED` (`router.py:1044`)/`FAILED`/`REDACTED`) | own column |
| `lifecycle_events` (`storage.py:670`) | `event` (persisted lifecycle record) | per stored envelope direction | stored envelope `message.sender_id`/`receiver_id` | `event_name` column (`DELIVERED`/`ACCEPTED`/…, §4 event enum, open) | `timestamp` column |

**No `ack` rows.** `ack` (§4, `SPEC.md:494`) is a response constructed
per message (`build_ack`, `messages.py:65`); no table stores acks, so a
promised ack row would be invented from unstored state — precisely what the
record's "no new stored state" justification forbids. `ack` is cut from the
row schema's `message_type` enum (the §4 type set itself is untouched; the
`message_type=ack` **filter** remains a legal closed-set value returning the
empty set), and a durable ack store is a Deferred question with its own
backing trigger. Where a table has a column, the row field reads from the
column; identifier fields for child rows may additionally read the stored
envelope's `message` member (`envelope_json` is a projection *source* for ids
only — raw envelope bodies are still never returned, per the closed shape).
Optional row fields appear only where a source column exists: `expires_at`
from `leads`/`pings` only; `bids` carries neither `expires_at` nor
`updated_at`, so its rows omit both. `pings`/`bids`/`deliveries`/
`lifecycle_events`/`dead_letter_jobs` have no `tenant_id` column, so child
rows are tenant-attributed by joining their `lead_id` to the `leads` parent
(see tenant binding, below); a child row with no parent is unattributable
and excluded. Mapping and row-set faithfulness are blocking-tested by
`plat: test_history_row_projection_covers_stored_tables` (fixture with
lead+ping+bid+post+event rows; assert the row set matches this table and no
row is minted from unstored state).

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
6. **Identifier text is pinned at the projection, not trusted at the
   source** (post-dossier rule, #202 F1). `lead_id` is publisher-supplied
   and protocol-legal whatever the publisher mints — the shipped lead
   schema constrains it exactly as `{"type": "string"}`
   (`schemas/lead.json:16-18`) — and `erase_lead`'s redaction whitelist
   **retains** it (`storage.py:2800-2805`). The ARB lane executed the
   consequence: a lead whose `lead_id` is
   `"+15559876543-jane.q.public@example.com"` passes the shipped
   `SchemaValidator`, ingests, erases, and its history row still carries
   the phone and address verbatim (dossier attempt 7 / invalid F below).
   The row schema therefore refuses, **at emission and at query time**,
   any `lead_id` that matches §3's contact shapes — (i) a run of 10–15
   digits starting the value or not preceded by a letter/digit/hyphen
   (the national-number form of the E.164 pattern pinned at
   `schemas/core.json:17` and the shape of a phone pasted into an id),
   (ii) a value consisting only of digits and phone punctuation
   (`^[0-9 .()+-]{10,}$` — a formatted phone), or (iii) an email address
   (the `consumer.email` form, `schemas/core.json:16`) — and bounds every
   free-text identifier field (`status`, `sender_id`, `receiver_id`,
   `vertical`: anchored charset + `maxLength` 64), so no unbounded or
   contact-shaped string reaches a counterparty. The run-detection is
   deliberately narrower than "any digit soup ≥10 chars": hyphen- or
   letter-delimited digit segments (uuid-tail ids, `lead_20261103-0001`
   date-sequence ids) PASS — the revision battery showed a wider run
   regex misfires on them, and silently omitting the join key from 0.4%
   of legitimate uuid-shaped ids is a worse audit failure than the
   residual it accepts.
   **Legacy nonconforming ids — decision: omit, do not coerce.** For a
   stored row whose `lead_id` fails the pin, history emission MUST
   **omit** the `lead_id` member (same absent-never-blank discipline as
   rule 2); the row itself survives with `request_id` as its handle.
   Coercion is rejected on evidence, not taste: (i) truncation or
   casefolding invents a collision namespace — two consumers' distinct
   ids can fold to one pinned value, fabricating a false join on a
   dispute-resolution surface, which is strictly worse than an absent
   field; (ii) a hashed substitute would be a new disclosure oracle
   requiring its own per-pair keying argument (the LEP-0003 analysis)
   this LEP does not open; (iii) no caller loses recourse — the
   publisher that minted the id still holds it via its own ingest ack
   and `lead_status`, and the stored envelope is untouched (retention of
   the publisher's own text stays §9 deployment-defined). An omission
   counter (`history_lead_id_omitted_total`) is the operational alert
   that a publisher is minting contact-shaped ids. Blocking tests:
   `vec: history-request-row-identifier-shape-pins` (negative shapes:
   E.164-shaped id, email-shaped id, 900+ char id),
   `plat: test_history_planted_contact_in_lead_id_rejected_or_pinned`,
   `plat: test_history_legacy_nonconforming_lead_id_omitted`.
   **Residual risk, named rather than argued away:** the pins are bounded
   heuristics. They refuse the §3 contact shapes, unbounded stuffing,
   and the shipped-dossier plant; they do not claim to recognize every
   conceivable contact encoding inside 64 charset-safe characters. A
   publisher smuggling a bespoke encoding into its own ids remains a
   counterparty-data problem under §9 (the data is the publisher's, the
   rows are scoped to parties already holding it), not something a read
   projection can structurally prevent.

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
`authorize_lead_read` (`router.py:1646-1658`), which answers "not found"
404 rather than "forbidden" so existence itself is not disclosed. For a
list surface the analogue is **filtering, not erroring**: out-of-scope rows
simply do not appear; a `request_id`/`lead_id` filter that exists but is
not yours returns `200` with an empty set.

| Caller | Rows visible | Row field restrictions |
|---|---|---|
| **Publisher** (sender of `lead`/`call` messages) | `direction=received` rows where `sender_id == caller` (its own sends: leads, calls, events it posted) + `direction=sent` rows where `receiver_id == caller` per the projection mapping (the outbound records addressed to it; acks are response artifacts with no backing table and mint no rows — see row universe and Deferred questions) | `price_cents`/`currency` absent (bids it may see are never its rows); `caller_phone_hash` computable only for rows whose lead still holds plaintext; `lead_id` subject to rule 6 pins/omission |
| **Buyer** (sender of `bid`, receiver of `ping`/`post`) | rows where `sender_id == caller` (its bids/events) or `receiver_id == caller` (pings addressed to it, posts delivered to it — no ack rows exist, rule 6/row universe) | `price_cents` only on rows whose record carries a price it is party to (its own bids' `bids.bid_price_cents`, `storage.py:635-648`) — never another buyer's; `caller_phone_hash` as above |
| **Platform operator** (`platform:admin` credential, or the wildcard — see critique) | all rows within the operator's tenant | `caller_phone_hash` omitted unless the operator credential itself is the row's counterparty pair (operator holds the plaintext; a hash under nobody's pair key carries no information the operator can compare against a pair's stream); per-pair confidentiality of *prices* is NOT extended to operators (they already compute payables, `payable_records`, `storage.py:598`) |
| **Any caller, any role** | additionally scoped to `tenant_id` equality with the authenticated credential (`credentials.tenant_id`/`leads.tenant_id`, `storage.py:501`, `storage.py:514`) — a **new obligation of this surface, not inherited shipped behavior**; the previous "shipped multi-tenant list pattern" framing is corrected in "Tenant binding at query time" below | — |

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
- Every credential that holds `*` or `platform:admin` scope automatically
  gets this endpoint — those are the shipped auto-grant literals, verbatim
  `"*" in scopes or required_scope in scopes or "platform:admin" in scopes`
  (`auth.py:187-189`); an earlier draft of this bullet named
  `platform:read`, a literal that exists nowhere in shipped code or SPEC
  (#202 F4 — corrected). Wildcard auto-grant is today's shipped behavior and
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

#### Tenant binding at query time (new obligation, #202 F2)

The matrix previously presented credential→tenant equality alongside "the
shipped multi-tenant list pattern" as if the platform already enforced it.
The ARB lane executed the shipped code and the framing was wrong; it is
corrected in record. **No shipped read path binds a reader credential to a
tenant.** `authorize_lead_read` (`router.py:1646-1658`) filters by
counterparty only — a `'*'`-scoped credential provisioned in `tenant-b`
reads a `tenant-a` lead through the real dispatch path with `200` (dossier
attempt 13). The cited offer-listing precedent scopes by
`config.routing_tenant_id` — a **deployment config value** fed to
`store.list_offers(tenant_id=…)` (`router.py:1773-1780`), not by the
caller's `credentials.tenant_id`; `test_platform.py:792-805` exercises that
store parameter, not a credential. LEP-0005 therefore does not inherit a
protection that does not exist: tenant equality is a **new** obligation this
surface adds (it is stricter than the shipped lead-read precedent). The
shipped cross-tenant lead-read gap is itself filed to the platform lane via
#202 F2; this record binds its own surface and commits the stronger rule.

Enforcement design — following the single-decision-point discipline of
LEP-0003's slot award (one transaction resolves the decision; nothing
downstream re-decides):

1. **One decision point.** A single row-builder in the implementation
   (`history_rows`, reference platform) composes the union of the
   projection tables from the row universe above, and the tenant predicate
   `leads.tenant_id = :caller_tenant` appears **exactly once**, at that
   outer join. The route, the service dispatch, and the MCP adapter consume
   its output and add no second filter; per-row tenant checks in response
   serialization are an implementation bug, not defense-in-depth — by then
   the row set has already crossed the boundary.
2. **Credential-sourced, never query-sourced.** `:caller_tenant` resolves
   through `auth.tenant_for` (`auth.py:202-208`) for the authenticated
   sender at dispatch time — the same posture as the Security section's
   rule that "authorization is computed from the authenticated credential,
   never from query text". A `tenant` query parameter does not exist and
   MUST NOT be added.
3. **Child-table attribution.** The row-minting child tables — `pings`/
   `bids`/`deliveries`/`lifecycle_events` — carry no `tenant_id` column
   (nor does `dead_letter_jobs`, which is out of the v1 row universe
   anyway; `storage.py:623-675`, `storage.py:718-741`); every child row is
   attributed by joining its `lead_id` to the `leads` parent inside the
   same builder. A child row with no parent is unattributable and
   excluded.
4. **No wildcard bypass.** `'*'`/`platform:admin` satisfy the *scope*
   check and broaden an operator's row set **within the credential's
   tenant only** — tenant equality is row-scoping, and row-scoping rides
   inside every scope (bullet above). The `'*'` shipped-behavior critique
   stays in record: binding the wildcard to a tenant is a floor, not a
   fix, for a surface disclosing prices and timing graphs.

Named blocking tests (implementation PR, #202 format):
`plat: test_history_cross_tenant_rows_invisible` (two tenants, one
`'*'`/`platform:admin` credential per tenant, assert empty sets across the
boundary), `plat: test_history_operator_scope_within_tenant`, and
`plat: test_history_unattributable_child_rows_excluded`.

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

### Valid wire example — erased lead (contact fields absent)

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
made mechanical **at the member level**: the shape has no contact *fields*,
so there is no scrub step to forget. It is not a claim that no identifier
can carry contact text — text smuggled inside an allowed string is a
different vector, closed only by the rule-6 pins (invalid F), and a receiver
detecting this shape from a conformant-claiming platform treats it as a
conformance failure, not data.

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

### Invalid wire example F — phone-shaped `lead_id` plant (post-dossier)

The ARB lane's executed plant (dossier attempt 7): a publisher mints a lead
whose id **is** the consumer's contact information. This is protocol-legal —
`schemas/lead.json:16-18` constrains `lead_id` only as `{"type": "string"}` —
and, because `erase_lead` retains `lead_id` (`storage.py:2800-2805`), the
pre-revision draft row schema accepted it and would have re-emitted it to
counterparties forever. Under the rule-6 pins the row shape refuses it three
times over: the leading `+` fails the anchored charset, the 11-digit run
fails the §3 phone-shape refusal, and the address fails the email-shape
refusal:

```json
{
  "request_id": "e40e8400-0000-4000-8000-000000000206",
  "lead_id": "+15559876543-jane.q.public@example.com",
  "message_type": "lead",
  "direction": "received",
  "status": "PINGED",
  "created_at": "2026-11-03T10:20:05Z"
}
```

REJECTED — `'+15559876543-jane.q.public@example.com' does not match
'^[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}$'` (revision battery, both validator
legs). A conformant platform therefore never serializes this row: emission
**omits** the `lead_id` member for stored rows whose id fails the pin (rule
6's legacy decision), and the email-only and 900+ character variants are
refused the same way. A receiver seeing the shape from a conformant-claiming
platform treats it as a conformance failure.

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
  and record-level identifiers the caller already holds for in-scope rows
  (`lead_id` is publisher-supplied text, pinned at the projection by
  rule 6 — see the residual-risk note there);
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
  contact-free in every **platform-derived** field (valid example C: no
  `consumer` member can exist, and no preimage remains to key a hash
  against) — a `caller_phone_hash` filter cannot match an erased lead.
  The one member `erase_lead` **retains** is the publisher-minted
  `lead_id` (`storage.py:2800-2805`), which erasure does not scrub; it is
  gated at emission by the rule-6 pins, not by the redaction whitelist —
  "structurally contact-free" held for members before this revision and
  stopped holding for planted identifier text at the dossier's executed
  attempt 7. This pair of guarantees is asserted as blocking vectors, not
  trusted to prose: `plat: test_history_phone_hash_filter_misses_erased_leads`
  and `plat: test_history_rows_after_erase_have_no_contact_derived_fields`
  (the shipped redaction suite is the baseline these extend), together
  with `plat: test_history_planted_contact_in_lead_id_rejected_or_pinned`
  (rule 6) closing the whitelist-retained-text path.
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
- *Abuse cases:* (a) cross-tenant enumeration — the **new** tenant binding
  at query time (single decision point, no wildcard bypass; not a shipped
  protection, see the matrix section) + authz matrix + per-sender §9 rate
  limits (`storage.py:694`,
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
        - { name: lead_id,             in: query, schema: { type: string, minLength: 1, maxLength: 64, pattern: '^(?!.*(?<![-\w])\d{10,15})(?![0-9 .()+-]+$)(?!.*[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}$' } }
        - { name: message_type,        in: query, schema: { enum: [lead, call, ping, bid, post, ack, event] } }  # §4 closed set; 'ack' matches no stored row in v1
        - { name: status,              in: query, schema: { type: string, minLength: 1, maxLength: 64, pattern: '^[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}$' } }
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
  `vec: history-request-row` positive/negative shapes (invalid A–D and F
  as corpus entries, mirroring the `slot-fields.json` precedent);
  `vec: history-request-row-identifier-shape-pins` (rule 6 negatives:
  E.164-shaped id, email-shaped id, 900+ char id, dashed phone-run id);
  `plat: test_history_planted_contact_in_lead_id_rejected_or_pinned` and
  `plat: test_history_legacy_nonconforming_lead_id_omitted` (rule 6
  emission path, #202 F1);
  `plat: test_history_cross_tenant_rows_invisible`,
  `plat: test_history_operator_scope_within_tenant`, and
  `plat: test_history_unattributable_child_rows_excluded` (tenant
  binding, #202 F2);
  `plat: test_history_row_projection_covers_stored_tables` (row universe
  — lead+ping+bid+post+event fixture asserts the projection mapping and
  that no ack row is minted from unstored state, #202 F3);
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
   found" per `service.py:172`, no §8 `history` member — today's reality
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
- **Ack row class (post-dossier, #202 F3).** `ack` (§4, `SPEC.md:494`) is
  a per-message response artifact (`build_ack`, `messages.py:65`) that no
  table stores, so the v1 row universe is requests + deliveries/events
  only and `ack` is cut from the row schema enum (the `message_type=ack`
  filter stays a legal §4 value returning the empty set). A durable ack
  store is **new stored state** and the LEP bar for that is its own
  analysis: ack rows carry publisher-minted `lead_id`/
  `original_message_id`, so they inherit rule 6 pins/omission, the §9
  erasure projection (redact-on-erase like every other envelope), a
  retention decision, and `plat: test_history_ack_rows_backs_promised_rows`
  coverage before any ack row may be promised again. **Trigger:** the
  first adopter dispute that cannot be reconciled from delivery rows plus
  the publisher's own ack receipt, or a written implementation proposal
  for an acks table with that erasure/retention analysis attached.
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
   deliveries `DELIVERED` (`router.py:1044`) / `REDACTED` (`storage.py:2726-2732`); leads
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

Post-dossier, the battery was re-run with the rule-6 pins in the drafted
schema; the full re-validation (published + overlay trees, all valid rows,
all invalids, plus the new invalid F plant) is recorded in
"Revision 2026-11-03 (post-dossier)" at the end of this record.

## Revision 2026-11-03 (post-dossier)

Record-only revision resolving the ARB L-Platform+L-Security dossier on
[#187](https://github.com/SpearSystems/LCP/issues/187) (tracked in
[#202](https://github.com/SpearSystems/LCP/issues/202)) at reviewed SHA
`49e84794ae61ef8c50ff0269460ff2b39905f0e0`; re-validated at repo SHA
`67fc16ef7fa8433cc90eac3a306f8748fdc03d6d` (citations below re-pinned for
that range's `router.py` shift). **Status stays Draft**: no implementation
is authorized; every `vec:`/`plat:` test named in this revision belongs to
the implementation PR (G5), not to this record. The registry row and the
coverage matrix are untouched.

### Findings → edits

| Dossier item | Disposition in record |
|---|---|
| **F1 (major)** — contact text smuggled through pattern-free `lead_id`, survives the erasure whitelist; "cannot carry a contact at all" over-claimed | Rule 6 added (identifier pins: `lead_id` charset+`maxLength` 64 + §3 contact-shape refusal; `status`/`sender_id`/`receiver_id`/`vertical` pinned); legacy nonconforming ids: **omit at emission, never coerce** (decision + defense in rule 6); structural claims narrowed to "no contact **fields**" (row-shape paragraph, invalid A prose, erasure bullet, erased-example heading); residual risk named; invalid example F added (the dossier's executed plant); `schemas/lead.json:16-18` shipped constraint cited honestly |
| **F2 (major)** — tenant equality cited a shipped precedent that binds no reader credential | Matrix row rewritten as **new obligation**; "Tenant binding at query time" section added: single-decision-point design (one `leads.tenant_id = :caller_tenant` predicate in the row builder, credential-sourced via `auth.tenant_for` `auth.py:202-208`, child-table join/parent-exclusion rule, no wildcard bypass, `'*'` critique retained); shipped reality cited honestly (`router.py:1646-1658`, `router.py:1773-1780`, `test_platform.py:792-805`); abuse case (a) updated; blocking tests named for the implementation PR |
| **F3 (major)** — `ack` rows promised with no storage backing | Ack cut from the v1 response surface: removed from the row schema `message_type` enum and both matrix rows; "Row universe and projection mapping" section added (per-table projection: `leads`/`pings`/`bids`/`deliveries`/`lifecycle_events` → `direction`/ids/`status`/`created_at`, columns-first with envelope `message` as id projection source; `expires_at`/`updated_at` only where a column exists); `message_type=ack` stays a legal §4 filter value returning the empty set; "the data already exists" scoped to **stored** state; Deferred question "Ack row class" added with backing trigger |
| **F4 (minor)** — `platform:read` names no shipped literal | Reworded to the shipped auto-grant literals, quoting `auth.py:187-189` (`"*" in scopes or required_scope in scopes or "platform:admin" in scopes`) |
| **F5 (minor)** — citation precision | 404 raise fixed `service.py:174` → `service.py:172`; deliveries `DELIVERED` grounded at `router.py:1044` (was `storage.py:2726-2732`, which covers only the erasure `REDACTED` write); roadmap quote restored to lowercase "platform" (`docs/V1.1-ROADMAP.md:300`); all `router.py` anchors re-pinned for `67fc16e` (`1615-1627`→`1646-1658`, `1615-1697`→`1646-1728`, `1629`→`1660`, `718-747`→`735-778`, `1699-1737`→`1730-1768`) |

### Battery re-run (executed 2026-11-03, post-edit)

Environment: repo venv, `jsonschema 4.26.0` (matches `requirements-ci.txt`).
Both validator legs — plain `Draft202012Validator(row_schema,
format_checker=FormatChecker())` and the platform overlay construction
(`registry=` core.json, as built in `validation.py:64-86`) — on every fenced
row block, plus rule-6 probes. The new guard's exact refusal semantics came
out of the battery itself: the first wide phone-run regex misfired on
`lead_20261103-0001` and uuid-tail ids (FAIL output, 2026-11-03), so the run
detect was tightened to digit-starts/all-punct-values/email — the trade is
documented in rule 6. Final transcript (900+ char instance echoes
truncated; this section adds no `json` fences, so parsing is unchanged by
appending it):

```text
jsonschema version: 4.26.0
record sha256 (pre-append of this transcript section): 23daaca3ecf32863…
json blocks found: 6
META: check_schema(row_schema) OK

== POSITIVE SHAPES (all must PASS, both legs) ==
row[0] plain=PASS overlay=PASS mt=lead  status=PINGED    lead_id=lead_def456
row[1] plain=PASS overlay=PASS mt=bid   status=accept    lead_id=lead_def456
row[2] plain=PASS overlay=PASS mt=post  status=DELIVERED lead_id=lead_def456
row[3] plain=PASS overlay=PASS mt=post  status=DELIVERED lead_id=lead_def456
row[4] plain=PASS overlay=PASS mt=lead  status=ERASED    lead_id=lead_abc123
wrapper[0..2] list-shape PASS

== STANDALONE EXAMPLES (invalid A + invalid F; REJ both legs) ==
invalid A plain: REJ | "Additional properties are not allowed ('consumer' was unexpected)"
invalid A overlay: REJ | same
invalid F plain: REJ | "'+15559876543-jane.q.public@example.com' does not match '^[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}$'"
invalid F overlay: REJ | same

== NEGATIVE PROBES (REJ, both legs) ==
consumer-plant          : REJ | additionalProperties
phone in hash field     : REJ | "'+15125550147' does not match '^[a-f0-9]{64}$'"
40-hex SHA-1 in hash    : REJ | same pattern
lowercase currency      : REJ | "'usd' does not match '^[A-Z]{3}$'"
sub-second created_at   : REJ | whole-second pattern
float money             : REJ | "'29.00' is not of type 'integer'"
F plant phone+email id  : REJ | charset pattern (leading '+')
900-char stuffed id     : REJ | charset pattern (leading '+', echo truncated)
email-only id           : REJ | "should not be valid under {'pattern': …email branch}"
dashed phone-run id     : REJ | "should not be valid under {…all-punctuation branch}"
10-digit id             : REJ | "should not be valid under {…digit-start branch}"
phone sender_id         : REJ | charset pattern
65-char status          : REJ | charset/length

== POSITIVE BOUNDARY PROBES (PASS both legs) ==
lead_20261103-0001 (date-seq id)   : PASS
id:8675309 (7-digit opaque)        : PASS
64-char id at boundary             : PASS
uuid-form id (12-digit tail)       : PASS
unknown future status (open enum)  : PASS
status 36-char dotted text         : PASS (platform-minted field: bound, not contact-guard)

== QUERY-PARAM PROBES (OpenAPI sketch lead_id = guarded pattern) ==
'+15559876543-jane.q.public@example.com': REJ    'jane.q.public@example.com': REJ
'5125550147': REJ                        '555-123-4567': REJ
'lead_def456': PASS                      'lead_20261103-0001': PASS
status param 'NEW_UNKNOWN_STATUS': PASS  '+15125550147': REJ

== MESSAGE_TYPE ENUM / DIGEST ==
row enum: ['lead', 'call', 'ping', 'bid', 'post', 'event'] | 'ack' present: False
digest recomputed == vector lcp-contact-hash-phone-001: True

BATTERY: ALL PASS
```

Repository gates on the revised record (same session): `tools/check_doc_citations.py`
`doc-citation-drift: 275 citation(s) checked, 0 broken [advisory]` ·
`tools/check_lep_registry.py` passed (7 proposals) ·
`tools/check_reserved_fields.py` passed · `test-vectors/conformance.py` ALL
56 tests PASSED · `tools/tests/test_lep_wire_examples.py` (unittest lane) 3
tests OK. Full CI recorded on the PR.

Remaining for the implementation PR (unticked in #202 by design, G5): the
`vec:`/`plat:` tests named in rule 6, the tenant-binding section, the row
universe, and the conformance bullet.
