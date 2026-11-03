# LCP Operations Runbook
> **Operations page · Page 4 of 6**

## Health and readiness

Use the reference health endpoints through the deployment layer:

- `GET /health/live`: process liveness only.
- `GET /health/ready`: database connectivity/readiness.
- `GET /v1/lcp/metrics`: aggregate queue, retry, lease, scanner, deletion, and
  dead-letter counts. Require `platform:admin`; the response contains no lead,
  buyer, tenant, URL, or consumer identifiers.
- Worker health: last successful poll, queue age, and lease expiry health from
  the metrics endpoint or a supervisor-specific health check.

Do not expose database or credential health details to unauthenticated callers.

## Metrics and alerts

Collect labels that do not contain PII:

- Intake requests by status/code and message type.
- Validation/auth failures by code and sender class.
- Rate-limit responses.
- Duplicate and idempotency conflicts.
- Match decisions by reason and offer ID.
- Auction expiry/no-bid counts.
- Delivery success, retry, failure, and queue age.
- Webhook latency and response classes.
- Database latency/connection pool usage.
- Worker lease expiry and crash/restart counts.
- Attachment upload counts, hash/MIME rejection counts, malware-scan queue age,
  download authorization failures, and storage capacity.
- Payable outcomes by offer, pending validation age, monthly target progress,
  remaining pace, disputes, and refunds.
- Publisher mapping failures and active mapping version by brand/form.

Alert on sustained auth failures, PII validation failures, queue growth,
failed deliveries, database saturation, backup failures, unusual volume from a
tenant, attachment capacity/malware backlog, open dead letters, and any offer
that is materially under-paced toward its monthly payable minimum.

### Clock sync

The HMAC replay window is enforced against each host's system clock:
`auth.py` in the reference platform accepts a signed request only when its
`X-LCP-Timestamp` is within `LCP_REPLAY_WINDOW_SECONDS` (default 300) of the
server's current time, in either direction. The platform stamps outbound
signed webhook deliveries with the same clock, and buyers verifying those
timestamps apply the same rule against their own clocks.

- Run an NTP daemon (`chronyd` or `ntpd`) on every API, worker, and ingress
  host, pointed at the same trusted time sources. Require the same of
  publisher and buyer peers during onboarding; their clocks decide whether
  your webhooks verify and whether your API accepts their traffic.
- Alert when any host's reported clock offset exceeds one second — that is
  well inside the replay window, so drift is fixed before it becomes an
  outage.
- Track `LCP-012` "Timestamp is outside the replay window" responses broken
  out by sender. A single sender failing while others succeed is that
  sender's clock, not an attack.
- Never widen `LCP_REPLAY_WINDOW_SECONDS` to mask skew; keep it at five
  minutes or shorter per the deployment guide.

The reference platform exposes only aggregate metrics. Example response shape:

```json
{
  "routing": {"pending": 0, "processing": 0, "dead_letter": 0, "lease_expired": 0},
  "delivery": {"pending": 0, "retry": 0, "failed": 0, "dead_letter": 0},
  "attachments": {"scanner_backlog": 0, "deletion_pending": 0, "deletion_failed": 0},
  "dead_letters": {"open": 0, "quarantined": 0}
}
```

### Shared-lead slot budget and contact reports

LEP-0003 makes slot accounting a platform invariant: the budget counts
**distinct awarded buyers** (`max_buyers`; an undeclared `shared` lead has a
budget of 1; `exclusive` is the one-slot special case), the check and the
award write happen in one transaction, and a partial unique index
(`(lead_id, offer_id)` where `kind = 'post'`) backs the invariant at the
storage layer.

- **Alerts that must never fire.** Oversell attempts (any second awarded
  buyer beyond `max_buyers`, or any second post row for an already-awarded
  `(lead, offer)`) must run permanently zero - a nonzero count is a
  conformance bug, page on it. Same rule for zero-slot pings
  (`slots_available: 0` on the wire; the platform stops pinging instead).
- **Steady-state metrics** (rollout §4): slot-consumption histogram and
  time-to-sold-out per shared lead; `CONTACTED` report rate and
  contacted-hash match rate per post; duplicate-contact dispute rate.
