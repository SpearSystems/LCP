# LEP-0016 — `mortgage` vertical 2.0.0: whole-unit applicant amounts, a declared currency, and side-by-side vertical versions

- **Title:** `mortgage` vertical 2.0.0: whole-unit applicant amounts with a declared `amount_currency`, bounded values, and a rule for receivers that serve two versions of one vertical at once
- **Author(s):** LCP maintainers
- **Date:** 2026-11-19
- **Status:** **Implemented** — shipped in v1.4.0 (PR
  [#251](https://github.com/SpearSystems/LCP/pull/251), released
  2026-11-19); Accepted 2026-11-19 (maintainer;
  [v1.4 decision record](../../docs/DECISIONS-v1.4.md) D1–D4: review
  window and independent review waived).
- **Target version:** v1.4.0. The vertical file gains a `2.0.0` schema
  alongside a frozen `1.0.0`.
- **Discussion:** [#258](https://github.com/SpearSystems/LCP/issues/258)
- **Depends on:** [LEP-0014](LEP-0014-personal-loans-vertical.md), for the
  whole-unit authoring rule. If LEP-0014 is rejected, this record is
  withdrawn with it.

## Decision (maintainer, 2026-11-19)

**Accepted** under [D1–D4](../../docs/DECISIONS-v1.4.md) of the v1.4 decision
record. The review window closed early with no comments, and no reviewer
independent of the author took part (D1, D2). No PII class changes, so no
security/privacy condition applies beyond the v1.3 record.

Conditions:

- `mortgage` 1.0.0 stays served side by side for the 180-day window, frozen
  at `verticals/archive/mortgage-1.0.0.json`.
- The SDKs carry both versions in their schema bundles and validate by
  `attributes.schema_version`. No SDK has typed per-vertical builders, so
  "1.0.0 builder deprecated" is carried as `deprecated: true` on the
  archived schema (D4 scope notes).
- Any comment with new evidence on
  [#258](https://github.com/SpearSystems/LCP/issues/258) reopens this record.
  Re-evaluation: 2027-05-19.

## Problem statement and evidence

**Mortgage amounts have no currency.** `verticals/mortgage.json` 1.0.0
declares four applicant-declared amounts:

- `loan_amount_cents`
- `property_value_cents`
- `annual_income_cents`
- `cash_out_amount_cents`

They are minor units of a currency the schema never names, so a GBP or AUD
mortgage lead is indistinguishable from a USD one.

**They have no upper bound.** Each is `integer` with `minimum: 0` only. A
value multiplied by 100 twice, the defect LEP-0014 records for
`personal_loans`, is accepted without complaint.

**They are typed by applicants in whole units.** These are form answers,
not platform-priced money. LEP-0014 sets the authoring rule:

- applicant-declared amounts are whole units of a declared
  `amount_currency`;
- machine-produced money stays integer `_cents`.

Once LEP-0014 lands, mortgage is the only vertical that breaks that rule.
No-code form tools that cannot multiply a field (LEP-0014, evidence) are
used for mortgage forms too.

**Receivers cannot serve two versions of a vertical.** Capabilities already
advertise `verticals[{id, schema_version}]` (spec/05 §capabilities), so a
receiver can list `mortgage` 1.0.0 and 2.0.0. The spec says nothing about
validating both. The reference platform keys vertical validators by `id`
alone, so a breaking vertical revision has no migration window. That gap
also applies to every future vertical major.

## Scope

1. **`mortgage` 2.0.0:**
   - `amount_currency` (ISO 4217);
   - `loan_amount`, `property_value`, `annual_income` and `cash_out_amount`
     in whole units, each bounded;
   - all other fields unchanged.
2. **Frozen 1.0.0.** The current file moves to
   `verticals/archive/mortgage-1.0.0.json` unchanged. `verticals/mortgage.json`
   becomes 2.0.0.
3. **Side-by-side rule (normative):**
   - A receiver that advertises two versions of one vertical MUST validate
     each lead against the version its `attributes.schema_version` names.
   - It MUST refuse a version it does not advertise, with `LCP-100`,
     `field` = `attributes.schema_version`.
   - Pings validate the same way.
4. **Deprecation window:** a receiver keeps 1.0.0 for at least 180 days
   after it first advertises 2.0.0.

**Not in scope:**

- other verticals, which stay on their current versions;
- core money fields;
- converting the band enumerations;
- moving `property_address`, which is the property and may differ from the
  consumer's `location`.

## Proposed wire shape

Differences from 1.0.0:

| 1.0.0 | 2.0.0 | Why |
|---|---|---|
| — | `amount_currency` (string, ISO 4217, `^[A-Z]{3}$`), required when any amount is present (`dependentRequired`) | Amounts finally have a currency. |
| `loan_amount_cents` (integer, `minimum: 0`) | `loan_amount` (integer, `1000`–`100000000`) | Whole units, bounded. |
| `property_value_cents` (integer, `minimum: 0`) | `property_value` (integer, `1000`–`1000000000`) | Same reason. |
| `annual_income_cents` (integer, `minimum: 0`) | `annual_income` (integer, `0`–`100000000`) | Same reason. |
| `cash_out_amount_cents` (integer, `minimum: 0`) | `cash_out_amount` (integer, `0`–`100000000`) | Same reason. |
| `schema_version` const `1.0.0` | const `2.0.0` | Breaking for 1.0.0 senders. |

**Unchanged:**

- every other field, its `x-lcp-pii` class and its `ping_safe` flag;
- the amounts stay `restricted` and `ping_safe: false`;
- the platform-derived bands (`loan_amount_band` and the others) remain
  the only amount data a ping may carry. They are read in units of
  `amount_currency`.

#### Valid 1 — minimal lead

```json
{ "vertical": "mortgage", "schema_version": "2.0.0" }
```

#### Valid 2 — refinance with amounts

```json
{
  "vertical": "mortgage", "schema_version": "2.0.0", "amount_currency": "USD",
  "loan_type": "refinance", "loan_amount": 350000, "property_value": 500000,
  "annual_income": 120000, "cash_out_amount": 25000
}
```

#### Invalid A — amount without currency

`"loan_amount": 350000` with no `amount_currency` is refused with
`LCP-100`, `field` = `attributes.amount_currency`.

#### Invalid B — cents sent by mistake

`"loan_amount": 35000000000` (meant as $350,000 in cents) is refused,
because it is above `100000000`.

**Known limit:** cents for loans of $1,000,000 or less pass the bound.
Bands and buyer requirement profiles surface those as out-of-pattern.

#### Invalid C — 1.0.0 field on 2.0.0

`"loan_amount_cents": 35000000` with `schema_version` `2.0.0` is refused,
because `additionalProperties` is `false`.

#### Invalid D — unadvertised version

`"schema_version": "1.0.0"` sent to a receiver that advertises only 2.0.0
is refused with `LCP-100`, `field` = `attributes.schema_version`.

## Privacy, security, and universal-core audit

- **No class changes.** The amounts stay `restricted` and
  `ping_safe: false`. `amount_currency` is `public` and `ping_safe: true`,
  because a currency code identifies no one.
- **Universal core:** untouched. `amount_currency` is not named `currency`,
  because core owns that name (SPEC §12). This follows LEP-0014.
- **The side-by-side rule** narrows what a receiver accepts. It never
  widens it.

## Compatibility, versioning, and deprecation impact

- **For mortgage senders:** breaking, with a window of at least 180 days.
  Senders on 1.0.0 keep working at any receiver that still advertises it.
- **Normalisation is optional, and refused when the currency is unknown.**
  A receiver MAY normalise 1.0.0 at intake (divide by 100). It MUST NOT
  assume a currency for this. It uses the sender's configured market
  currency, and refuses the normalisation when none is configured.
- **SDKs** carry both model versions during the window. The 1.0.0 builder
  is marked deprecated.

## SDK, OpenAPI, MCP, conformance, and operational impact

- **Schemas and SDKs:** a `mortgage` 2.0.0 model in ten SDKs, with 1.0.0
  kept and deprecated. Bundles and manifests carry both versions.
- **Reference platform:** vertical validators keyed by
  `(id, schema_version)`, loading `verticals/archive/*.json`, and
  capabilities that list both entries.
- **Conformance:**
  - vectors for every Valid and Invalid example;
  - a side-by-side vector: the same receiver accepts 1.0.0 and 2.0.0, and
    refuses `9.9.9`;
  - ping vectors for both versions.
- **OpenAPI and MCP:** examples are regenerated on 2.0.0.
- **Metrics:** per-sender counts of mortgage 1.0.0 traffic, to drive the
  end of the window.

## Alternatives considered

- **Add `amount_currency` to 1.x as a minor change and keep cents.** It
  fixes the currency but not the unit rule or the bounds, and it leaves
  mortgage inconsistent with LEP-0014. Rejected.
- **Convert in place without a version window.** That breaks every
  shipped mortgage sender at once. Rejected.
- **Wait for a mortgage-specific defect** (LEP-0014's deferred trigger).
  The missing currency is a defect by itself, and the side-by-side rule is
  needed before any vertical major can ship safely. Rejected.

## Rollout, migration, observability, and rollback

1. **LCP:** the side-by-side rule and the reference platform's versioned
   validators land first. That work is useful on its own.
2. **Next:**
   - the 2.0.0 file lands;
   - 1.0.0 moves to `verticals/archive/`;
   - capabilities advertise both versions.
3. **Senders migrate** in one edit: rename four fields, drop the ×100
   conversion, add `amount_currency` and set `schema_version` to `2.0.0`.
4. **End of window:** a receiver stops advertising 1.0.0 once its
   per-sender counts reach zero, or after 180 days with notice.
5. **Rollback:** a receiver keeps or restores the 1.0.0 advertisement. The
   archive file never changes.

## Deferred questions

- **Bound values.** `100000000` whole units suits USD, GBP and AUD markets
  but not high-denomination currencies such as JPY or KRW. Trigger: a
  receiver advertising mortgage in such a currency. The likely answer is
  bounds expressed per currency in the requirement profile.
- **Band labels per currency.** The band enums read as US dollars
  (`under_100k`). Trigger: non-USD mortgage traffic.
- **A general archive policy for vertical majors.** How long frozen files
  stay in the repository. Trigger: the third archived vertical version.
