# LCP — Lead Context Protocol

> **Created and maintained by Spear Systems** (a Spear company).
> This is an open standard — Apache 2.0, free to implement.
> Spear Systems stewards the conformance test suite and reference
> implementations, but the protocol is community-governed.

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

**DRAFT v1.0** — spec amendments complete, schemas authored, conformance
tests passing (27/27), reference MCP server working. Pre-publication.

## Quickstart

```bash
# Validate examples against schemas (conformance runner)
python3 test-vectors/conformance.py --verbose

# Run the MCP server (needs: pip install -e implementations/mcp-server/)
lcp-mcp-server
```

## Repository layout

```
LICENSE            Apache 2.0
SPEC.md            The canonical specification
schemas/           JSON Schema (Draft 2020-12) for envelope, core, and message types
verticals/         Per-vertical attribute schemas (mortgage first)
examples/          Sample payloads (lead, call, ping, post, ack, event)
test-vectors/      Conformance fixtures (L1/L2/L3) + conformance runner
governance/        CONTRIBUTING, CLA, EXTENSION-REGISTRY, SECURITY, TRADEMARK
implementations/   Reference MCP server (thin REST adapter)
docs/              Design notes and deep-research review
```

## Key documents

- [SPEC.md](SPEC.md) — the full specification (14 sections + appendices)
- [governance/SECURITY.md](governance/SECURITY.md) — responsible disclosure policy
- [governance/TRADEMARK.md](governance/TRADEMARK.md) — "LCP compliant" usage rules
- [governance/EXTENSION-REGISTRY.md](governance/EXTENSION-REGISTRY.md) — extension namespace registry
- [implementations/mcp-server/](implementations/mcp-server/) — reference MCP server
- [docs/PLATFORM-INTEGRATION.md](docs/PLATFORM-INTEGRATION.md) — platform mapping guide (Facebook, Google, Twilio, HubSpot, Salesforce, TikTok)
- [docs/lcp-deep-research-review.md](docs/lcp-deep-research-review.md) — adversarial review + resolutions

## License

Apache 2.0. See [LICENSE](LICENSE). The spec is free to implement
without payment, approval, or membership. See
[governance/](governance/) for the anti-capture, security, trademark,
and extension policies.