- **Upgrade order.** `initialize()` creates the award index on startup. A
  database that already contains duplicate post rows for one
  `(lead_id, offer_id)` - possible only through the pre-LEP-0003 race -
  will **fail startup loudly**. Reconcile those rows under change control
  before upgrading (billing review: both copies may have been delivered);
  never silently delete ledger history.
- **Capability gating is the emission switch.** Slot/contacted fields are
  sent only to receivers whose credential record advertises a full
  `major.minor.patch` element with `major == 1` and `minor >= 1`
  (`lcp_versions` in `upsert_credential`; parsed comparison, never string
  prefix - an element like `"1.1"` or `"1.10.0"` does not open the gate for
  v1.1). Non-advertising receivers keep receiving bit-for-bit v1.0 bytes.
  Rollback = stop advertising and/or drop the v1.1 element; no data-format
  migration is needed (slot counters are reconstructible from delivery
  history; contacted digests are disposable derived data).
- **Pair-secret rotation invalidates prior contacted hashes.** Contacted
  digests are HMACs under a pair secret; rotating a secret mid-lead means
  earlier reports for that pair can no longer be re-keyed or matched
  (deployment concern mirroring SPEC §9). Rotate between leads, or accept
  that the affected lead's dedup history restarts.

## Delivery retry and dead-letter behaviour

Shipped semantics an operator must know when a buyer webhook is unhealthy
(recorded here because the delivered-post acknowledgement deadline remains a
design item — issue #193 — and the lead status does not encode delivery
failure):

- Each delivery attempt uses the offer's `webhook_timeout_seconds` (default
  **10 s**) as the HTTP read timeout. A timed-out or non-2xx response is a
  failed attempt.
- Failures move the delivery row `PENDING`/`RETRY` → `FAILED` and, after the
  retry budget is exhausted, into `dead_letter_jobs` with status `OPEN` and a
  `last_error`. `GET /v1/lcp/metrics` reports `delivery.{pending,retry,failed,
  dead_letter}` and queue age.
- A post whose receiver **never acknowledges** it expires the lead after the
  post response window (`LCP_POST_RESPONSE_TIMEOUT_SECONDS`, default 30 s per
  SPEC §9). The sweep marks the delivery `FAILED`, records a dead-letter row
  (`Post response timeout`), writes a `lead.post_response_timeout` audit event,
  and moves the lead to `EXPIRED`. An **acknowledged** post (2xx) is never
  expired by this window — it is the response that establishes the delivery and
  its payable. The platform does not re-offer an expired post (the award already
  consumed its slot); route the lead again deliberately if the buyer may still
  take it.
- Operator commands (audited, `platform:admin`):
  `lcp-platform-admin dead-letter list --status OPEN`,
  `dead-letter replay --job-id <id>`, `dead-letter quarantine --job-id <id>`.
  `list` prints lead IDs and error strings — keep it out of tickets and chat.
- Buyer-side deadlines: the buyer should acknowledge within the delivery
  window its agreement defines; the platform does not currently re-offer or
  expire a posted lead on a buyer's silence (design deferred to v1.2, #193).
  Treat a silent buyer as an operational escalation, not as a state the
  platform will resolve.

## Deployment procedure

1. Review schema/spec/changelog changes.
2. Run conformance, SDK/platform, real-Postgres integration, security, build,
   SBOM, dependency, and image scans.
3. Apply database migrations under change control.
4. Deploy API canaries.
5. Verify readiness and synthetic test traffic.
6. Deploy workers.
7. Monitor queue age and error rate.
8. Roll back application code without rolling back accepted lead data.

## Specification site

`site/` is the static specification site. It is generated from the normative
sources — `SPEC.md`, `spec/*.md`, `schemas/*.json`, `verticals/*.json`, and
`governance/LEPs/*.md` — and is never hand-edited. Every page carries its
source path and the build hash of the whole source set.

1. Edit the normative source, not the HTML under `site/`.
2. Regenerate: `python3 tools/build_spec_site.py`.
3. Check the committed output is current:
   `python3 tools/build_spec_site.py --check`. It exits non-zero when a page is
   stale, missing, or not generated by the tool, and when an internal link or
   heading anchor does not resolve in the generated output.
