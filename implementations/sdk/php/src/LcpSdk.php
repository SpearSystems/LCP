<?php

declare(strict_types=1);

namespace Lcp;

require_once __DIR__ . '/Capability.php';
require_once __DIR__ . '/SchemaValidator.php';
require_once __DIR__ . '/GeneratedModels.php';

use DateTimeImmutable;
use DateTimeZone;
use InvalidArgumentException;
use RuntimeException;

final class Signing
{
    public static function canonicalBytes(string $timestamp, ?string $idempotencyKey, string $body): string
    {
        return $timestamp . "\n" . ($idempotencyKey ?? '') . "\n" . $body;
    }

    public static function sign(string $secret, string $timestamp, ?string $idempotencyKey, string $body): string
    {
        return self::hmacHex($secret, self::canonicalBytes($timestamp, $idempotencyKey, $body));
    }

    /** The single HMAC-SHA256 -> lowercase-hex construction in this SDK (SPEC.md §9). */
    private static function hmacHex(string $secret, string $data): string
    {
        return hash_hmac('sha256', $data, $secret);
    }

    /**
     * SPEC.md §9 pinned contact preimage. Phone: the wire value verbatim,
     * byte-for-byte — no folding, stripping, or reformatting (the reference
     * platform's phone_hash builder is the executed baseline). Email: the §9
     * pinned candidate canonical — trim surrounding whitespace, ASCII-lowercase
     * the domain, keep the local-part bytes verbatim, plus-addresses distinct.
     * Email-form contacted_hashes ENTRIES stay emission-gated until #153 T9
     * executed vectors publish; this is the pinned compute rule every
     * contact-hash vector row reproduces under.
     */
    public static function normalizeContact(string $value, string $domain = 'phone'): string
    {
        if ($domain === 'phone') return $value;
        if ($domain === 'email') {
            $trimmed = trim($value);
            $separator = strrpos($trimmed, '@');
            if ($separator === false) throw new RuntimeException("email contact preimage must contain '@'");
            $local = substr($trimmed, 0, $separator);
            $host = substr($trimmed, $separator + 1);
            $asciiLower = preg_replace_callback('/[A-Z]/', static fn (array $m): string => chr(ord($m[0]) + 32), $host);
            return $local . '@' . $asciiLower;
        }
        throw new RuntimeException("unknown contact domain '$domain' (expected phone or email)");
    }

    /**
     * Per-pair contact hash (SPEC.md §9, LEP-0003 §3): lowercase-hex
     * HMAC-SHA256(pair_secret, normalized_preimage) keyed with the secret
     * shared with the message's receiver. phone_hash, email_hash, and
     * contacted_hashes entries all ride this one construction — the same
     * digest path as sign(), never an unsalted digest, never SHA-1.
     */
    public static function contactHash(string $secret, string $value, string $domain = 'phone'): string
    {
        return self::hmacHex($secret, self::normalizeContact($value, $domain));
    }

    public static function verify(string $secret, string $signature, string $timestamp, ?string $idempotencyKey, string $body, int $maxSkewSeconds = 300, ?DateTimeImmutable $now = null): void
    {
        try { $signedAt = new DateTimeImmutable($timestamp); } catch (\Exception $error) { throw new RuntimeException('Invalid X-LCP-Timestamp', 0, $error); }
        $current = $now ?? new DateTimeImmutable('now', new DateTimeZone('UTC'));
        if (abs($current->getTimestamp() - $signedAt->getTimestamp()) > $maxSkewSeconds) throw new RuntimeException('X-LCP-Timestamp is outside the replay window');
        if (!hash_equals(self::sign($secret, $timestamp, $idempotencyKey, $body), strtolower($signature))) throw new RuntimeException('Invalid X-LCP-Signature');
    }

