# Monthly payable quotas

> **Operations page · Page 4A**
>
> **Previous:** [MVA and attachments](MVA-ATTACHMENTS.md) · **Next:** [Operations runbook](OPERATIONS.md)

A buyer can configure a monthly payable target on each offer. This is useful
when one buyer has separate MVA-call, home-services-call, and mortgage-lead
offers and expects, for example, 300 payable outcomes from each.

## One offer per commercial stream

Use separate offer IDs even when the buyer is the same:

```text
buyer_001 / buyer-mva-call
buyer_001 / buyer-home-services-call
buyer_001 / buyer-mortgage-leads
```

This keeps vertical, channel, price, payable rules, dispute counts, and quota
progress independently auditable.

## Offer fields

```json
{
  "monthly_minimum_payable": 300,
  "monthly_quota_timezone": "Australia/Sydney",
  "monthly_quota_policy": "pace",
  "payable_rules": {
    "mode": "call_outcome",
    "require_call_answered": true,
    "minimum_call_seconds": 30
  }
}
```

The policies are:

- `monitor` — report progress only;
- `pace` — expose the remaining target and daily pace so routing and
  operations can prioritize the under-target offer; and
- `hard_cap` — enforce `monthly_maximum_payable` as a safety ceiling.

`monthly_minimum_payable` is a target, not a claim that supply exists. A
platform cannot guarantee 300 leads when the configured publishers do not
produce enough eligible traffic. A contractual guarantee belongs in the
buyer/platform/publisher agreement and its SLA.

## Payable, not merely delivered

The quota counts `payable` records, not raw intake, pings, or webhook attempts.
The platform stores:

- `pending` — delivered but awaiting a call outcome or validation window;
- `payable` — meets the configured payable rules;
- `not_payable` — failed the agreed criteria;
- `disputed` — under review; and
- `refunded` — dispute resolved in the buyer's/publisher's favor as agreed.

For calls, a `CALL_OUTCOME` event supplies answer status, duration, disposition,
and transfer status. The platform evaluates the structured `payable_rules`
and records the reason, duration, price, currency, and month key.

## Reporting

The reference platform exposes a quota report at:

```text
GET /v1/lcp/offers/{offer_id}/quota
```

The report contains the calendar month, target, remaining target, required
per-day pace, under-paced indicator, policy, and status counts. Operators
should export this data to their metrics and buyer reporting system, with
separate dimensions for publisher, brand, flow, country, channel, and
vertical.

A useful buyer dashboard is:

```text
Offer                       Target  Payable  Pending  Disputed  Remaining
buyer-mva-call                  300       286       11         3         14
buyer-home-services-call        300       319        4         1          0
buyer-mortgage-leads             300       244       28         5         56
```

## Pacing behavior

A pacing service should use the report to adjust only agreed operational
controls, such as:

- enabling additional eligible publisher flows;
- adjusting buyer bid strategy within price guardrails;
- increasing or decreasing a buyer's hourly/day capacity;
- prioritizing under-target offers when multiple offers are otherwise tied;
- alerting when consent, geography, or quality restrictions make the target
  unreachable; and
- escalating to the commercial owner before changing a payable definition.

It must not bypass consent, source allowlists, DNC rules, geography, or
vertical eligibility merely to hit a number.

**Next:** [Operations runbook](OPERATIONS.md)