4. Commit `site/` together with the source change; `tools/tests` fails on a
   stale tree.
5. Publishing: the `Specification site` workflow runs on a `v*` tag or a manual
   dispatch, rebuilds the site, and uploads `site/` as the `lcp-spec-site`
   artifact. That workflow holds no deploy credentials — serving the artifact
   is a separate, reviewed act.

Rendering is dependency-free, so the converter covers the Markdown subset the
specification uses: ATX headings, paragraphs, nested ordered/unordered lists,
blockquotes, tables, fenced code, inline code, bold/italic, and links. There is
no setext-heading, footnote, raw-HTML, or four-space indented code support.
Links to repository files outside the published source set are rendered as text
with their repository path, because the site publishes the normative documents
only.

## Incident procedure

1. Declare severity and assign an incident owner.
2. Preserve relevant audit logs and delivery records.
3. Disable affected credentials or offers if needed.
4. Stop outbound delivery if PII exposure is suspected.
5. Determine affected tenants, lead IDs, recipients, timestamps, and regions.
6. Rotate exposed credentials and encryption keys.
7. Quarantine exhausted work before investigating it, then replay only after
   the failure cause is understood:

   ```bash
   lcp-platform-admin dead-letter list --status OPEN
   lcp-platform-admin dead-letter quarantine --job-id <job-id>
   lcp-platform-admin dead-letter replay --job-id <job-id>
   ```

   `list` dumps the full dead-letter rows as JSON, including lead IDs
   (`lead_id`) and delivery error strings (`last_error`) from the
   `dead_letter_jobs` table. The rows carry no raw envelope bodies, but lead
   IDs are still production identifiers: run the command from an
   operator-controlled shell and treat its output like incident data — never
   paste it into tickets, chat, or screenshots.

   Replay resets only the durable queue row; it never reconstructs or prints a
   consumer payload. Preserve the job ID and redacted error in the incident
   record.
8. Contain, remediate, and validate with replayed synthetic fixtures.
9. Follow applicable breach-notification and contractual procedures.
10. Produce a post-incident report and update the threat model.

Do not put raw consumer payloads into incident tickets or chat systems.

## Audit trail

- Every lifecycle status change is recorded in `audit_events` with the actor,
  before/after status, event name, and request ID: `lead.accepted`,
  `lead.rejected`, `lead.disputed`, `lead.refunded`, `lead.converted`,
  `lead.archived`, and `lead.expired` for event-driven transitions.
- Intake and routing record `lead.routed` (ping/post counts) per accepted
  lead; attachment authorization, erasure, and dead-letter operations write
  their own action names.
- Audit rows never contain consumer payloads or secrets. Trace from a request
  ID without PII; the original envelopes live only in the encrypted store.

## Agent attestation rollout (observe → enforce)

LEP-0008 verification runs at the intake slot for every agent-origin lead
(`provenance.source_type = "agent"`). Mode is `LCP_ATTESTATION_MODE`:
`observe` (verify + audit + accept) or `enforce` (reject on failure). The
default at ship is `enforce`; a deployment migrating live traffic starts in
`observe`.

- **Phase 2 — observe.** Zero new rejections on the wire; every §5 outcome
  is recorded as an `attestation.accept` / `attestation.observe` audit row
  with the frozen metadata `{outcome, reason, iss_host, kid,
  tok_sha256_16}`. Read the reason mix per sender: exit when the failures
  are dominated by `format`/`algorithm` (junk, not honest senders
  mid-migration) and `expired`/`signature` sit at noise levels for an agreed
  interval (suggested review point: 2 weeks). Senders get their exact failure
  axis from their own leads; issue each a remediation ticket from the audit
  rows.
- **Phase 3 — enforce.** Flip the flag; rejections follow the §8 mapping:
  token shape (free text, non-compact JWS, oversized, padded) → `400
  LCP-100`; verification failures → `401 LCP-002` with
  `details.reason ∈ {format, algorithm, issuer_policy, key_unknown,
  signature, expired, audience, claims_mismatch}`; issuer *availability*
  failure only (DNS/connect/timeout, issuer 5xx) → `500 LCP-500`
  `key_unavailable`, which is retryable under the normal idempotency rules.
  Enforce mode never fails open; observe mode is the only sanctioned soft
  path, and a deployment running observe must not advertise L3 (§9).
