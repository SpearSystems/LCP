from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from check_lep_registry import validate_lep_registry  # noqa: E402


REQUIRED_SECTIONS = """## Problem statement and evidence
## Scope
## Proposed wire shape
## Privacy, security, and universal-core audit
## Compatibility, versioning, and deprecation impact
## SDK, OpenAPI, MCP, conformance, and operational impact
## Alternatives considered
## Rollout, migration, observability, and rollback
## Deferred questions
"""


def write_fixture(root: Path, *, registry_row: str, document: str | None = None) -> None:
    (root / "governance" / "LEPs").mkdir(parents=True)
    (root / "governance" / "LEP-REGISTRY.md").write_text(
        "# Registry\n\n"
        "| LEP | Title | Status | Author(s) | Opened | Review deadline | Decision/date | Target version | Proposal | Implementation / traceability |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
        + registry_row
        + "\n",
        encoding="utf-8",
    )
    if document is not None:
        (root / "governance" / "LEPs" / "LEP-0001-example.md").write_text(
            document, encoding="utf-8"
        )


class LepRegistryTests(unittest.TestCase):
    def test_repository_registry_is_valid(self) -> None:
        self.assertEqual(validate_lep_registry(), [])

    def test_missing_registry_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            problems = validate_lep_registry(Path(tmp))
        self.assertTrue(any("missing registry" in problem for problem in problems))

    def test_registry_requires_a_proposal_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(
                root,
                registry_row="| LEP-0001 | Example | Draft | Maintainer | 2026-08-18 | — | — | v1.1.0 | [proposal](LEPs/LEP-0001-missing.md) | Not implemented. |",
            )
            problems = validate_lep_registry(root)
        self.assertTrue(any("proposal file is missing" in problem for problem in problems))

    def test_registry_rejects_proposals_outside_the_numbered_directory(self) -> None:
        document = """# LEP-0001 — Example

- **Title:** Example
- **Author(s):** Maintainer
- **Date:** 2026-08-18
- **Status:** **Draft** — not accepted
- **Target version:** v1.1.0

""" + REQUIRED_SECTIONS
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(document, encoding="utf-8")
            write_fixture(
                root,
                registry_row="| LEP-0001 | Example | Draft | Maintainer | 2026-08-18 | — | — | v1.1.0 | [proposal](../README.md) | Not implemented. |",
            )
            problems = validate_lep_registry(root)
        self.assertTrue(any("must target governance/LEPs" in problem for problem in problems))

    def test_document_metadata_and_sections_are_checked(self) -> None:
        document = """# LEP-0001 — Example

- **Title:** Example
- **Author(s):** Maintainer
- **Date:** 2026-08-18
- **Status:** **Draft** — not accepted
- **Target version:** v1.1.0

""" + REQUIRED_SECTIONS
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(
                root,
                registry_row="| LEP-0001 | Example | Draft | Maintainer | 2026-08-18 | — | — | v1.1.0 | [proposal](LEPs/LEP-0001-example.md) | Not implemented. |",
                document=document,
            )
            self.assertEqual(validate_lep_registry(root), [])

    def test_status_mismatch_is_reported(self) -> None:
        document = """# LEP-0001 — Example

- **Title:** Example
- **Author(s):** Maintainer
- **Date:** 2026-08-18
- **Status:** **Review**
- **Target version:** v1.1.0

""" + REQUIRED_SECTIONS
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(
                root,
                registry_row="| LEP-0001 | Example | Draft | Maintainer | 2026-08-18 | — | — | v1.1.0 | [proposal](LEPs/LEP-0001-example.md) | Not implemented. |",
                document=document,
            )
            problems = validate_lep_registry(root)
        self.assertTrue(any("does not match registry" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()
