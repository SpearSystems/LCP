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

// LEP-0005 history read: only supplied, non-empty filters reach the query, the
// decoded body comes back unchanged, and a 4xx throws the client's exception.
$serveOnce = static function ($server, int $status, string $responseBody): string {
    $connection = stream_socket_accept($server, 10);
    $raw = '';
    while (strpos($raw, "\r\n\r\n") === false) $raw .= fread($connection, 8192);
    [$headers] = explode("\r\n\r\n", $raw, 2);
    $target = preg_match('#^GET\s+(\S+)\s+HTTP/#', $headers, $match) === 1 ? $match[1] : '';
    $reason = $status === 200 ? 'OK' : 'Bad Request';
    fwrite($connection, "HTTP/1.1 $status $reason\r\nContent-Type: application/json\r\nContent-Length: " . strlen($responseBody) . "\r\nConnection: close\r\n\r\n" . $responseBody);
    fclose($connection);
    fclose($server);
    return $target;
};

$historyServer = stream_socket_server('tcp://127.0.0.1:0', $errno, $errstr);
if ($historyServer === false) throw new RuntimeException("history server: $errstr");
$historyPort = (int) substr(strrchr(stream_socket_get_name($historyServer, false), ':'), 1);
$historyCapture = tempnam(sys_get_temp_dir(), 'lcp-history-');
$historyPid = pcntl_fork();
if ($historyPid === -1) throw new RuntimeException('fork failed');
if ($historyPid === 0) {
    $target = $serveOnce($historyServer, 200, '{"requests":[{"request_id":"req-hist-1","message_type":"lead","direction":"inbound","status":"OPEN","created_at":"2026-11-01T00:00:00Z"}]}');
    file_put_contents($historyCapture, $target);
    exit(0);
}
$historyClient = new Lcp\Client("http://127.0.0.1:$historyPort", 'publisher_001', 'api-key', null, 0);
$historyBody = $historyClient->requestHistory(['limit' => 2, 'cursor' => '', 'status' => 'OPEN', 'to' => '2026-11-01T00:00:00Z']);
pcntl_waitpid($historyPid, $historyStatus);
$historyTarget = file_get_contents($historyCapture);
unlink($historyCapture);
if ($historyBody['requests'][0]['request_id'] !== 'req-hist-1' || $historyBody['requests'][0]['status'] !== 'OPEN') throw new RuntimeException('history row lost');
if ($historyTarget !== '/v1/lcp/history/requests?limit=2&status=OPEN&to=2026-11-01T00%3A00%3A00Z') throw new RuntimeException('history query: ' . $historyTarget);

$historyServer = stream_socket_server('tcp://127.0.0.1:0', $errno, $errstr);
if ($historyServer === false) throw new RuntimeException("history server: $errstr");
$historyPort = (int) substr(strrchr(stream_socket_get_name($historyServer, false), ':'), 1);
$historyPid = pcntl_fork();
if ($historyPid === -1) throw new RuntimeException('fork failed');
if ($historyPid === 0) {
    $serveOnce($historyServer, 400, '{"errors":[{"code":"LCP-001","message":"limit out of range"}]}');
    exit(0);
}
$historyClient = new Lcp\Client("http://127.0.0.1:$historyPort", 'publisher_001', 'api-key', null, 0);
try {
    $historyClient->requestHistory(['limit' => 500]);
    throw new RuntimeException('history 4xx was accepted');
} catch (RuntimeException $historyError) {
    if (!str_starts_with($historyError->getMessage(), 'LCP HTTP 400')) throw $historyError;
}
pcntl_waitpid($historyPid, $historyStatus);
echo "PHP SDK history read passed\n";

// Schema fetch: a slash-namespaced name keeps its separators, every other
// character is percent-encoded, and the decoded body comes back unchanged.
$schemaServer = stream_socket_server('tcp://127.0.0.1:0', $errno, $errstr);
if ($schemaServer === false) throw new RuntimeException("schema server: $errstr");
$schemaPort = (int) substr(strrchr(stream_socket_get_name($schemaServer, false), ':'), 1);
$schemaCapture = tempnam(sys_get_temp_dir(), 'lcp-schema-');
$schemaPid = pcntl_fork();
if ($schemaPid === -1) throw new RuntimeException('fork failed');
if ($schemaPid === 0) {
    $target = $serveOnce($schemaServer, 200, '{"title":"Lead","type":"object"}');
    file_put_contents($schemaCapture, $target);
    exit(0);
}
$schemaClient = new Lcp\Client("http://127.0.0.1:$schemaPort", 'publisher_001', 'api-key', null, 0);
$schemaBody = $schemaClient->getSchema('verticals/mortgage?x=1');
pcntl_waitpid($schemaPid, $schemaStatus);
$schemaTarget = file_get_contents($schemaCapture);
unlink($schemaCapture);
if ($schemaBody['title'] !== 'Lead' || $schemaBody['type'] !== 'object') throw new RuntimeException('schema body lost');
if ($schemaTarget !== '/v1/lcp/schemas/verticals/mortgage%3Fx%3D1') throw new RuntimeException('schema path: ' . $schemaTarget);
echo "PHP SDK schema fetch passed\n";