- **Alerting.** Dashboard and alert on the `key_unavailable` rate — an
  issuer outage is now a *visible* failure (that is the point of `LCP-500`
  vs the silent accept it replaces). Fetched-but-invalid JWKS documents
  (size/key-count bounds, non-JSON, duplicate `kid`, issuer 4xx) are `401
  key_unknown` — an issuer-configuration signal, not an outage signal; do
  not page on them.
- **Egress.** Every JWKS fetch runs the paired delivery-path controls
  (`validate_webhook_url` + resolve-aware `validate_egress_host`): HTTPS
  only, no embedded credentials, no private/loopback/link-local
  destinations post-resolution, no redirect following, no proxy/env
  interception — in **every** mode. `LCP_ALLOW_INSECURE_WEBHOOKS` (sandbox)
  does not relax any of this for attestation resolution. The `iss` URL must
  resolve to a routable host the platform may reach; a hostname allowlist
  can be enforced through `LCP_WEBHOOK_HOST_ALLOWLIST`, which the same guard
  consults. Fetch timeout is capped at 2 s; the success cache honours
  `cache-control`/`max-age` under `LCP_ATTESTATION_JWKS_CACHE_TTL_SECONDS`
  (hard cap 24 h), key rotation propagates on that TTL.
- **Skew.** `LCP_ATTESTATION_CLOCK_SKEW_SECONDS` accepts 0–30 only (a
  config-time refusal outside that range is required behavior). Clocks
  drifting past that are an NTP incident, not a knob.
- **Privacy.** Audit rows, metrics, and logs never carry token bytes —
  correlate incidents with `tok_sha256_16` (SHA-256 over the full compact
  token, first 16 hex chars). Do not paste attestations into tickets.
- **Rollback.** Phase 3 → 2 is the flag: accept behavior returns to
  today's wire while verification keeps recording. The §2 schema-pattern
  tightening (schemas + regenerated bundles) lands separately from this
  module; until it does, shape rejection happens at the verifier gate and
  rolls back with the same mode flag.
- **Advertisement.** Until this PR's verification is proven green on `main`,
  the platform keeps advertising `conformance_level: "L2"` (Phase 0); the
  L3 flip is a separate, deliberate change (V12 pin test).

## Batch submission (LEP-0001 / W2)

`POST /v1/lcp/leads/batch` accepts up to **100 lead envelopes** in one
request of at most **1 MiB**, and answers `207` with one per-item receipt
(`accepted`, `duplicate`, `rejected`, `error`) for every item, in input
order. It is a loop over the single-item intake path: each item is
validated, authorized, throttled, and deduped exactly like a `POST
/v1/lcp/leads`, there is no cross-item transaction, and a rejected item never
rolls back its siblings. Replaying an item key returns that item's original
receipt.

- **Flag.** `LCP_FEATURE_BATCH` (default **off**). Off means no
  `features.batch` entry in `GET /v1/lcp/capabilities` and a `404` from the
  route — the surface is invisible rather than half-enforced. Enabling it is
  an operator decision, made with the adoption note in
  [DECISIONS-v1.2.md](DECISIONS-v1.2.md) in hand.
- **Whole-request refusals.** Authentication, a malformed wrapper, more than
  100 items, or a body above 1 MiB fail the request (`413` for either cap) and
  nothing is persisted. Everything else is per item.
- **Throttling.** Every item consumes one sender token, so a batch cannot
  outrun `LCP_RATE_LIMIT_PER_MINUTE`; a throttled item is a `rejected` receipt
  (`LCP-011`) and the rest of the batch still runs.
- **Audit.** One `lead.*` audit row per processed item (`lead.routed`,
  `lead.duplicate`, `lead.rejected`) sharing the request id, so a batch is
  reconstructible from the trail without storing the batch body.
- **Clients.** Retry only the items whose receipt is `rejected`/`error`;
  re-sending the whole batch is safe (idempotent per item) but does needless
  work.

## Event subscriptions (LEP-0001 / W3)

