// Receiver capability helpers for v1.1 slot fields (SPEC.md §8, LEP-0003).
//
// The v1.1 slot fields are capability-gated, never ignore-gated: ping/bid/post
// payloads are closed objects (additionalProperties: false), so a
// schema-validating v1.0 receiver REJECTS unknown slot fields rather than
// ignoring them. Senders MUST only include them toward a receiver whose §8
// lcp_versions advertisement parses to a full major.minor.patch element with
// major == 1 and minor >= 1 — compared as parsed versions, never as a string
// literal or prefix.

/** Payload fields introduced at v1.1 that require v1.1 receiver advertisement. */
export const SLOT_FIELDS = ["slots_available", "slots_claimed", "contacted_hashes"] as const;

const MIN_SLOT_MINOR = 1;

/**
 * §8 version-string membership: the gate opens only for a parseable full
 * semver element with major == 1 and minor >= 1. Malformed or partial
 * elements ("1.1", "v1.1.0", "1.1.0.1") never open the gate.
 */
export function receiverAdvertisesSlotFields(advertisedLcpVersions: readonly string[]): boolean {
 return advertisedLcpVersions.some((element) => {
  const parts = element.split(".");
  if (parts.length !== 3 || !parts.every((part) => /^\d+$/.test(part))) return false;
  return Number(parts[0]) === 1 && Number(parts[1]) >= MIN_SLOT_MINOR;
 });
}

/** Slot fields carried by an envelope's payload (incl. CONTACTED report details). */
export function slotFieldsPresent(payload: unknown): string[] {
 if (!payload || typeof payload !== "object" || Array.isArray(payload)) return [];
 const record = payload as Record<string, unknown>; // named structural cast: unknown is not indexable after the guard
 const present: string[] = SLOT_FIELDS.filter((field) => field in record);
 if (record.event === "CONTACTED") {
  const details = record.details;
  if (details && typeof details === "object" && "contacted_hashes" in details) {
   present.push("details.contacted_hashes");
  }
 }
 return present;
}

/**
 * Errors when an envelope carries v1.1 slot fields but the receiver's
 * advertisement does not support them (§8 gate; invalid example D). An empty
 * list is capability-compatibility, not a schema verdict.
 */
export function slotCapabilityErrors(
 envelope: unknown,
 advertisedLcpVersions: readonly string[],
): string[] {
 if (!envelope || typeof envelope !== "object" || !("lcp" in envelope)) return [];
 const lcp = envelope.lcp;
 if (!lcp || typeof lcp !== "object" || !("payload" in lcp)) return [];
 const present = slotFieldsPresent(lcp.payload);
 if (present.length === 0) return [];
 if (receiverAdvertisesSlotFields(advertisedLcpVersions)) return [];
 return present.map(
  (field) =>
   `${field} requires the receiver to advertise a full-semver LCP version with major 1 and minor >= ${MIN_SLOT_MINOR} (SPEC.md §8 capability gate)`,
 );
}