// Verify parity with the Python reference verifier: fractional seconds verify;
// an unzoned timestamp (read in the default timezone by DateTimeImmutable) or a
// non-hex signature is refused.
$verifyNow = new DateTimeImmutable('2026-08-15T10:20:01Z');
$fractional = '2026-08-15T10:20:00.250Z';
Lcp\Signing::verify('sdk-shared-secret', Lcp\Signing::sign('sdk-shared-secret', $fractional, 'sdk-vector-001', $body), $fractional, 'sdk-vector-001', $body, 300, $verifyNow);
$unzoned = '2026-08-15T10:20:00';
foreach ([[$unzoned, Lcp\Signing::sign('sdk-shared-secret', $unzoned, 'sdk-vector-001', $body)], ['2026-08-15T10:20:00Z', substr($signature, 0, -2) . 'zz']] as [$badTimestamp, $badSignature]) {
    try {
        Lcp\Signing::verify('sdk-shared-secret', $badSignature, $badTimestamp, 'sdk-vector-001', $body, 300, $verifyNow);
        throw new LogicException("$badTimestamp / $badSignature was accepted");
    } catch (RuntimeException) { }
}
echo "PHP SDK verify parity passed\n";

// Retry parity with the Python SDK: 429/5xx retry up to maxRetries, a
// delta-seconds Retry-After sets the wait, other statuses fail at once, and a
// batch is never retried.
$serveStatuses = static function (array $statuses): array {
    $server = stream_socket_server('tcp://127.0.0.1:0', $errno, $errstr);
    if ($server === false) throw new RuntimeException("socket: $errstr");
    $port = (int) substr(strrchr(stream_socket_get_name($server, false), ':'), 1);
    $hitsFile = tempnam(sys_get_temp_dir(), 'lcp-retry-');
    $pid = pcntl_fork();
    if ($pid === -1) throw new RuntimeException('fork failed');
    if ($pid === 0) {
        foreach ($statuses as $hits => $status) {
            $connection = @stream_socket_accept($server, 3);
            if ($connection === false) break;
            file_put_contents($hitsFile, (string) ($hits + 1));
            $raw = '';
            while (strpos($raw, "\r\n\r\n") === false) $raw .= fread($connection, 8192);
            [$headers, $rest] = explode("\r\n\r\n", $raw, 2);
            $length = preg_match('/Content-Length:\s*(\d+)/i', $headers, $match) === 1 ? (int) $match[1] : 0;
            while (strlen($rest) < $length) $rest .= fread($connection, $length - strlen($rest));
            $responseBody = $status === 200 ? '{"ok":true,"results":[]}' : '{"errors":[{"code":"LCP-011"}]}';
            fwrite($connection, "HTTP/1.1 $status X\r\nContent-Type: application/json\r\nRetry-After: 0\r\nContent-Length: " . strlen($responseBody) . "\r\nConnection: close\r\n\r\n" . $responseBody);
            fclose($connection);
        }
        exit(0);
    }
    fclose($server);
    return [$port, static function () use ($pid, $hitsFile): int { pcntl_waitpid($pid, $status); $hits = (int) file_get_contents($hitsFile); unlink($hitsFile); return $hits; }];
};
[$port, $hits] = $serveStatuses([429, 503, 200]);
$started = microtime(true);
$retried = (new Lcp\Client("http://127.0.0.1:$port"))->getCapabilities();
if (($retried['ok'] ?? false) !== true) throw new RuntimeException('retry body lost');
if (($count = $hits()) !== 3) throw new RuntimeException("retry hits $count, want 3");
if (microtime(true) - $started >= 0.9) throw new RuntimeException('Retry-After: 0 must replace the 1 s / 2 s backoff');
foreach ([[[503, 503], 1, 2, false], [[400], 2, 1, false], [[503], 2, 1, true]] as [$statuses, $maxRetries, $want, $batch]) {
    [$port, $hits] = $serveStatuses($statuses);
    $client = new Lcp\Client("http://127.0.0.1:$port", null, null, null, $maxRetries);
    try {
        $batch ? $client->submitLeadsBatch([['item_id' => 'a', 'idempotency_key' => 'k', 'payload' => []]]) : $client->getCapabilities();
        throw new LogicException(json_encode($statuses) . ' was accepted');
    } catch (RuntimeException $error) {
        if (!str_starts_with($error->getMessage(), "LCP HTTP {$statuses[0]}")) throw $error;
    }
    if (($count = $hits()) !== $want) throw new RuntimeException(json_encode($statuses) . " hits $count, want $want");
}
foreach ([['0', 1, 0], ['7', 0, 7], [null, 0, 1], ['Wed, 21 Oct 2026 07:28:00 GMT', 1, 2], ['1.5', 2, 4]] as [$retryAfter, $attempt, $want]) {
    if (($got = Lcp\Client::retryDelay($retryAfter, $attempt)) !== $want) throw new RuntimeException("retryDelay($retryAfter, $attempt) = $got, want $want");
}
echo "PHP SDK retry parity passed\n";