`POST /v1/lcp/events/subscribe`, `GET /v1/lcp/events/subscriptions`,
`DELETE /v1/lcp/events/subscriptions/{id}`, and `GET /v1/lcp/events?cursor=`
add tenant-scoped push delivery and cursor replay. The surface ships off and
is an operator decision like batch.

- **Flag.** `LCP_FEATURE_EVENTS` (default **off**). Off means no
  `features.event_subscriptions` entry, no `subscription` message type in
  `GET /v1/lcp/capabilities`, and a `404` on all four routes — the surface is
  invisible rather than half-enforced.
- **Registration.** The request is an ordinary authenticated `subscription`
  envelope (`POST .../events/subscribe`). `event_types` is an explicit
  allowlist drawn from `capabilities().events`; an unadvertised name is
  refused (`400`, `details.reason: unadvertised_event_type`), and `*` is not
  a value. `callback_url` passes the conformance runner's SSRF rule
  (`tools/conformance_runner.py`): http/https only, no embedded credentials,
  a valid port, and only public resolved addresses — `http` and private
  addresses need `LCP_ALLOW_INSECURE_WEBHOOKS` (sandbox only). The response is
  `201` with the subscription view plus `secret`; that per-subscription HMAC
  secret is returned **once** and never appears in list/read responses.
- **Tenant isolation.** A subscription is bound to the authenticated
  sender's tenant. Listing filters by tenant; disabling another tenant's
  subscription is `403`; fan-out and every delivery re-check that the
  subscription's tenant matches the event's tenant.
- **Filters.** Closed key set: `event_type`, `message_type`, `vertical`,
  `status`, `lead_id`. `consumer.*`, `provenance.*`, `attributes.*`,
  `details.*`, `contact.*`, `security.*`, and any other key are refused with
  `400` and `details.field` naming the offending path
  (`details.reason: filter_field_pii` for a consumer/PII path).
- **Delivery.** A background worker (`process_once`) fans a subscription's
  new stream events out to its delivery queue and POSTs them; nothing is sent
  inline with the request that produced the event. Bodies are the stored
  `event` envelope bytes. Headers are `X-LCP-Signature`, `X-LCP-Timestamp`,
  `X-LCP-Idempotency-Key`, `X-LCP-Event-Id`, `X-LCP-Subscription-Id`, and
  `X-LCP-Sender-Id`. The signature is lowercase hex HMAC-SHA256 over:
  `<X-LCP-Timestamp>\n<X-LCP-Event-Id>\n<exact request body bytes>` — the
  LEP-0002 canonical input with the stable `event_id` as the idempotency
  term. Verify the digest over the received bytes, never a reformatted body.
- **Retry/backoff and DLQ.** `LCP_MAX_DELIVERY_ATTEMPTS` (default **5**) is
  the budget. Failed attempts are `RETRY` with `next_attempt_at` set from the
  shipped ladder `1, 5, 30, 120, 600` seconds (the last step repeats); an
  attempt that exhausts the budget is `FAILED` and writes a
  `dead_letter_jobs` row with `queue_type = event_subscription`,
  `resource_id = <delivery_id>`, and a bounded `last_error` (subscriber
  response bodies are never stored). Inspect with
  `lcp-platform-admin dead-letter list --status OPEN`; replay/quarantine with
  `dead-letter replay|quarantine --job-id <id>` (the replay path moves the
  `event_deliveries` row back to `RETRY`).
- **Duplicates are legal.** Delivery is at-least-once; a worker restart or a
  replay can deliver the same `X-LCP-Event-Id` twice. Consumers MUST dedupe on
  that header (or `lcp.message.id`) and must not rely on ordering.
- **Cursor and retention.** `GET /v1/lcp/events?cursor=<opaque>&limit=<1-500>`
  returns `{events, next_cursor, has_more}` for the caller's tenant. Cursors
  are the tenant's monotonic stream sequence, so a replay from the same cursor
  reproduces the same event set and no same-second insertion can fall behind
  an advanced cursor. `replay_from` on registration seeds a subscription at a
  cursor. `LCP_EVENT_CURSOR_TTL_SECONDS` (default **86400**, 24 h) bounds both
  the stream and cursors: the worker purges older stream rows, and an older
  cursor returns `400` with `details.reason: cursor_expired`. A cursor from
  another tenant names no event in the caller's stream and is refused as
  expired/unknown — sequences are per tenant, so no cross-tenant event can be
  read. Replaying deeper than retention is not possible by design — export or
  reconcile before the TTL when a subscriber has been down for longer than a
  day.
