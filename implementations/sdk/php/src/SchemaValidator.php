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

    /** @param array<string, string> $schemaTexts */
    public function __construct(array $schemaTexts)
    {
        $this->validator = new CompliantValidator();
        $resolver = $this->validator->resolver();
        foreach ($schemaTexts as $name => $text) {
            $schema = json_decode($text, false, 512, JSON_THROW_ON_ERROR);
            $id = is_object($schema) && isset($schema->{'$id'}) ? (string)$schema->{'$id'} : $name;
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
        $this->validate("schemas/$type.json", $envelope['lcp']['payload'] ?? null);
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
