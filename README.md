# LCP — Lead Context Protocol

**The open standard for exchanging consumer lead data.**

LCP is a universal, Apache-2.0 protocol for transferring consumer lead
data (PII) between publishers, platforms, and buyers — the "HTTP of lead
generation." It covers every lead channel (form fills, calls, chats,
clicks, API, AI agents) and the full lifecycle (intake → ping → post →
accept/reject → dispute/refund → conversion).

- **Fast core:** HTTP + JSON, millisecond routing, HMAC-signed,
  idempotent, PII-disciplined. Built for system-to-system transfer.
- **Agent-ready:** a thin binding layer (MCP first) lets AI agents
  submit, transport, and receive leads — without the core depending on
  any agent protocol.
- **Universal:** zero vertical-specific or market-specific fields in the
  core. New vertical = new JSON Schema. New channel = new message type.
  New business state = new event. Designed for 20+ years.

## Status

**DRAFT v1.0** — spec under active development. Not yet published.

## Quickstart

```bash
# Validate a lead against the LCP core + a vertical schema
# (reference validators land in implementations/)
```

See [SPEC.md](SPEC.md) for the full specification and
[examples/](examples/) for sample payloads.

## Repository layout

```
LICENSE            Apache 2.0
SPEC.md            The canonical specification
schemas/           JSON Schema for the envelope, core, and message types
verticals/         Per-vertical attribute schemas
examples/          Sample payloads (lead, call, ping, post, ack, event)
test-vectors/      Conformance fixtures (L1/L2/L3)
governance/        CONTRIBUTING, CLA, extension registry
implementations/   Reference implementations (incl. MCP server)
docs/              Design notes and research
```

## License

Apache 2.0. See [LICENSE](LICENSE). The spec is free to implement
without payment, approval, or membership. See
[governance/](governance/) for the anti-capture and extension policies.
