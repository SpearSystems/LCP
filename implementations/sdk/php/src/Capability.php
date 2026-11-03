<?php
declare(strict_types=1);

namespace Lcp;

/**
 * Receiver capability helpers for v1.1 slot fields (SPEC.md §8, LEP-0003).
 *
 * The ping/bid/post payload objects are closed (additionalProperties: false),
 * so a schema-validating v1.0 receiver REJECTS slot fields rather than
 * ignoring them. Senders MUST only include them toward a receiver whose §8
 * lcp_versions advertisement parses to a full major.minor.patch element with
 * major == 1 and minor >= 1 — compared as parsed versions, never as a string
 * literal or prefix.
 */
final class Capability
{
    /** Payload fields introduced at v1.1 that require a v1.1 advertisement. */
    public const SLOT_FIELDS = ['slots_available', 'slots_claimed', 'contacted_hashes'];

    private const MIN_SLOT_MINOR = 1;

    /**
     * §8 version-string membership: the gate opens only for a parseable full
     * semver element with major == 1 and minor >= 1. Malformed or partial
     * elements ("1.1", "v1.1.0", "1.1.0.1") never open the gate.
     */
    public static function receiverAdvertisesSlotFields(array $advertisedLcpVersions): bool
    {
        foreach ($advertisedLcpVersions as $element) {
            if (!is_string($element) || !preg_match('/^\d+\.\d+\.\d+$/', $element)) continue;
            [$major, $minor] = array_map('intval', explode('.', $element));
            if ($major === 1 && $minor >= self::MIN_SLOT_MINOR) return true;
        }
        return false;
    }

    /** Slot fields carried by an envelope payload (incl. CONTACTED report details). */
    public static function slotFieldsPresent(mixed $payload): array
    {
        if (!is_array($payload)) return [];
        $present = array_values(array_filter(self::SLOT_FIELDS, static fn (string $field): bool => array_key_exists($field, $payload)));
        if (($payload['event'] ?? null) === 'CONTACTED' && is_array($payload['details'] ?? null) && array_key_exists('contacted_hashes', $payload['details'])) {
            $present[] = 'details.contacted_hashes';
        }
        return $present;
    }

    /**
     * Errors when an envelope carries v1.1 slot fields but the receiver's
     * advertisement does not support them (§8 gate; invalid example D). An
     * empty list is capability-compatibility, not a schema verdict.
     */
    public static function slotCapabilityErrors(array $envelope, array $advertisedLcpVersions): array
    {
        $present = self::slotFieldsPresent($envelope['lcp']['payload'] ?? null);
        if ($present === [] || self::receiverAdvertisesSlotFields($advertisedLcpVersions)) return [];
        return array_map(
            static fn (string $field): string => "$field requires the receiver to advertise a full-semver LCP version with major 1 and minor >= " . self::MIN_SLOT_MINOR . ' (SPEC.md §8 capability gate)',
            $present,
        );
    }
}