- **Scope.** Registration/read/disable need `event:subscribe`; cursor reads
  need `event:read`. `platform:admin` satisfies both.

## Conformance runner (D11)

`tools/conformance_runner.py` is the self-hostable conformance runner from
[DECISIONS-v1.2.md](DECISIONS-v1.2.md) D11: a WSGI service, standard library
only, that a publisher or buyer can run against its own deployment (or point
at a peer) and get a portable, digest-verified report.

- **Run it.** `python3 tools/conformance_runner.py --port 8099`. It binds
  loopback by default; add `--host 0.0.0.0` only behind an authenticated,
  TLS-terminating edge you control. `--timeout` bounds each target request,
  and `--no-schema-checks` skips the repository vector suite.
- **Endpoints.** `GET /healthz` answers `{"status": "ok", "service":
  "lcp-conformance-runner"}`. `POST /run` takes
  `{"base_url": ..., "auth_method": "bearer"|"hmac"|"none", "api_key": ...,
  "sender_id": ...}` and returns the report. `sender_id` is optional; it is
  required only for the `hmac` probe, which needs a caller identity.
- **What a run checks.** `transport.*` entries probe the live peer: the
  `GET /v1/lcp/capabilities` advertisement (§8) including `lcp_versions`,
  `conformance_level` and `auth_methods`; that an unauthenticated read of
  `/v1/lcp/history/requests` is rejected; and that the supplied credential is
  accepted on a read-only `GET /v1/lcp/offers`. `vector.*` entries are the
  repository's own suite (`test-vectors/conformance.py`) executed in a child
  process, so schema-level conformance is measured against the vectors rather
  than a reimplementation. The child needs `jsonschema` + `referencing`;
  set `LCP_CONFORMANCE_PYTHON` to choose its interpreter, otherwise
  `.venv/bin/python` is preferred and a missing dependency is reported as a
  `skipped` check, never as a pass.
- **Report.** `{report_id, generated_at, target, lcp_versions, checks,
  summary, digest}`. `digest` is the SHA-256 of the canonical body (the
  report minus `digest`, sorted keys, no whitespace), so a recipient can
  re-hash the body and detect any edit. Keep the report file; it is the
  evidence, and the runner is stateless.
- **Security model.** Only `http`/`https` targets are accepted and
  credentials embedded in the URL are refused. Loopback, link-local
  (including `169.254.169.254`), private, unspecified, multicast and reserved
  addresses are refused — including when a public-looking name resolves to
  one — unless the operator passes `--allow-private`, which exists for the
  local self-test. The hostname is resolved exactly once, every answer is
  policy-checked, and the socket is pinned to that address, so a second DNS
  answer (rebind) cannot redirect the request; TLS still verifies the
  advertised hostname. Responses are capped at 1 MiB and by wall clock,
  redirects are not followed, the request body is capped at 8 KiB, and the
  API key is used in a header only — it never reaches the report, the logs,
  or the in-memory report index, which stores just `{digest, summary}` with
  no target URL. Check details are redacted before publication.
- **Self-test.** `python3 tools/conformance_runner.py --self-test` serves a
  local stub peer, runs the full report against it (including the vector
  suite), prints it, and exits non-zero only if the runner's own transport
  probes fail — it proves the service, not the repository's vectors.
- **No certification claim.** A report says "this target answered these
  checks on this date". It is not a certification, an endorsement, or a
  trademark licence, and a hosted instance may not be advertised as one.
  D11 records the condition for revisiting that: a second independent
  operator running the same runner and publishing a matching report.

## Backup and restore

- Encrypt backups with a separate key hierarchy.
- Test restore at least quarterly and after backup-system changes.
- Validate restored schema/version and delivery-queue continuity.
- Verify retention and deletion obligations apply to backups.
- Record measured RPO/RTO rather than relying on assumptions.

---

**Previous:** [Production deployment](DEPLOYMENT.md) · **Next:** [Security architecture](SECURITY-ARCHITECTURE.md)