    public static function verifyRequest(string $secret, array $headers, string $rawBody, int $maxSkewSeconds = 300): void
    {
        $normalized = [];
        foreach ($headers as $key => $value) $normalized[strtolower($key)] = (string)$value;
        foreach (['x-lcp-signature', 'x-lcp-timestamp', 'x-lcp-idempotency-key'] as $required) if (empty($normalized[$required])) throw new RuntimeException("Missing required LCP header: $required");
        self::verify($secret, $normalized['x-lcp-signature'], $normalized['x-lcp-timestamp'], $normalized['x-lcp-idempotency-key'], $rawBody, $maxSkewSeconds);
    }
}

final class Envelope
{
    public static function build(string $type, string $senderId, string $receiverId, array $payload, bool $test = false): array
    {
        $id = self::uuid();
        return ['lcp' => ['version' => '1.0.0', 'message' => ['id' => $id, 'type' => $type, 'timestamp' => gmdate('Y-m-d\\TH:i:s\\Z'), 'sender_id' => $senderId, 'receiver_id' => $receiverId, 'correlation_id' => null, 'idempotency_key' => "$senderId-$type-" . self::uuid(), 'test' => $test], 'payload' => $payload]];
    }

    public static function validate(array $envelope): void
    {
        $message = $envelope['lcp']['message'] ?? null;
        foreach (['version' => $envelope['lcp']['version'] ?? null, 'id' => $message['id'] ?? null, 'type' => $message['type'] ?? null, 'timestamp' => $message['timestamp'] ?? null, 'sender_id' => $message['sender_id'] ?? null, 'receiver_id' => $message['receiver_id'] ?? null, 'idempotency_key' => $message['idempotency_key'] ?? null] as $field => $value) if (!is_string($value) || $value === '') throw new RuntimeException("Missing LCP field: $field");
        if (!array_key_exists('payload', $envelope['lcp'])) throw new RuntimeException('Missing lcp.payload');
    }

    private static function uuid(): string
    {
        $bytes = random_bytes(16); $bytes[6] = chr((ord($bytes[6]) & 0x0f) | 0x40); $bytes[8] = chr((ord($bytes[8]) & 0x3f) | 0x80);
        return vsprintf('%s%s-%s-%s-%s-%s%s%s', str_split(bin2hex($bytes), 4));
    }
}

final class Client
{
    public function __construct(private readonly string $endpoint, private readonly ?string $senderId = null, private readonly ?string $apiKey = null, private readonly ?string $hmacSecret = null, private readonly int $maxRetries = 2) {}

    public function request(string $method, string $path, ?array $payload = null, bool $test = false, ?int $maxRetries = null): array
    {
        $body = $payload === null ? '' : json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
        $key = $payload['lcp']['message']['idempotency_key'] ?? null;
        $retries = max(0, $maxRetries ?? $this->maxRetries);
        for ($attempt = 0; $attempt <= $retries; $attempt++) {
            $headers = ['Content-Type: application/json', 'Accept: application/json']; if ($this->senderId) $headers[] = 'X-LCP-Sender-Id: ' . $this->senderId; if ($this->apiKey) $headers[] = 'Authorization: Bearer ' . $this->apiKey; if ($key) $headers[] = 'X-LCP-Idempotency-Key: ' . $key; if ($test) $headers[] = 'X-LCP-Test: true';
            if ($this->hmacSecret) { $timestamp = gmdate('Y-m-d\\TH:i:s\\Z'); $headers[] = 'X-LCP-Timestamp: ' . $timestamp; $headers[] = 'X-LCP-Signature: ' . Signing::sign($this->hmacSecret, $timestamp, $key, $body); }
            $handle = curl_init(rtrim($this->endpoint, '/') . '/' . ltrim($path, '/')); curl_setopt_array($handle, [CURLOPT_CUSTOMREQUEST => $method, CURLOPT_POSTFIELDS => $body === '' ? null : $body, CURLOPT_HTTPHEADER => $headers, CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 30]); $responseBody = curl_exec($handle); $error = curl_error($handle); $status = (int)curl_getinfo($handle, CURLINFO_RESPONSE_CODE); curl_close($handle);
            if ($error !== '' && $attempt < $retries) { sleep(2 ** $attempt); continue; } if ($error !== '') throw new RuntimeException($error);
            $result = json_decode((string)$responseBody, true) ?? ['raw' => $responseBody]; if ($status >= 200 && $status < 300) return $result; if (!in_array($status, [429, 500, 502, 503, 504], true) || $attempt === $retries) throw new RuntimeException("LCP HTTP $status: " . json_encode($result)); sleep(2 ** $attempt);
        }
        throw new RuntimeException('LCP request failed');
    }

