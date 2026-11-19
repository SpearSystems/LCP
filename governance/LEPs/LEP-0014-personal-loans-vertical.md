# LEP-0014 — `personal_loans` vertical 2.0.0: whole-unit applicant amounts and a regulated identity container

- **Title:** `personal_loans` vertical 2.0.0: an interoperable personal-loan vertical with whole-unit applicant amounts and an LEP-0013 identity container
- **Author(s):** LCP maintainers (draft from SpearPointX adopter evidence)
- **Date:** 2026-11-19
- **Status:** **Implemented** — shipped in v1.3.0 (PR
  [#249](https://github.com/SpearSystems/LCP/pull/249), released
  2026-11-19); Accepted 2026-11-19 (maintainer;
  [v1.3 decision record](../../docs/DECISIONS-v1.3.md) D1–D4: review
  window and independent review waived, maintainer security/privacy
  sign-off under D3).
- **Target version:** v1.3.0 (a new vertical file is additive. The file's
  own `schema_version` is `2.0.0`, so it can never be confused with the
  adopter's unpublished 1.0.0.)
- **Discussion:** [#256](https://github.com/SpearSystems/LCP/issues/256)
- **Schema:** [`verticals/personal_loans.json`](../../verticals/personal_loans.json).
- **Depends on:** [LEP-0013](LEP-0013-regulated-identifiers.md) for the
  `us_details` container. If LEP-0013 is rejected, this vertical ships
  without `us_details`. Every other field stands alone.

## Decision (maintainer, 2026-11-19)

**Accepted** under [D1–D4](../../docs/DECISIONS-v1.3.md) of the v1.3 decision
record. The review window closed early with no comments, and no reviewer
independent of the author took part (D1, D2). LEP-0013 is accepted in the
same record, so `us_details` ships with the vertical.

Any comment with new evidence on
[#256](https://github.com/SpearSystems/LCP/issues/256) reopens this record.
Re-evaluation: 2027-05-19.

## Problem statement and evidence

**No personal-loans vertical exists in LCP.** The shipped verticals are
`home_services`, `insurance`, `legal`, `mortgage`, `mva` and `solar`.
SpearPointX registered its own `personal_loans` 1.0.0 to onboard a
publisher, and published a schema URL under `lcp.dev` that LCP does not
serve.

**Unit conversion caused a real defect.** Version 1.0.0 carries
applicant-declared amounts as cents (`loan_amount_cents`,
`monthly_income_cents`). Applicants type whole dollars, so every
integration has to multiply by 100. In practice that failed in three ways:

- The adopter's own integration guide multiplied twice. A $1,000 request
  went out as $100,000, and the schema accepted it.
- No-code form tools (GoHighLevel, Heyflow) cannot multiply a field value.
  The guide had to send those publishers through a separate automation
  service just to add two zeros.
- The buyer API the adopter delivers to takes whole dollars. Its adapter
  divides by 100 again.

**The 1.0.0 identity container cannot validate.** Its `us_details` lists
`ssn`, `license_number` and `routing_number` as required but declares no
such properties (see LEP-0013, evidence item 2).

## Scope

- **Vertical (additive):** a new `verticals/personal_loans.json` at
  `schema_version` `2.0.0`, as proposed in the linked schema.
- **Vertical-authoring rule** (documentation, `docs/VERTICALS.md` or
  equivalent): applicant-declared amounts may be whole units of a declared
  vertical attribute such as `amount_currency`, named without a `_cents`
  suffix and bounded. The attribute is not named `currency`: core schemas
  own that name (SPEC §12, `tools/check_universal_core.py`).
  Platform-priced money stays integer minor units with `_cents`.
- **Not in scope:**
  - changing existing verticals (`mortgage.loan_amount_cents` and others);
  - core money fields;
  - non-US personal loans.

## Proposed wire shape

The full schema is in the linked file. Differences from the adopter's 1.0.0:

| 1.0.0 | 2.0.0 | Why |
|---|---|---|
| `loan_amount_cents` (integer, multiple of 100) | `loan_amount` (integer, `100`–`50000`, whole units) | The form value passes straight through, with no conversion step. The bound rejects most wrong-unit values. |
| `monthly_income_cents` (integer, multiple of 100) | `monthly_income` (integer, `0`–`50000`, whole units) | Same reason. |
| `account_number` at top level | `us_details.account_number`, regulated | Account and routing numbers together are LEP-0013 `bank_account` identifiers and sit in one container. |
| `us_details` classed `contact`; `ssn`, `license_number`, `routing_number` required but undeclared | `us_details` classed `restricted`; all five identifiers declared with patterns and `x-lcp-regulated`; only `country_code` required | Fixes the unvalidatable container and aligns it with LEP-0013. |
| 20 required fields | 4 required: `vertical`, `schema_version`, `amount_currency`, `loan_amount` | What a given buyer needs belongs in its requirement profile (`regulated_fields` and the existing predicates), not in the interoperable schema. |
| `street_address` in `attributes` | core `location.street_address` | SPEC §3: a vertical MUST NOT redefine a core field name (`tools/check_reserved_fields.py`). |
| `schema_version` `1.0.0` | `2.0.0` | A breaking change for 1.0.0 senders. |

**Unchanged:**

- every other field, its class and its `ping_safe` flag;
- the platform-derived bands (`loan_amount_band`, `monthly_income_band`),
  which remain the only amount data a ping may carry.

**The rule a publisher reads.** Type what the applicant typed. `1000` means
$1,000. Prices and payouts the platform reports back stay in cents
(`payout_cents`, LEP-0012), because machines produce those.

#### Valid 1 — minimal lead

```json
{ "vertical": "personal_loans", "schema_version": "2.0.0", "amount_currency": "USD", "loan_amount": 1000 }
```

#### Valid 2 — full lead with regulated identifiers

See [LEP-0013 Valid 1](LEP-0013-regulated-identifiers.md#valid-1--enrolled-sender-test-lead-v130-shape-personal_loans-200).

#### Invalid A — cents sent by mistake

`"loan_amount": 100000` (meant as $1,000 in cents) is refused with
`LCP-100`, `field` = `attributes.loan_amount`, because it exceeds `50000`.

**Known limit:** cents for requests of $500 or less (`50000` and below) pass
the bound. Requirement profiles and the platform's band derivation surface
those as out-of-pattern requests. Bounds alone cannot close the gap.

#### Invalid B — float amount

`"loan_amount": 1000.5` is refused, because the type is integer.

#### Invalid C — 1.0.0 field name on 2.0.0

`"loan_amount_cents": 100000` with `schema_version` `2.0.0` is refused,
because `additionalProperties` is `false`.

## Privacy, security, and universal-core audit

- **Classes** are carried over from 1.0.0 field by field, with no
  downgrade. `us_details` moves from `contact` to `restricted`, which is
  more protective and matches LEP-0011's container rule: a container takes
  the maximum of its children.
- **Regulated identifiers** follow LEP-0013 in full. Without that record
  accepted, `us_details` is removed from this file.
- **Ping:** only `public`, `ping_safe: true` fields:
  - bands;
  - `loan_purpose`, `income_source`, `income_frequency`;
  - `direct_deposit`, `owns_vehicle`, `is_homeowner`.
- **Universal core:** untouched. Everything here is in `attributes`.

## Compatibility, versioning, and deprecation impact

- **LCP release:** additive (a new vertical file).
- **For the adopter's 1.0.0 senders:** breaking. The migration is below.
- **Precedent.** Whole-unit applicant amounts differ from `mortgage`'s
  `loan_amount_cents`. The vertical-authoring rule makes this an explicit,
  per-vertical choice. Converting other verticals is a deferred question,
  not part of this record.

## SDK, OpenAPI, MCP, conformance, and operational impact

- **Schemas and SDKs:** new vertical models in ten SDKs, with bundles and
  manifests regenerated. A `personal_loans` builder takes whole units and
  never multiplies.
- **Conformance:** `test-vectors/verticals/personal_loans.json` covers
  every Valid and Invalid example, ping eligibility per field, and the
  `us_details` gate vectors (LEP-0013).
- **OpenAPI and MCP:** vertical examples are regenerated with reserved test
  identifiers only.

## Alternatives considered

- **Keep cents and add bounds.** This catches the double conversion but
  keeps the multiply step that no-code publishers cannot do and that buyers
  undo. Rejected.
- **Decimal strings (`"1000.00"`).** These parse in every stack, but
  publishers still have to format them, and they add a third numeric form
  to the protocol. Rejected.
- **A `_dollars` suffix.** It is currency-specific, so a future GBP or AUD
  personal-loans market would carry a lie in the field name. Whole units of
  the declared `amount_currency` stay correct everywhere. Rejected.
- **Keep the 20 required fields.** That encodes one buyer's requirements as
  everyone's schema. Rejected, in favour of requirement profiles.

## Rollout, migration, observability, and rollback

1. **LCP:** the vertical lands with LEP-0013's gate. If LEP-0013 has not
   been accepted, it lands without `us_details`.
2. **Adopter migration (SpearPointX):**
   - accept `schema_version` `1.0.0` and `2.0.0` side by side for 90 days;
   - normalise 1.0.0 at intake (`loan_amount = loan_amount_cents / 100`);
   - log per-sender 1.0.0 counts;
   - remove 1.0.0 once the counts reach zero.
3. **Publisher change:** a publisher moves in one edit. Rename two fields,
   drop the ×100 conversion, move `account_number` into `us_details`, and
   set `schema_version` to `2.0.0`.
4. **Rollback:** the adopter keeps accepting 1.0.0. The LCP file is
   additive and can be withdrawn before release without affecting other
   verticals.

## Deferred questions

- **Whole-unit amounts in other verticals** (`mortgage.loan_amount_cents`,
  `property_value_cents`). Trigger: a no-code integration defect or support
  burden in another vertical, or a second adopter asking.
- **Bound values.** `50000` is set for short-term and mid-size installment
  loans. Trigger: a buyer with larger products, or refusal metrics showing
  legitimate requests above the bound.
- **Non-US personal loans.** Trigger: buyer evidence outside the US. That
  needs a country container and, for identifiers, its own LEP-0013
  successor.
