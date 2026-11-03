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
$corpus = json_decode(file_get_contents($root . '/test-vectors/sdk/validation-corpus.json'), true);
$failures = [];
foreach ($corpus['fixtures'] as $fixture) {
    $isOffer = isset($fixture['offer']);
    $document = $isOffer ? $fixture['offer'] : $fixture['envelope'];
    $passed = true;
    try {
        if ($isOffer) {
            $validator->validateOffer($document);
        } else {
            $validator->validateEnvelope($document);
        }
    } catch (RuntimeException $e) {
        $passed = false;
    }
    if ($passed !== ($fixture['expect'] === 'pass')) {
        $failures[] = $fixture['id'] . ' (' . $fixture['rule'] . '): expected ' . $fixture['expect'];
    }
}
if ($failures) {
    throw new RuntimeException('Shared validation corpus mismatches: ' . implode(', ', $failures));
}
echo "PHP SDK HMAC and full JSON Schema vectors passed\n";
echo "PHP SDK shared validation corpus passed\n";
$rows = require __DIR__ . '/lep0003_slots.php';
echo "PHP SDK LEP-0003 vectors and slot round-trip passed ($rows contact-hash rows reproduced byte-exactly)\n";

// LEP-0001 W2-01 batch submission: 207 is a successful response, receipts come
// back in input order, and a batch over the cap is refused before any request.
$server = stream_socket_server('tcp://127.0.0.1:0', $errno, $errstr);
if ($server === false) throw new RuntimeException("socket: $errstr");
$address = stream_socket_get_name($server, false);
$port = (int) substr(strrchr($address, ':'), 1);
$capture = tempnam(sys_get_temp_dir(), 'lcp-batch-');
$pid = pcntl_fork();
if ($pid === -1) throw new RuntimeException('fork failed');
if ($pid === 0) {
    $connection = stream_socket_accept($server, 10);
    $raw = '';
    while (strpos($raw, "\r\n\r\n") === false) $raw .= fread($connection, 8192);
    [$headers, $rest] = explode("\r\n\r\n", $raw, 2);
    $length = preg_match('/Content-Length:\s*(\d+)/i', $headers, $match) === 1 ? (int) $match[1] : 0;
    $requestBody = $rest;
    while (strlen($requestBody) < $length) $requestBody .= fread($connection, $length - strlen($requestBody));
    file_put_contents($capture, $requestBody);
    $responseBody = '{"request_id":"req-batch-1","summary":{"total":2,"accepted":1,"duplicate":0,"rejected":1,"error":0},"results":['
        . '{"item_id":"item_b","status":"accepted","lead_id":"lead_b"},'
        . '{"item_id":"item_a","status":"rejected","error":{"code":"LCP-100","message":"invalid lead"}}]}';
    fwrite($connection, "HTTP/1.1 207 Multi-Status\r\nContent-Type: application/json\r\nContent-Length: " . strlen($responseBody) . "\r\nConnection: close\r\n\r\n" . $responseBody);
    fclose($connection);
    fclose($server);
    exit(0);
}
$client = new Lcp\Client("http://127.0.0.1:$port", 'publisher_001', 'api-key', null, 0);
$results = $client->submitLeadsBatch([
    ['item_id' => 'item_a', 'idempotency_key' => 'batch-a', 'payload' => ['lead_id' => 'lead_a']],
    ['item_id' => 'item_b', 'idempotency_key' => 'batch-b', 'payload' => ['lead_id' => 'lead_b']],
], 'req-batch-1');
pcntl_waitpid($pid, $status);
$ordered = array_map(static fn (array $result): string => $result['item_id'], $results);
if ($ordered !== ['item_a', 'item_b']) throw new RuntimeException('unexpected batch order: ' . implode(',', $ordered));
if ($results[0]['status'] !== 'rejected' || $results[0]['error']['code'] !== 'LCP-100') throw new RuntimeException('per-item error lost');
if ($results[1]['status'] !== 'accepted') throw new RuntimeException('accepted receipt lost');
$sent = json_decode(file_get_contents($capture), true);
unlink($capture);
if ($sent['request_id'] !== 'req-batch-1' || $sent['items'][0]['payload']['lead_id'] !== 'lead_a') throw new RuntimeException('batch wire shape lost');
try {
    $overCap = array_map(static fn (int $index): array => ['item_id' => "item_$index", 'idempotency_key' => "key_$index", 'payload' => []], range(0, 100));
    $client->submitLeadsBatch($overCap);
    throw new RuntimeException('batch over the cap was accepted');
} catch (InvalidArgumentException) { }
echo "PHP SDK batch submission passed\n";
