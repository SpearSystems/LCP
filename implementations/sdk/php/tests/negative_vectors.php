<?php
// PHP SDK must refuse every attack in test-vectors/sdk/negative/.
//
// Loads the shared negative security-vector suite (format documented in
// test-vectors/sdk/negative/README.md), runs each vector through this SDK's real primitives
// (Lcp\Signing::verify, Lcp\SchemaValidator), and asserts the expected outcome. Deterministic:
// fixed clocks, repo schemas/, no network. A thrown exception is a reject; a clean return is an
// accept — this SDK does not surface protocol error codes, so per the runner contract the code is
// asserted only where a primitive would expose one (never).
//
// Gap vectors (sdk_exercisable: false) are reported loudly on stderr and never counted as executed
// — never a silent pass. Format lint runs first so a malformed vector fails the script even when
// behavior would accidentally match.
require __DIR__ . '/../vendor/autoload.php';
require __DIR__ . '/../src/LcpSdk.php';

$root = __DIR__ . '/../../../..';
$negativeDir = $root . '/test-vectors/sdk/negative';
$suiteName = 'lcp-sdk-negative';
$suiteVersion = 1;
$requiredKeys = ['id', 'invariant', 'spec_ref', 'description', 'applies_to', 'check', 'inputs', 'expect', 'sdk_exercisable'];
// SPEC.md §5 codes each check is allowed to expect on rejection.
$checkCodes = [
    'verify_hmac' => ['LCP-012'],
    'verify_raw_body' => ['LCP-012'],
    'envelope_validate' => ['LCP-006', 'LCP-008', 'LCP-100'],
    'offer_validate' => ['LCP-100'],
    'production_mode_verify' => ['LCP-013'],
];
// SPEC.md §2/§6 closed message.type enum (validates vector applies_to kinds).
$messageTypes = ['lead', 'call', 'ping', 'post', 'ack', 'event', 'bid'];

/** @return array<int, array{file: string, suite: array}> */
function loadSuites(string $negativeDir): array
{
    $files = glob($negativeDir . '/*.json');
    sort($files);
    if (!$files) {
        throw new RuntimeException("no vector files found under $negativeDir");
    }
    $suites = [];
    foreach ($files as $path) {
        $suites[] = ['file' => basename($path), 'suite' => json_decode(file_get_contents($path), true, 512, JSON_THROW_ON_ERROR)];
    }
    return $suites;
}

function iterVectors(array $suites): array
{
    $vectors = [];
    foreach ($suites as $suite) {
        foreach ($suite['suite']['vectors'] ?? [] as $vector) {
            $vectors[] = $vector;
        }
    }
    return $vectors;
}

// ---- Format lint (run first; a malformed vector fails CI regardless of behavior) ----
$problems = [];
$seenIds = [];
foreach (loadSuites($negativeDir) as $entry) {
    $source = $entry['file'];
    $suite = $entry['suite'];
    if (($suite['suite'] ?? null) !== $suiteName) $problems[] = "$source: suite must be '$suiteName'";
    if (($suite['version'] ?? null) !== $suiteVersion) $problems[] = "$source: unsupported suite version " . json_encode($suite['version'] ?? null);
    foreach (['group', 'description'] as $field) if (empty($suite[$field])) $problems[] = "$source: missing $field";
    if (empty($suite['vectors'])) $problems[] = "$source: empty vectors array";
    foreach ($suite['vectors'] ?? [] as $vector) {
        $id = $vector['id'] ?? '(no id)';
        $where = "$source:$id";
        foreach ($requiredKeys as $key) if (!array_key_exists($key, $vector)) $problems[] = "$where: missing field '$key'";
        if (!str_starts_with($id, 'lcp-neg-')) $problems[] = "$where: id must start with 'lcp-neg-'";
        if (in_array($id, $seenIds, true)) $problems[] = "$where: duplicate vector id";
        $seenIds[] = $id;
        $invariant = $vector['invariant'] ?? null;
        if ($invariant !== null && !(is_int($invariant) && $invariant >= 1 && $invariant <= 8)) $problems[] = "$where: invariant must be 1-8 or null";
        if (empty($vector['spec_ref'])) $problems[] = "$where: spec_ref required (even with invariant set)";
        $appliesTo = $vector['applies_to'] ?? null;
        if (!is_array($appliesTo) || !$appliesTo) {
            $problems[] = "$where: applies_to must be a non-empty list";
        } else {
            foreach ($appliesTo as $kind) {
                if (!is_string($kind)) { $problems[] = "$where: applies_to kind must be a string"; continue; }
                if ($kind !== '*' && !in_array($kind, $messageTypes, true)) $problems[] = "$where: applies_to has unknown kind '$kind'";
            }
        }
        $check = $vector['check'] ?? '';
        if (!array_key_exists($check, $checkCodes)) { $problems[] = "$where: unknown check '$check'"; continue; }
        if (!is_array($vector['inputs'] ?? null)) $problems[] = "$where: inputs must be an object";
        $expect = $vector['expect'] ?? [];
        $outcome = $expect['outcome'] ?? null;
        if ($outcome !== 'reject' && $outcome !== 'accept') {
            $problems[] = "$where: expect.outcome must be reject|accept";
        } elseif ($outcome === 'reject') {
            if (!in_array($expect['error_code'] ?? null, $checkCodes[$check], true)) {
                $problems[] = "$where: reject expects error_code in [" . implode(', ', $checkCodes[$check]) . "], got " . json_encode($expect['error_code'] ?? null);
            }
            if (empty($expect['error_name'])) $problems[] = "$where: reject requires error_name";
        } elseif ($outcome === 'accept' && (isset($expect['error_code']) || isset($expect['error_name']))) {
            if ($expect['error_code'] !== null || $expect['error_name'] !== null) $problems[] = "$where: accept vectors must not declare an error";
        }
        if (($vector['sdk_exercisable'] ?? true) === false) {
            if (strlen($vector['gap_note'] ?? '') < 40) $problems[] = "$where: sdk_exercisable=false requires a substantive gap_note";
        } elseif (array_key_exists('gap_note', $vector)) {
            $problems[] = "$where: gap_note only allowed when sdk_exercisable is false";
        }
    }
}
if ($problems) {
    fwrite(STDERR, "Negative vector format violations:\n  " . implode("\n  ", $problems) . "\n");
    throw new RuntimeException('negative vector format violations: ' . count($problems));
}

