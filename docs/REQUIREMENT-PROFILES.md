# Versioned requirement profiles and service areas

> **Platform page · v1.0-compatible implementation extension**
>
> These profiles are deployment-scoped extensions. They do not add fields to
> the universal LCP core or create a new vertical for every buyer taxonomy.

## Why profiles exist

Real buyer requirements change more quickly than a protocol schema should.
One buyer may accept roofing and gutters this month, require a verified phone
next month, and change its service area or minimum project value the month
after that. Encoding every variation into `verticals/home_services.json` would
make the shared schema unstable and create unnecessary SDK releases.

The reference platform therefore supports two namespaced offer extensions:

- `lcp.platform.requirements` — versioned, safe predicates over allowlisted
  LCP fields.
- `lcp.platform.service_area` — a named, versioned allowlist of countries,
  regions, and exact postal codes.

A platform may store profiles in its own configuration registry and copy the
active profile's immutable identity and predicates into an offer. A profile
change should create a new version and an auditable offer update rather than
silently changing the meaning of an existing offer.

## Requirement profile shape

```json
{
  "extensions": {
    "lcp.platform.requirements": {
      "profile_id": "buyer-home-services-v1",
      "version": "2026-08-17",
      "predicates": [
        {
          "path": "attributes.service_type",
          "operator": "in",
          "values": ["roofing", "gutters"]
        },
        {
          "path": "attributes.project_value",
          "operator": "between",
          "min": 10000,
          "max": 50000
        },
        {
          "path": "channel",
          "operator": "equals",
          "value": "form"
        }
      ]
    }
  }
}
```

The reference implementation supports these operators:

| Operator | Value | Meaning |
|---|---|---|
| `equals` | `value` | Exact value equality. |
| `in` | non-empty `values` array | Value is in an allowlist. |
| `exists` | boolean `value` | Field is present or absent. |
| `between` | numeric `min` and `max` | Inclusive numeric range. |
| `prefix` | string `value` | String begins with the prefix. |

Allowed paths are deliberately limited to one field below `attributes`,
`location`, or `provenance`, plus `channel`. Arbitrary expressions, code, and
unregistered nested paths are not evaluated. Predicates are ANDed together;
invalid profiles fail closed with an explainable match reason.

## Named service-area shape

```json
{
  "extensions": {
    "lcp.platform.service_area": {
      "profile_id": "au-nsw-metro",
      "version": "2026-08-17",
      "countries": ["AU"],
      "state_regions": ["NSW"],
      "postal_codes": ["2000", "2001"]
    }
  }
}
```

Service-area restrictions are additional allowlists. If an offer also has
standard `countries`, `state_regions`, or `postal_codes`, both restrictions
apply. This conservative intersection prevents a profile from accidentally
expanding a buyer's existing scope.

The current profile implementation intentionally uses exact postal codes.
Postal prefixes, polygons, county/metro data, and external geography datasets
remain deferred until deployments demonstrate that exact versioned sets are
not sufficient.

## Operational rules

1. Use stable canonical vertical values such as `home_services` and
   `service_type=roofing`; do not create a new vertical for one buyer's label.
2. Normalize publisher labels through a versioned mapping with rollback support.
3. Create a new profile version when requirements change materially.
4. Keep free text, exact sensitive measurements, and source-only questions in
   the authorized post or source archive, not in a ping.
5. Record the profile IDs and versions in match/audit records.
6. Treat invalid profile data as non-matching, never as a match-all fallback.
7. Keep profile criteria declarative and reviewable; never execute expressions
   supplied by an offer author.

## Platform profile store (W4-01)

A deployment MAY keep requirement profiles in the platform instead of copying
inline predicates into every offer. Each profile version is an immutable row
identified by `{profile_id, version}`:

| Field | Meaning |
|---|---|
| `profile_id`, `version` | Stable identity. A version's predicates never change. |
| `status` | `active`, `inactive`, or `superseded`. |
| `effective_from`, `effective_to` | The window in which the version may govern matching. |
| `predicates` | Validated predicate array (see below). |
| `service_area_ref` | Optional dataset name resolved at match time. |
| `tenant_id` | Owning tenant; identities are unique per tenant. |

Operations: create a version (always `inactive`), activate (supersedes the
previously active version and closes its window), deactivate (closes the
active window), roll back to a prior version (re-activates it, supersedes the
current one), and list versions with their effective window and an
`effective` flag.

An offer references a stored profile through its existing extension
namespace:

```json
"lcp.platform.requirements": {"profile_id": "home-services", "version": "1"}
```

