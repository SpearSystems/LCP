# Vertical schemas

> **Reference page** · [Docs index](README.md)

The core is vertical-agnostic (SPEC §3): the envelope and the canonical core
carry **zero** vertical-specific fields. A vertical is a typed extension block —
`vertical` plus `schema_version` select a JSON Schema under
[`verticals/`](../verticals/), and only fields that schema tags `ping_safe: true`
may appear in `attributes` (SPEC §3 `attributes`, §6).

Six vertical schemas ship in this repository. They are examples of the
extension mechanism, not capabilities of the protocol — a platform may ship
none of them and still conform.

| Vertical id | Covers |
|---|---|
| `mortgage` | Loan purpose and product, property and value, credit and employment, country-scoped product enums |
| `insurance` | Product and coverage, deductible and claim history, driver/vehicle bands |
| `solar` | System size and roof, ownership and utility, bill band, storage interest |
| `legal` | Case type, incident timing and state, representation, injury and fault bands |
| `home_services` | Controlled service/subcategory taxonomy, project and urgency, property and budget bands |
| `mva` | Motor-vehicle-accident bands; documents stay in attachments or the source system |

## What a vertical may and may not contain

- Every field is tagged `ping_safe: true` or `ping_safe: false`; a ping that
  carries a `false` field is rejected by the conformance runner.
- A vertical MUST NOT redefine core field names (`phone`, `email`, `name`,
  `country_code`, …) inside `attributes` (SPEC §12 reserved fields).
- Country-specific data is a country-scoped object with a required
  `country_code` inside the vertical schema — never a per-country vertical
  file (SPEC §3).
- Marketplace-specific fields belong in the `extensions` mechanism
  ([LEP-0006](../governance/LEPs/LEP-0006-marketplace-migration-extensions.md)),
  not in a vertical schema.
- Documents, free text, and evidentiary detail belong in
  [secure attachments](ATTACHMENTS.md) as metadata, or in the source system —
  never in `attributes`.

## Example: motor-vehicle-accident (`mva`)

The `mva` schema uses controlled qualification bands for pings and keeps exact
dates, free text, medical detail, contracts, and evidence in the full post or
the protected source system.

The schema covers:

- accident type and age band;
- injury presence and severity band;
- medical-treatment and emergency-service indicators;
- police-report and insurance indicators;
- fault position and representation status;
- vehicle/passenger bands;
- claim stage; and
- `evidence_available` presence indicators.

The actual documents are never placed in `attributes` or `ping.attributes`.
They are uploaded separately as described in [secure attachments](ATTACHMENTS.md)
and referenced by attachment metadata only.

## Adding a vertical

1. Add `verticals/<id>.json` with `vertical`, `schema_version`, and the
   `ping_safe` tag on every property.
2. Add the id to the conformance corpus and the coverage matrix row for the
   clause it satisfies.
3. Regenerate the SDK schema bundles (`tools/generate_sdk_models.py --write`)
   so every SDK sees the same schema set.
