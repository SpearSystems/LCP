# Calls and telephony

> **Integration page · Page 2 of 6**
>
> **Previous:** [Publisher mapping](PUBLISHER-MAPPING.md) · **Next:** [MVA and attachments](MVA-ATTACHMENTS.md)

LCP carries the interoperable call record and commercial lifecycle. It does
not replace a carrier, telephony provider, dialler, or live call-control API.
A production call integration has two cooperating layers:

```text
telephony provider / bridge  →  call adapter  →  LCP call, event, post messages
```

## Two call modes

### `post_call`

The publisher or call platform submits a `call` message after the call has
been answered or completed. SpearPointX can run the normal ping/bid/post flow,
then deliver the full call record and any permitted attachments in the post.
This is the simplest mode for recorded-call billing and asynchronous CRM
processing.

### `realtime_transfer`

The call adapter submits a minimal call offer to the platform while the caller
is still connected. The buyer has a short decision window, configured by
`connect_timeout_seconds` and `ping_timeout_seconds`. The platform returns the
commercial decision, while a deployment-specific telephony adapter performs
the actual transfer to the buyer's destination.

The LCP platform must never treat a bid as proof that a carrier transfer
succeeded. The adapter must report the result using lifecycle events.

## Recommended lifecycle

```text
CALL_OFFERED
    ↓ buyer accepts
CALL_CONNECTED
    ↓ caller and buyer disconnect
CALL_ENDED
    ↓ duration/disposition evaluated
CALL_OUTCOME
```

An event payload should identify the offer without embedding consumer data:

```json
{
  "lead_id": "mva-call-001",
  "event": "CALL_OUTCOME",
  "timestamp": "2026-08-15T10:05:00Z",
  "details": {
    "offer_id": "buyer-mva-call",
    "call_status": "answered",
    "total_seconds": 65,
    "disposition": "qualified_lead",
    "transfer_status": "connected",
    "telephony_reference": "provider-call-reference"
  }
}
```

The platform authenticates event senders, stores the event, and evaluates the
offer's payable rules. Event names remain open so a telephony provider can add
new states without changing the core message type.

## Call offer configuration

A call offer can declare:

```json
{
  "routing_mode": "auction",
  "vertical": "mva",
  "channels": ["call"],
  "call_routing_mode": "realtime_transfer",
  "connect_timeout_seconds": 5,
  "payable_rules": {
    "mode": "call_outcome",
    "require_call_answered": true,
    "minimum_call_seconds": 30,
    "allowed_call_dispositions": ["qualified_lead"]
  }
}
```

`payable_rules` are intentionally structured and explainable:

- `require_call_answered` rejects no-answer, busy, voicemail, and failed calls;
- `minimum_call_seconds` enforces a duration threshold; and
- `allowed_call_dispositions` limits payable outcomes to agreed dispositions.

A failed or missing transfer is not silently counted as payable. The buyer and
publisher can dispute an outcome through `DISPUTED`, after which the platform
can record `REFUNDED` or resolve it back to payable according to the bilateral
contract.

## Call posts

A call `post` carries:

- normal consumer, location, consent, provenance, and vertical attributes;
- the complete `call` block, including status, durations, transfer,
  disposition, and recording references; and
- attachment metadata for any authorized documents.

Binary recordings and documents are never embedded in JSON. Use the
[attachment flow](MVA-ATTACHMENTS.md) and send only authenticated metadata in
the post.

## Security and compliance

- Use a separate telephony credential per publisher, platform, and buyer.
- Do not put raw caller audio, transcripts, or carrier tokens in a ping.
- Use opaque provider references and authenticated attachment downloads.
- Apply DNC, consent-purpose, recording-consent, and jurisdiction checks before
  connecting a caller to a buyer.
- Treat the call provider's event as untrusted input until the HMAC, schema,
  idempotency, and sender authorization checks succeed.
- Preserve the event and call-record audit trail without logging raw recordings
  or consumer PII.

**Previous:** [Publisher mapping and normalization](PUBLISHER-MAPPING.md) · **Next:** [MVA and attachments](MVA-ATTACHMENTS.md)
