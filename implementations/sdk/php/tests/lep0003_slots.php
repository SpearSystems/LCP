<?php
// LEP-0003 SDK lane: contact-hash vectors, slot round-trip, §8 capability gate.
// Executed from tests/shared_vector.php (the script CI runs). Deterministic:
// repo test-vectors + examples + schemas/, no network.

require_once __DIR__ . '/../src/LcpSdk.php';

$root = __DIR__ . '/../../../..';
$suite = json_decode(file_get_contents("$root/test-vectors/sdk/contact-hash.json"), true);
$vectors = $suite['vectors'] ?? [];
if (!$vectors) throw new RuntimeException('contact-hash.json exposed no vectors');

$failures = [];
foreach ($vectors as $vector) {
    $observed = Lcp\Signing::contactHash($vector['secret'], $vector['preimage'], $vector['domain']);
    if ($observed !== $vector['digest']) {
        $failures[] = "{$vector['name']} ({$vector['domain']}): preimage \"" . $vector['preimage'] .
            '" pinned ' . $vector['digest'] . " but PHP SDK produced $observed";
    }
}
if ($failures) throw new RuntimeException("contact-hash vector reproduction failed:\n" . implode("\n", $failures));

// SPEC.md §9 preimage rules: phone verbatim; email trims and ASCII-lowercases the domain only.
if (Lcp\Signing::contactHash('pair-secret-for-vectors-0123456789', '+1 (512) 555-0147') === 'c47aff331606437528b34b7f919eb37704e87ee35eed80e52cfa36826a4a2e12') {
    throw new RuntimeException('phone preimage must stay verbatim; separators must not be folded away');
}
$pinnedEmail = 'd28da9b5424990cf7ac6000cf4e04537ca487929e71cef12f91191f29f78a6c4';
if (Lcp\Signing::contactHash('pair-secret-for-vectors-0123456789', '  Jordan@Example.COM  ', 'email') !== $pinnedEmail) {
    throw new RuntimeException('email canonical must trim whitespace and ASCII-lowercase the domain');
}
if (Lcp\Signing::contactHash('pair-secret-for-vectors-0123456789', 'jordan@example.com', 'email') === $pinnedEmail) {
    throw new RuntimeException('local-part bytes are verbatim: a casefolded local part must differ');
}
if (Lcp\Signing::contactHash('pair-secret-for-vectors-0123456789', 'Jordan+crm@example.com', 'email') === $pinnedEmail) {
    throw new RuntimeException('plus-addresses are distinct under the §9 canonical');
}

// §8 version-string membership: parsed full semver only, never literal or prefix.
$gateCases = [
    [['1.0.0'], false], [['1.1.0'], true], [['1.0.0', '1.1.0'], true], [['1.10.0'], true],
    [['2.0.0'], false], [['1.1'], false], [['v1.1.0', '1.1.0.1', ''], false],
];
foreach ($gateCases as [$advertised, $expected]) {
    if (Lcp\Capability::receiverAdvertisesSlotFields($advertised) !== $expected) {
        throw new RuntimeException('capability gate membership mismatch for ' . json_encode($advertised));
    }
}

$validator = Lcp\SchemaValidator::fromDirectory("$root/schemas");
$envelopes = [];
foreach (['ping-slots', 'bid-slots', 'post-contacted', 'event-contacted'] as $name) {
    $envelopes[$name] = json_decode(file_get_contents("$root/examples/$name.json"), true);
}

// Slot-bearing ping/bid/post/event validate through the SDK schema path.
foreach ($envelopes as $envelope) $validator->validateEnvelope($envelope);

// The capability gate rejects them toward a v1.0-only receiver…
foreach ($envelopes as $name => $envelope) {
    $rejected = false;
    try {
        $validator->validateEnvelope($envelope, ['1.0.0']);
    } catch (RuntimeException $error) {
        if (!str_contains($error->getMessage(), 'capability gate')) throw new RuntimeException("unexpected rejection for $name: " . $error->getMessage());
        $rejected = true;
    }
    if (!$rejected) throw new RuntimeException("slot-bearing $name was accepted for a v1.0-only receiver");
}
// …and accepts them toward a v1.1 advertisement.
foreach ($envelopes as $envelope) $validator->validateEnvelope($envelope, ['1.0.0', '1.1.0']);

return count($vectors);
