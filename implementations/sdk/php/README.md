# LCP PHP SDK

Tier 2 reference SDK for PHP 8.1+ publisher, WordPress, Laravel, and buyer
integrations.

```bash
composer require spearsystems/lcp-sdk
```

From this repository:

```bash
php tests/shared_vector.php
```

The SDK provides generated payload models, `Lcp\\SchemaValidator` full Draft 2020-12 validation, and `Lcp\\Envelope`, `Lcp\\Signing`, and `Lcp\\Client` helpers
for envelope construction, validation, canonical HMAC signing/verification,
raw-body webhook verification, and cURL HTTP operations for leads, calls,
bids, status, capabilities, and offers. See the shared [SDK contract](../../../docs/SDK-ROADMAP.md).
