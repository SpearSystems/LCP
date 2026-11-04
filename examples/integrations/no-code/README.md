# No-code integration files

> **Example — not for production without review.** These are reviewed starters
> for the recipes in [`docs/RECIPES.md`](../../../docs/RECIPES.md). They carry
> secrets handling and a schema-valid envelope, but they are not a supported
> runtime and they are **not** one-click marketplace templates.

## Which file is for which platform

| File | Platform | What it is | What you do with it |
|---|---|---|---|
| [`supabase-edge-function.ts`](supabase-edge-function.ts) | Lovable form → Supabase | A Deno edge function that maps a form submission to an LCP lead, signs it with HMAC, and returns the platform ack | Deploy as a Supabase Edge Function named `lcp-lead`; secrets via `supabase secrets set` |
| [`make-blueprint.json`](make-blueprint.json) | Make | **Module configuration, not an importable blueprint**: URL, headers, and the raw JSON body template | Recreate the HTTP module in the Make editor, or paste the body template |
| [`apps-script.gs`](apps-script.gs) | Google Sheets / Google Forms | Apps Script that posts a row or a form response with `UrlFetchApp`, reading endpoint, ids, and HMAC secret from Script Properties | Paste into a bound Apps Script project; add an installable "On form submit" trigger |
| this README | all five recipes | Pointers and test steps | Read first |

GoHighLevel and Zapier have no file here: both are configured in the vendor UI
(see [the recipes](../../../docs/RECIPES.md)). Zapier's path includes a Code
step you can copy from the recipe.

## Not one-click yet

These are honest limits, not bugs:

1. **No marketplace listing or template link.** A genuine one-click Make
   blueprint or Zapier app template can only be produced by exporting it from
   that vendor's own account. This repository ships the module configuration
   and the raw body template instead, so nothing here pretends to be
   importable. `make-blueprint.json` is a **configuration document**; Make will
   not import it as a scenario, and its own `_importable` field says so.
2. **No Supabase project is provisioned for you.** The edge function has to be
   deployed into your project, with your secrets, in your region.
3. **No Apps Script library or add-on.** The script must live in a project
   bound to your sheet, because it reads your header row and your triggers.
4. **Vendor UI names change.** Click paths in the recipes name the current
   actions and say where a label may differ. Follow the intent, not the exact
   word.
5. **GoHighLevel and Zapier's HTTP actions cannot compute the LCP HMAC.** Their
   webhook and Custom Request steps do not give you an HMAC over the exact
   request body. Use a Bearer API key, or route through the Supabase function,
   which signs server-side.

## How to test one of these

The acceptance is always the same three signals, whatever platform:

1. **Sandbox + test flags.** Point the endpoint at the operator's sandbox, send
   `"test": true` in `lcp.message`, and send `X-LCP-Test: true` while the
   payload says test. Both markers must agree.
2. **A clean ack.** A 2xx response whose ack status is `RECEIVED`,
   `VALIDATED`, `ACCEPTED`, or `PROCESSED` — not `ERROR`, not `REJECTED`.
3. **A duplicate on re-run.** Send the identical submission again with the same
   idempotency key. The ack must say `DUPLICATE`. An `ACCEPTED` on the second
   run means the key is not stable and retries will create leads twice.

Per-file first runs:

| File | Fastest proof |
|---|---|
| Supabase function | `curl` the function URL with a synthetic body (the recipe has the exact command), then `curl` it again with the same `submission_id` |
| Make | Run the scenario once, read the HTTP module's output bundle, run it again |
| Apps Script | Run `testSend` from the editor (approve the authorization prompt), then submit the real form once and read **Executions** |

Use synthetic values only. The examples use `example.com`, `example.invalid`,
and `+61412345678` on purpose; never point them at real consumer data while
testing.

## Where the field names come from

The envelope and payload shapes are defined by the repository schemas, not by
these files:

- [`schemas/envelope.json`](../../../schemas/envelope.json) — the wrapper and
  the message header;
- [`schemas/lead.json`](../../../schemas/lead.json) — the lead payload;
- [`schemas/core.json`](../../../schemas/core.json) — shared consumer,
  location, compliance, and provenance blocks;
- [`verticals/`](../../../verticals/) — the vertical `attributes` shape;
- [`spec/01-transport.md`](../../../spec/01-transport.md) — the HMAC signing
  input and headers.

The envelopes produced by the shipped files were validated against those
schemas before landing; if you edit the mapping, re-validate rather than
trusting the diff.

## Related

- [No-code and low-code recipes](../../../docs/RECIPES.md)
- [Publisher onboarding](../../../docs/PUBLISHER-ONBOARDING.md)
- [Integration examples](../README.md)
- [Error guide](../../../docs/ERROR-GUIDE.md)
- [HTTP contract](../../../api/lcp-openapi.yaml)
