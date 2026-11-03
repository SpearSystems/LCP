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

## Backup and restore

- Encrypt backups with a separate key hierarchy.
- Test restore at least quarterly and after backup-system changes.
- Validate restored schema/version and delivery-queue continuity.
- Verify retention and deletion obligations apply to backups.
- Record measured RPO/RTO rather than relying on assumptions.

---

**Previous:** [Production deployment](DEPLOYMENT.md) · **Next:** [Security architecture](SECURITY-ARCHITECTURE.md)