// ---- Execution against the real SDK primitives ----
$validator = Lcp\SchemaValidator::fromDirectory($root . '/schemas');

/** @return array{0: string, 1: string} {outcome, detail} where outcome is accept|reject|gap|error */
function execute(Lcp\SchemaValidator $validator, array $vector): array
{
    if (($vector['sdk_exercisable'] ?? true) === false) {
        return ['gap', $vector['gap_note'] ?? ''];
    }
    $inputs = $vector['inputs'];
    $check = $vector['check'];
    try {
        switch ($check) {
            case 'verify_hmac':
            case 'verify_raw_body':
                $bodyField = $check === 'verify_hmac' ? 'body' : 'verify_body';
                Lcp\Signing::verify(
                    $inputs['secret'],
                    $inputs['signature'],
                    $inputs['timestamp'],
                    $inputs['idempotency_key'],
                    $inputs[$bodyField],
                    (int) $inputs['replay_window_seconds'],
                    new DateTimeImmutable($inputs['now'])
                );
                break;
            case 'envelope_validate':
                $validator->validateEnvelope($inputs['envelope']);
                break;
            case 'offer_validate':
                $validator->validateOffer($inputs['offer']);
                break;
            default:
                // Exercisable but no adapter in this runner: format lint passed, so the vector
                // expects an SDK surface this lane has not mapped. Fail loudly, never silently pass.
                return ['error', "no runner adapter for check '$check'"];
        }
        return ['accept', ''];
    } catch (Throwable $error) {
        return ['reject', get_class($error) . ': ' . $error->getMessage()];
    }
}

$mismatches = [];
$gaps = [];
$ran = 0;
foreach (iterVectors(loadSuites($negativeDir)) as $vector) {
    $id = $vector['id'];
    $check = $vector['check'];
    [$outcome, $detail] = execute($validator, $vector);
    if ($outcome === 'gap') {
        $gaps[] = "$id: $detail";
        continue;
    }
    $ran++;
    $expected = $vector['expect']['outcome'];
    if ($outcome !== $expected) {
        $mismatches[] = "$id ($check): expected $expected, got $outcome - " . ($detail !== '' ? $detail : 'no detail') . ' - ' . $vector['description'];
    }
}
foreach ($gaps as $gap) {
    fwrite(STDERR, "GAP (reported, never silently passed): $gap\n");
}
if ($ran === 0) {
    throw new RuntimeException('no negative vector was actually executed');
}
if ($mismatches) {
    fwrite(STDERR, "Negative vector outcome mismatches:\n  " . implode("\n  ", $mismatches) . "\n");
    throw new RuntimeException('negative vector outcome mismatches: ' . count($mismatches));
}
echo "PHP SDK negative security vectors passed ($ran executed, " . count($gaps) . " gap-reported)\n";
