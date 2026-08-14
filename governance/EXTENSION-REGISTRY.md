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

### Rejection grounds

A registration is rejected only if:

- The namespace collides with an existing registration or `lcp.core.*`.
- The namespace format is invalid (does not match `{org}.{division}.{purpose}`).
- The namespace is demonstrably misleading (e.g. claims to be a core field).

No editorial veto. No fees. No mandatory review period.

## Registry

| Namespace | Registrant | Description | Since |
|---|---|---|---|
| _(empty — registrations open)_ | | | |

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