    public function submitLead(array $envelope, bool $test = false): array { Envelope::validate($envelope); return $this->request('POST', '/v1/lcp/leads', $envelope, $test); }
    public function submitCall(array $envelope, bool $test = false): array { Envelope::validate($envelope); return $this->request('POST', '/v1/lcp/calls', $envelope, $test); }
    public function submitBid(array $envelope, bool $test = false): array { Envelope::validate($envelope); return $this->request('POST', '/v1/lcp/bids', $envelope, $test); }

    /**
     * Submit up to 100 lead items in one request (LEP-0001 W2-01) and return the
     * per-item receipts in input order. HTTP 207 partial success is a successful
     * response; per-item `error` objects are preserved. The batch is never
     * retried as a whole — retrying only rejected/error receipts is the
     * caller's job.
     */
    public function submitLeadsBatch(array $items, ?string $requestId = null): array
    {
        if (count($items) < 1 || count($items) > 100) throw new InvalidArgumentException('batch supports 1..100 items, got ' . count($items));
        $response = $this->request('POST', '/v1/lcp/leads/batch', ['request_id' => $requestId ?? bin2hex(random_bytes(16)), 'items' => array_values($items)], false, 0);
        $results = $response['results'] ?? null;
        if (!is_array($results)) throw new RuntimeException('batch response missing results');
        $order = [];
        foreach ($items as $index => $item) $order[$item['item_id'] ?? ''] = $index;
        usort($results, static fn (array $left, array $right): int => ($order[$left['item_id'] ?? ''] ?? count($items)) <=> ($order[$right['item_id'] ?? ''] ?? count($items)));
        return $results;
    }
    public function queryLeadStatus(string $id): array { return $this->request('GET', '/v1/lcp/leads/' . rawurlencode($id)); }
    public function getCapabilities(): array { return $this->request('GET', '/v1/lcp/capabilities'); }
    public function listOffers(?string $vertical = null): array { return $this->request('GET', '/v1/lcp/offers' . ($vertical === null ? '' : '?vertical=' . rawurlencode($vertical))); }

    /**
     * LEP-0005 request-history read (`GET /v1/lcp/history/requests`,
     * `history:read`). Only supplied, non-empty filters reach the query string:
     * a blank filter would widen the page instead of narrowing it. Rows are
     * scoped server-side to the credential's tenant, so there is no tenant
     * argument here. The decoded body comes back unchanged (`requests`).
     */
    public function requestHistory(array $params = []): array
    {
        $query = [];
        foreach (['limit', 'cursor', 'request_id', 'status', 'vertical', 'caller_phone_hash', 'from', 'to'] as $name) {
            $value = $params[$name] ?? null;
            if ($value === null || $value === '') continue;
            $query[] = $name . '=' . rawurlencode((string) $value);
        }
        return $this->request('GET', '/v1/lcp/history/requests' . ($query === [] ? '' : '?' . implode('&', $query)));
    }

    /**
     * Fetch one protocol schema by name (`GET /v1/lcp/schemas/{name}`) and
     * return the decoded body unchanged. Slash namespaces such as
     * `verticals/mortgage` are legitimate, so each segment is encoded
     * separately and the separators are kept.
     */
    public function getSchema(string $name): array
    {
        return $this->request('GET', '/v1/lcp/schemas/' . implode('/', array_map('rawurlencode', explode('/', $name))));
    }
}
