<?php

declare(strict_types=1);

namespace Lcp;

require_once __DIR__ . '/SchemaValidator.php';
require_once __DIR__ . '/GeneratedModels.php';

use DateTimeImmutable;
use DateTimeZone;
use RuntimeException;

final class Signing
{
    public static function canonicalBytes(string $timestamp, ?string $idempotencyKey, string $body): string
    {
        return $timestamp . "\n" . ($idempotencyKey ?? '') . "\n" . $body;
    }

    public static function sign(string $secret, string $timestamp, ?string $idempotencyKey, string $body): string
    {
        return hash_hmac('sha256', self::canonicalBytes($timestamp, $idempotencyKey, $body), $secret);
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

    public function request(string $method, string $path, ?array $payload = null, bool $test = false): array
    {
        $body = $payload === null ? '' : json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
        $key = $payload['lcp']['message']['idempotency_key'] ?? null;
        for ($attempt = 0; $attempt <= max(0, $this->maxRetries); $attempt++) {
            $headers = ['Content-Type: application/json', 'Accept: application/json']; if ($this->senderId) $headers[] = 'X-LCP-Sender-Id: ' . $this->senderId; if ($this->apiKey) $headers[] = 'Authorization: Bearer ' . $this->apiKey; if ($key) $headers[] = 'X-LCP-Idempotency-Key: ' . $key; if ($test) $headers[] = 'X-LCP-Test: true';
            if ($this->hmacSecret) { $timestamp = gmdate('Y-m-d\\TH:i:s\\Z'); $headers[] = 'X-LCP-Timestamp: ' . $timestamp; $headers[] = 'X-LCP-Signature: ' . Signing::sign($this->hmacSecret, $timestamp, $key, $body); }
            $handle = curl_init(rtrim($this->endpoint, '/') . '/' . ltrim($path, '/')); curl_setopt_array($handle, [CURLOPT_CUSTOMREQUEST => $method, CURLOPT_POSTFIELDS => $body === '' ? null : $body, CURLOPT_HTTPHEADER => $headers, CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 30]); $responseBody = curl_exec($handle); $error = curl_error($handle); $status = (int)curl_getinfo($handle, CURLINFO_RESPONSE_CODE); curl_close($handle);
            if ($error !== '' && $attempt < $this->maxRetries) { sleep(2 ** $attempt); continue; } if ($error !== '') throw new RuntimeException($error);
            $result = json_decode((string)$responseBody, true) ?? ['raw' => $responseBody]; if ($status >= 200 && $status < 300) return $result; if (!in_array($status, [429, 500, 502, 503, 504], true) || $attempt === $this->maxRetries) throw new RuntimeException("LCP HTTP $status: " . json_encode($result)); sleep(2 ** $attempt);
        }
        throw new RuntimeException('LCP request failed');
    }

    public function submitLead(array $envelope, bool $test = false): array { Envelope::validate($envelope); return $this->request('POST', '/v1/lcp/leads', $envelope, $test); }
    public function submitCall(array $envelope, bool $test = false): array { Envelope::validate($envelope); return $this->request('POST', '/v1/lcp/calls', $envelope, $test); }
    public function submitBid(array $envelope, bool $test = false): array { Envelope::validate($envelope); return $this->request('POST', '/v1/lcp/bids', $envelope, $test); }
    public function queryLeadStatus(string $id): array { return $this->request('GET', '/v1/lcp/leads/' . rawurlencode($id)); }
    public function getCapabilities(): array { return $this->request('GET', '/v1/lcp/capabilities'); }
    public function listOffers(?string $vertical = null): array { return $this->request('GET', '/v1/lcp/offers' . ($vertical === null ? '' : '?vertical=' . rawurlencode($vertical))); }
}
