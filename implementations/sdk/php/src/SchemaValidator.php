<?php

declare(strict_types=1);

namespace Lcp;

use Opis\JsonSchema\CompliantValidator;
use RuntimeException;

final class SchemaValidator
{
    private CompliantValidator $validator;
    /** @var array<string, string> */
    private array $idsByName = [];
    /** @var array<string, array<string, mixed>> */
    private array $documents = [];

    /** @param array<string, string> $schemaTexts */
    public function __construct(array $schemaTexts)
    {
        $this->validator = new CompliantValidator();
        $resolver = $this->validator->resolver();
        foreach ($schemaTexts as $name => $text) {
            $schema = json_decode($text, true, 512, JSON_THROW_ON_ERROR);
            $id = is_array($schema) && isset($schema['$id']) ? (string)$schema['$id'] : $name;
            $this->documents[$this->normalize($name)] = $schema;
            $this->documents[$this->normalize($id)] = $schema;
            $resolver->registerRaw($text, $id);
            $this->idsByName[$this->normalize($name)] = $id;
            $this->idsByName[$this->normalize($id)] = $id;
        }
    }

    public static function fromDirectory(string $root): self
    {
        $schemas = [];
        $iterator = new \RecursiveIteratorIterator(new \RecursiveDirectoryIterator($root));
        foreach ($iterator as $file) {
            if (!$file->isFile() || $file->getExtension() !== 'json') continue;
            $schemas[str_replace('\\', '/', substr($file->getPathname(), strlen(rtrim($root, DIRECTORY_SEPARATOR)) + 1))] = file_get_contents($file->getPathname());
        }
        if (basename(rtrim($root, DIRECTORY_SEPARATOR)) === 'schemas') {
            $verticalRoot = dirname(rtrim($root, DIRECTORY_SEPARATOR)) . DIRECTORY_SEPARATOR . 'verticals';
            if (is_dir($verticalRoot)) {
                $iterator = new \RecursiveIteratorIterator(new \RecursiveDirectoryIterator($verticalRoot));
                foreach ($iterator as $file) {
                    if (!$file->isFile() || $file->getExtension() !== 'json') continue;
                    $relative = str_replace('\\', '/', substr($file->getPathname(), strlen(rtrim($verticalRoot, DIRECTORY_SEPARATOR)) + 1));
                    $schemas['verticals/' . $relative] = file_get_contents($file->getPathname());
                }
            }
        }
        return new self($schemas);
    }

    public function validate(string $schemaName, mixed $document): void
    {
        $id = $this->idsByName[$this->normalize($schemaName)] ?? null;
        if ($id === null) throw new RuntimeException("Unknown LCP schema: $schemaName");
        $result = $this->validator->validate($this->jsonValue($document), $id);
        if (!$result->isValid()) throw new RuntimeException("LCP schema validation failed for $schemaName");
    }

    public function validateEnvelope(array $envelope): void
    {
        $this->validate('schemas/envelope.json', $envelope);
        $type = $envelope['lcp']['message']['type'] ?? null;
        if (!is_string($type)) throw new RuntimeException('Missing lcp.message.type');
        $payload = $envelope['lcp']['payload'] ?? null;
        $this->validate("schemas/$type.json", $payload);
        $this->validateVerticalPolicy($type, $payload);
    }

    private function validateVerticalPolicy(string $type, mixed $payload): void
    {
        if (!in_array($type, ['lead', 'call', 'post', 'ping'], true) || !is_array($payload)) return;
        $attributes = $payload['attributes'] ?? null;
        if (!is_array($attributes)) return;
        $vertical = $type === 'ping' ? ($payload['vertical'] ?? null) : ($attributes['vertical'] ?? null);
        if (!is_string($vertical) || $vertical === '') return;
        $schema = $this->documents[$this->normalize("verticals/$vertical.json")] ?? null;
        if (!is_array($schema)) throw new RuntimeException("Vertical schema '$vertical' not found");
        $verticalAttributes = $attributes;
        if ($type === 'ping') {
            $verticalAttributes['vertical'] ??= $vertical;
            $verticalAttributes['schema_version'] ??= $schema['properties']['schema_version']['const'] ?? '1.0.0';
        }
        $this->validateVertical($vertical, $verticalAttributes);
        if ($type === 'ping') $this->validatePingSafe($vertical, $attributes, $schema, 'attributes');
    }

    /** @param array<string, mixed> $value */
    private function validatePingSafe(string $vertical, array $value, array $schema, string $path): void
    {
        $properties = $schema['properties'] ?? [];
        foreach ($value as $name => $child) {
            if ($path === 'attributes' && in_array($name, ['vertical', 'schema_version'], true)) continue;
            $definition = $properties[$name] ?? null;
            if (!is_array($definition) || ($definition['ping_safe'] ?? false) !== true) {
                throw new RuntimeException("$path.$name is not tagged ping_safe: true in vertical '$vertical'");
            }
            if (is_array($child) && isset($definition['properties']) && is_array($definition['properties'])) {
                $this->validatePingSafe($vertical, $child, $definition, "$path.$name");
            }
        }
    }

    public function validateOffer(array $offer): void { $this->validate('schemas/offer.json', $offer); }
    public function validateVertical(string $vertical, array $attributes): void { $this->validate("verticals/$vertical.json", $attributes); }

    private function jsonValue(mixed $value): mixed
    {
        if (is_array($value)) {
            $converted = [];
            foreach ($value as $key => $item) $converted[$key] = $this->jsonValue($item);
            return array_is_list($value) ? $converted : (object)$converted;
        }
        return $value;
    }

    private function normalize(string $name): string
    {
        $name = ltrim(str_replace('\\', '/', $name), '/');
        $name = preg_replace('/^(schemas|verticals)\//', '', $name) ?? $name;
        return preg_replace('/\.json$/', '', $name) ?? $name;
    }
}
