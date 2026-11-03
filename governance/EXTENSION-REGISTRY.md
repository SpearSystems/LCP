# LCP Extension Registry

Extensions allow organizations to add non-core fields and behaviors to LCP
without modifying the specification. Extensions are namespaced and
self-registering.

## Namespace Format

```
{org}.{division}.{purpose}
```

- `org` — organization identifier (lowercase, no spaces).
- `division` — organizational division or product line.
- `purpose` — the extension's functional purpose.

Examples:

- `acme.ops.routing_priorities` — Acme routing priority metadata.
- `partner.intake.source_campaign` — partner intake campaign attribution.
- `acme.lead_scoring.risk_model` — Acme's proprietary lead scoring model.

## Registration

Registration is **open** — no approval required. To register:

1. Add an entry to the registry table below.
2. Open a pull request (format check only — the maintainers verify the
   namespace format, not the extension's content or design).
3. The entry is merged if the format is valid and the namespace does not
   collide with an existing registration or `lcp.core.*`.

Rows may also land **proposed-on-acceptance**: an LEP files its
namespace rows with the Draft record so the names are visible and
reserved before the decision — such rows carry `proposed (LEP-NNNN)`
in the **Since** column instead of a version, are not live until the
named LEP track is accepted, and registry rows never mark acceptance
themselves (maintainers record decisions in
`governance/LEP-REGISTRY.md`).

### Rejection grounds

A registration is rejected only if:

- The namespace collides with an existing registration or `lcp.core.*`.
- The namespace format is invalid (does not match `{org}.{division}.{purpose}`).
- The namespace is demonstrably misleading (e.g. claims to be a core field).

No editorial veto. No fees. No mandatory review period.

## Registry

| Namespace | Registrant | Description | Since |
|---|---|---|---|
| `lcp.platform.requirements` | LCP reference platform | Versioned, allowlisted buyer requirement predicates evaluated before routing; no executable expressions. | v1.0 |
| `lcp.platform.service_area` | LCP reference platform | Versioned country, region, and exact-postal service-area allowlists for deployment-scoped offer matching. | v1.0 |
| `lcp.marketplace.final_pricing` | LCP reference platform | Proposed-on-acceptance (LEP-0006 Track A): deployment-specific additions inside `FINAL_PRICING` event `details` (publisher-attested winning price/duration for a ping, incl. external sales). Not live until LEP-0006 Track A is accepted. | proposed (LEP-0006) |
| `lcp.form.questions` | LCP reference platform | Proposed-on-acceptance (LEP-0006 Track B): registered `form.questions` shape — bounded array of verbatim intake question/answer pairs carried in `lead`/`post` `extensions`; post-only, never ping_safe, scrub rules apply. Not live until LEP-0006 Track B is accepted. | proposed (LEP-0006) |
| `lcp.outbound.call_request` | LCP reference platform | Proposed-on-acceptance (LEP-0007): namespaced `event` value (open enum) requesting the platform place an outbound call or warm transfer on a delivered lead, keyed by `lead_id` only; decline rides the open `rejection_reason` value `ignored`. Not live until LEP-0007 is accepted. | proposed (LEP-0007) |

## Extension payload location

Extension data lives in the `extensions` object of an LCP message:

```json
{
  "lcp": {
    "version": "1.0.0",
    "message": { ... },
    "payload": {
      "extensions": {
        "spear.ops.routing_priorities": {
          "priority_tier": "gold",
          "max_buyers": 5
        }
      }
    }
  }
}
```

Extensions are **optional** — unknown extensions are ignored by
implementations that do not recognize them (SPEC.md §6, "unknown optional
fields ignored"). This is the forward-compatibility guarantee: an
implementation that does not know about an extension must not fail.