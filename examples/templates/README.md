# Integration templates

These files are sanitized starting points:

- [`../lead.json`](../lead.json) — complete synthetic lead envelope;
- [`offer-auction.json`](offer-auction.json) — buyer auction offer with common
  country, quality, compliance, capacity, and pricing criteria.

Before use, replace the synthetic IDs, timestamps, vertical attributes,
consent evidence, endpoint URL, currency, prices, and commercial terms. Keep
`message.id`, `payload.lead_id`, and `message.idempotency_key` stable for one
logical submission and generate new values for a new submission.

Do not commit real consumer data, production URLs, credentials, HMAC secrets,
consent tokens, CRM IDs, or private configuration to a template or example.
Validate the completed envelope and offer against the schemas before sending
or publishing them.
