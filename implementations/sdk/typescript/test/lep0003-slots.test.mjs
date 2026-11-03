import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import {
  LcpSchemaValidator,
  contactHash,
  normalizeContact,
  receiverAdvertisesSlotFields,
  slotCapabilityErrors,
} from "../dist/index.js";

const repoRoot = fileURLToPath(new URL("../../../..", import.meta.url));
const example = async (name) =>
  JSON.parse(await readFile(new URL(`../../../../examples/${name}`, import.meta.url), "utf8"));
const vectors = JSON.parse(
  await readFile(new URL("../../../../test-vectors/sdk/contact-hash.json", import.meta.url), "utf8"),
);

const failures = [];
for (const vector of vectors.vectors) {
  const observed = await contactHash(vector.secret, vector.preimage, vector.domain);
  if (observed !== vector.digest) {
    failures.push(
      `${vector.name} (${vector.domain}): preimage ${JSON.stringify(vector.preimage)} pinned ${vector.digest} but TypeScript SDK produced ${observed}`,
    );
  }
}
assert.deepEqual(failures, [], "contact-hash vector reproduction failed:\n" + failures.join("\n"));

// SPEC.md §9: phone preimages are the verbatim wire bytes; formatting noise changes the digest.
assert.equal(normalizeContact("+1 (512) 555-0147", "phone"), "+1 (512) 555-0147");
const pinnedEmail = "d28da9b5424990cf7ac6000cf4e04537ca487929e71cef12f91191f29f78a6c4";
assert.equal(await contactHash("pair-secret-for-vectors-0123456789", "  Jordan@Example.COM  ", "email"), pinnedEmail);
assert.notEqual(await contactHash("pair-secret-for-vectors-0123456789", "jordan@example.com", "email"), pinnedEmail);
assert.notEqual(await contactHash("pair-secret-for-vectors-0123456789", "Jordan+crm@example.com", "email"), pinnedEmail);

// §8 version-string membership: parsed full semver only, never literal or prefix.
assert.equal(receiverAdvertisesSlotFields(["1.0.0"]), false);
assert.equal(receiverAdvertisesSlotFields(["1.1.0"]), true);
assert.equal(receiverAdvertisesSlotFields(["1.0.0", "1.1.0"]), true);
assert.equal(receiverAdvertisesSlotFields(["1.10.0"]), true);
assert.equal(receiverAdvertisesSlotFields(["2.0.0"]), false);
assert.equal(receiverAdvertisesSlotFields(["1.1"]), false);
assert.equal(receiverAdvertisesSlotFields(["v1.1.0", "1.1.0.1", ""]), false);

const validator = await LcpSchemaValidator.fromDirectory(repoRoot);
const ping = await example("ping-slots.json");
const bid = await example("bid-slots.json");
const post = await example("post-contacted.json");
const event = await example("event-contacted.json");

// Slot-bearing ping/bid/post/event validate through the SDK schema path.
for (const envelope of [ping, bid, post, event]) validator.validateEnvelope(envelope);

// The capability gate rejects slot fields toward a receiver advertising v1.0 only…
assert.ok(slotCapabilityErrors(ping, ["1.0.0"]).some((error) => error.includes("slots_available")));
assert.deepEqual(slotCapabilityErrors(ping, ["1.1.0"]), []);
assert.throws(
  () => validator.validateEnvelope(ping, { receiverAdvertisedLcpVersions: ["1.0.0"] }),
  /capability gate/,
  "SDK validator must reject a slot-bearing ping to a non-advertising receiver",
);
// …and accepts it toward a v1.1 advertisement.
validator.validateEnvelope(ping, { receiverAdvertisedLcpVersions: ["1.1.0"] });
validator.validateEnvelope(post, { receiverAdvertisedLcpVersions: ["1.1.0"] });
assert.throws(() => validator.validateEnvelope(post, { receiverAdvertisedLcpVersions: ["1.0.0"] }), /capability gate/);
assert.throws(() => validator.validateEnvelope(event, { receiverAdvertisedLcpVersions: ["1.0.0"] }), /capability gate/);

console.log(`TypeScript SDK LEP-0003 contact-hash vectors passed (${vectors.vectors.length} rows reproduced byte-exactly)`);
console.log("TypeScript SDK LEP-0003 slot round-trip and capability gate passed");