With `version`, the offer is **pinned**: activating a newer version never
changes what that offer matches. Without `version`, the offer follows the
active version at match time, so activation and rollback are observable to it.
A pinned version that was since superseded still evaluates (the offer copied
its identity); an explicitly `inactive` version, an unknown version, or a
version outside its active window is non-comparable with a named reason
(`requirement_profile_inactive`, `requirement_profile_unknown`,
`requirement_profile_expired`, …) and never silently matches.

## Stored predicate language (W4-02)

Stored profiles use a smaller, typed language than the inline v1.0 block:

| Operator | Meaning |
|---|---|
| `eq` | Exact value equality. |
| `in` / `not_in` | Value in / not in an allowlist. |
| `gte` / `lte` | Inclusive numeric bound. |
| `range` | Inclusive numeric `min`/`max`. |
| `exists` | Field present/absent (`value` is a boolean). |

Paths come from a typed registry (for example `attributes.project_value` is a
number, `location.country_code` is a string, `lead_quality.verification.phone_verified_at`
is a string). Unknown paths and operators are refused at **profile-write
time** with an error naming the offending path or operator; they are never
deferred to match time. Limits: 50 predicates per profile, 100 values per set,
path depth at most 3. Arbitrary expressions, functions, and regex are not
evaluated. The inline block documented above remains supported for
v1.0-compatible offers.

## Match audit record (W4-02, I8)

Every offer/lead evaluation writes one explainable row alongside the decision
history, keyed by `{lead_id, offer_id}`:

```json
{
  "lead_id": "lead_001",
  "offer_id": "buyer-home-services-au",
  "buyer_id": "buyer_001",
  "matched": false,
  "reasons": ["requirements_predicate_mismatch"],
  "profile": {
    "profile_id": "home-services",
    "version": "2",
    "resolved": true,
    "effective_from": "2026-11-01T00:00:00Z",
    "effective_to": null
  },
  "predicates": [
    {"path": "attributes.service_type", "operator": "in",
     "matched": false, "reason": "set_miss"},
    {"path": "attributes.project_value", "operator": "range",
     "matched": true, "reason": "matched"}
  ],
  "service_area": {
    "matched": true,
    "checks": [
      {"ref": "au-nsw-metro", "version": "2026-01-01", "source": "…",
       "matched": true, "reason": "matched", "reasons": []}
    ]
  },
  "created_at": "2026-11-04T12:00:00Z"
}
```

Predicate `reason` values are `matched`, `missing_field`, `wrong_type`,
`outside_range`, `set_miss`, `value_mismatch`, `value_present`,
`field_present`, and `invalid_profile`. The record contains no consumer PII
(paths, operators, and outcomes only) and is retention bounded by
`LCP_MATCH_AUDIT_RETENTION_DAYS` (default 90), purged by the worker during
`process_once`.

## Service-area datasets (W4-03)

Versioned datasets live in `data/service-areas/<name>.json` (override with
`LCP_SERVICE_AREA_DIRECTORY`):

```json
{
  "name": "au-nsw-metro",
  "version": "2026-01-01",
  "effective_from": "2026-01-01T00:00:00Z",
  "effective_to": null,
  "source": "Australia Post postcode list, NSW metropolitan ring",
  "countries": ["AU"],
  "regions": ["NSW"],
  "postal_codes": ["2000", "2001"]
}
```

The loader validates the shape (unknown fields, a `name` that does not match
the file name, non-uppercase country codes, a non-increasing effective window),
caches by file size and mtime, and reloads on change. An offer references a
set with `"lcp.platform.service_area": {"service_area_ref": "au-nsw-metro"}`;
the name resolves to an immutable version snapshot at match time. An unknown,
expired, or ambiguous set makes the offer non-comparable with
`service_area_unknown`, `service_area_expired`, or `service_area_ambiguous` —
never a silent match. A dataset's countries/regions/postal codes are an
additional allowlist intersected with any standard offer scope.

## Candidate index and evidence (W4-04)

The denormalized candidate index remains a prefilter, never an alternate
matcher: any offer with an unrecognized or uncertain configuration keeps a
`fallback` row, so the indexed candidate set is always a superset of the
fallback full scan. Offer create/update/delete keep the index consistent in
the same transaction. `test_requirement_profiles.py` asserts the superset
property over a randomized corpus and the delete/update reindex behaviour.

`tools/benchmark_profiles.py` measures match throughput for N profiles and
offers over a mixed lead corpus and prints a table; `--json` emits the same
report for CI. It makes no hardware-dependent assertions.

See [Publisher mapping](PUBLISHER-MAPPING.md),
[Buyer onboarding](BUYER-ONBOARDING.md), and the
[extension registry](../governance/EXTENSION-REGISTRY.md).

---

**Previous:** [Buyer onboarding](BUYER-ONBOARDING.md) · **Next:** [Platform integration](PLATFORM-INTEGRATION.md)
