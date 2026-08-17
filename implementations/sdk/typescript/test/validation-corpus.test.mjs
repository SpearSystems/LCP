import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { LcpSchemaValidator } from "../dist/index.js";

const repoRoot = fileURLToPath(new URL("../../../../", import.meta.url));
const corpus = JSON.parse(
  await readFile(new URL("../../../../test-vectors/sdk/validation-corpus.json", import.meta.url), "utf8"),
);
const validator = await LcpSchemaValidator.fromDirectory(repoRoot);
const failures = [];
for (const fixture of corpus.fixtures) {
  const isOffer = fixture.offer !== undefined;
  const document = isOffer ? fixture.offer : fixture.envelope;
  let passed = true;
  try {
    if (isOffer) {
      validator.validate("schemas/offer.json", document);
    } else {
      validator.validateEnvelope(document);
    }
  } catch {
    passed = false;
  }
  if (passed !== (fixture.expect === "pass")) {
    failures.push(`${fixture.id} (${fixture.rule}): expected ${fixture.expect}`);
  }
}
assert.deepEqual(failures, []);
console.log(`TypeScript SDK shared validation corpus passed (${corpus.fixtures.length} fixtures)`);
