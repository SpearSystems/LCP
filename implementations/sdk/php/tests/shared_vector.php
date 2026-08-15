<?php
require __DIR__ . '/../vendor/autoload.php';
require __DIR__ . '/../src/LcpSdk.php';

$body = '{"hello":"world"}';
$signature = Lcp\Signing::sign('sdk-shared-secret', '2026-08-15T10:20:00Z', 'sdk-vector-001', $body);
$expected = '50f90d29b46e92f257eae62c94a3d985bf9a925da03fee82e33c800fe54e7259';
if ($signature !== $expected) throw new RuntimeException($signature);
Lcp\Signing::verify('sdk-shared-secret', $signature, '2026-08-15T10:20:00Z', 'sdk-vector-001', $body, 300, new DateTimeImmutable('2026-08-15T10:20:01Z'));
$root = __DIR__ . '/../../../..';
$validator = Lcp\SchemaValidator::fromDirectory($root . '/schemas');
$validator->validateEnvelope(json_decode(file_get_contents($root . '/examples/lead.json'), true));
echo "PHP SDK HMAC and full JSON Schema vectors passed\n";
