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

See [Publisher mapping](PUBLISHER-MAPPING.md),
[Buyer onboarding](BUYER-ONBOARDING.md), and the
[extension registry](../governance/EXTENSION-REGISTRY.md).

---

**Previous:** [Buyer onboarding](BUYER-ONBOARDING.md) · **Next:** [Platform integration](PLATFORM-INTEGRATION.md)
