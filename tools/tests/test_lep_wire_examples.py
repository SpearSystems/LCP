"""T1 (``tool: test_lep_wire_examples``) - the LEP-0003 F1 recurrence gate.

"Every fenced ``### Valid example`` block in ``governance/LEPs/*.md`` must
validate through the platform's own ``SchemaValidator`` (#153 T1,
``tool: test_lep_wire_examples``) so the F1 class of defect cannot silently
return" (ARB L-Platform dossier disposition, LEP-0003 "Conformance vectors").

The second gate here is the re-dossier's RF-1 conversion
(``tool: test_lep_wire_examples_digest_shape``, #160): for every fenced Valid
example, each string element of ``payload.details.contacted_hashes`` (and
``payload.contacted_hashes``) must match ``^[a-f0-9]{64}$``. The
``SchemaValidator`` lane alone CANNOT catch that defect class - event
``details`` is open (``schemas/event.json``), so a 63-hex digest validates
while failing the record's own §4 ingest MUST. The shape check therefore runs
on the parsed JSON directly, and a synthetic 63-hex self-test proves the
gate is not vacuous (a checker that cannot fail on the RF-1 shape is itself
the F1/RF-1 failure mode).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "implementations" / "reference-platform"))

from lcp_platform.validation import SchemaValidator  # noqa: E402

LEP_DIR = ROOT / "governance" / "LEPs"
HEADING_RE = re.compile(r"^### Valid example\b")
FENCE_RE = re.compile(r"^```json\s*$")
CONTACTED_HASH_RE = re.compile(r"\A[a-f0-9]{64}\Z")


def _valid_examples() -> list[tuple[str, str, dict]]:
    """(file, heading, parsed envelope) for every Valid example block.

    Each ``### Valid example`` heading (up to the next ``###`` heading) must
    carry exactly one fenced ```json block that parses as a full LCP
    envelope; a heading with no parsable envelope is a gate failure, not a
    skip - silently missing coverage is how F1 class defects return.
    """
    found: list[tuple[str, str, dict]] = []
    for path in sorted(LEP_DIR.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        index = 0
        while index < len(lines):
            if not HEADING_RE.match(lines[index]):
                index += 1
                continue
            heading = lines[index].removeprefix("### ").strip()
            index += 1
            block_lines: list[str] = []
            in_block = False
            closed = False
            while index < len(lines) and not lines[index].startswith("### "):
                line = lines[index]
                if not in_block and FENCE_RE.match(line):
                    in_block = True
                elif in_block and line.strip() == "```":
                    closed = True
                    index += 1
                    break
                elif in_block:
                    block_lines.append(line)
                index += 1
            if not closed:
                raise AssertionError(
                    f"{path.name}: heading {heading!r} has no closed ```json fence"
                )
            try:
                envelope = json.loads("\n".join(block_lines))
            except json.JSONDecodeError as exc:
                raise AssertionError(
                    f"{path.name}: heading {heading!r} holds invalid JSON: {exc}"
                ) from exc
            found.append((path.name, heading, envelope))
    return found


class LepWireExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.examples = _valid_examples()
        cls.validator = SchemaValidator(ROOT / "schemas")

    def test_lep_wire_examples(self) -> None:
        """Every Valid example validates through the platform's own validator.

        LEP-0003 F1 disposition: the drafted example must pass the same
        closed-object + ping-safe-vertical layer a conformant receiver runs
        (``schemas/ping.json`` allowlist, vertical ``ping_safe`` tagging).
        """
        self.assertGreaterEqual(
            len(self.examples),
            4,
            "expected at least the four LEP-0003 Valid examples; a heading that"
            " stopped yielding a fenced json envelope silently disabled the gate",
        )
        for name, heading, envelope in self.examples:
            with self.subTest(example=f"{name}: {heading}"):
                errors = self.validator.validate_envelope(envelope)
                self.assertEqual(errors, [], f"{name}: {heading} -> {errors[:3]}")

    def test_lep_wire_examples_digest_shape(self) -> None:
        """RF-1: contacted_hashes entries are lowercase 64-hex, shape-checked.

        Length-64 is asserted explicitly: the re-dossier found a 63-hex
        digest that the schema lane passed (open ``event.details``), so the
        gate must be able to fail on exactly that shape.
        """
        self.assertGreaterEqual(len(self.examples), 4)
        hashed = 0
        for name, heading, envelope in self.examples:
            payload = envelope.get("lcp", {}).get("payload", {})
            lists = []
            direct = payload.get("contacted_hashes")
            if isinstance(direct, list):
                lists.append(("payload.contacted_hashes", direct))
            details = payload.get("details")
            if isinstance(details, dict) and isinstance(
                details.get("contacted_hashes"), list
            ):
                lists.append(
                    ("payload.details.contacted_hashes", details["contacted_hashes"])
                )
            for field, entries in lists:
                hashed += 1
                with self.subTest(example=f"{name}: {heading}", field=field):
                    for entry in entries:
                        self.assertIsInstance(
                            entry, str, f"{field}: non-string digest entry"
                        )
                        self.assertEqual(
                            len(entry),
                            64,
                            f"{field}: digest length must be 64 (RF-1 caught a 63-hex value)",
                        )
                        self.assertIsNotNone(
                            CONTACTED_HASH_RE.match(entry),
                            f"{field}: {entry!r} fails ^[a-f0-9]{{64}}$",
                        )
        self.assertGreaterEqual(
            hashed,
            2,
            "the post and CONTACTED examples must both carry contacted_hashes;"
            " if either stopped doing so, this gate went vacuous",
        )

    def test_digest_shape_gate_catches_the_rf1_shape(self) -> None:
        """The shape check must FAIL a 63-hex digest (planted-bug proof).

        Proves the RF-1 conversion has teeth: run the same matcher the walk
        applies to a crafted payload carrying the re-dossier's exact defect
        shape (a 63-character lowercase hex string, not padded).
        """
        rf1_shape = "5a1c9e3b7d0f2a6c8e4b1d9f3a7c5e2b8d4f6a0c1e3b5d7f9a2c4e6b8d0f1a3"
        self.assertEqual(len(rf1_shape), 63)
        crafted = {"lcp": {"payload": {"details": {"contacted_hashes": [rf1_shape]}}}}
        entries = crafted["lcp"]["payload"]["details"]["contacted_hashes"]
        offenders = [
            e for e in entries if not CONTACTED_HASH_RE.match(e) or len(e) != 64
        ]
        self.assertEqual(
            offenders,
            [rf1_shape],
            "the digest-shape gate must reject the 63-hex RF-1 shape",
        )


if __name__ == "__main__":
    unittest.main()
