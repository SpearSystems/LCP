<?php
// PHP SDK interop probe for tools/sdk_interop_matrix.py (LEP-0002 step 1).
//
// Signs the fixed vector message through the SDK's own Signing::canonicalBytes
// + Signing::sign, then formats the SDK's generation expression
// (LcpSdk.php: gmdate('Y-m-d\TH:i:s\Z')) at a frozen sub-second instant —
// gmdate truncates to whole seconds, so PHP is on the compliant side today.
// The probe never asserts; the matrix owns the checks.

require __DIR__ . '/../src/LcpSdk.php';

$argvIndex = array_search('--vector', $argv, true);
$vectorPath = $argvIndex !== false && isset($argv[$argvIndex + 1])
    ? $argv[$argvIndex + 1]
    : (getenv('LCP_VECTOR') ?: 'test-vectors/sdk/hmac.json');
if (!is_readable($vectorPath)) {
    fwrite(STDERR, "hmac.json vector not found (pass --vector)\n");
    exit(2);
}
$vector = json_decode(file_get_contents($vectorPath), true);

$canonical = Lcp\Signing::canonicalBytes($vector['timestamp'], $vector['idempotency_key'], $vector['body_utf8']);
// Frozen instant 2026-08-15T10:20:00.123456789Z through the SDK's own
// generation expression (float epoch carries the fraction into the call).
$emitted = gmdate('Y-m-d\TH:i:s\Z', (int) 1786789200.123456789);

$result = [
    'canonical_sha256' => hash('sha256', $canonical),
    'emitted_timestamp' => $emitted,
    'sdk' => 'php',
    'signature_hex' => Lcp\Signing::sign($vector['secret'], $vector['timestamp'], $vector['idempotency_key'], $vector['body_utf8']),
];
ksort($result);
$json = json_encode($result);
// Logging hygiene: no payload prints on stdout; stderr + LCP_INTEROP_OUT.
fwrite(STDERR, 'LCP_INTEROP ' . $json . PHP_EOL);
$out = getenv('LCP_INTEROP_OUT');
if ($out !== false && $out !== '') {
    file_put_contents($out, $json . PHP_EOL);
}
