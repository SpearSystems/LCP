# LCP Operations Runbook

## Health and readiness

Use the reference health endpoints through the deployment layer:

- `GET /health/live`: process liveness only.
- `GET /health/ready`: database connectivity/readiness.
- Worker health: last successful poll, queue age, and lease expiry health from
  metrics or a supervisor-specific health check.

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

Alert on sustained auth failures, PII validation failures, queue growth,
failed deliveries, database saturation, backup failures, and unusual volume
from a tenant.

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
7. Contain, remediate, and validate with replayed synthetic fixtures.
8. Follow applicable breach-notification and contractual procedures.
9. Produce a post-incident report and update the threat model.

Do not put raw consumer payloads into incident tickets or chat systems.

## Backup and restore

- Encrypt backups with a separate key hierarchy.
- Test restore at least quarterly and after backup-system changes.
- Validate restored schema/version and delivery-queue continuity.
- Verify retention and deletion obligations apply to backups.
- Record measured RPO/RTO rather than relying on assumptions.
