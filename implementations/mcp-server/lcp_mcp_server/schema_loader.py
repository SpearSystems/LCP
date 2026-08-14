"""Schema loader — reads JSON Schemas from the LCP repo.

When the REST endpoint is unavailable (e.g. local development), the MCP server
can serve schemas directly from the repo's schemas/ and verticals/ directories.

LCP — Lead Context Protocol. Created by Spear Systems (a Spear company).
Open standard — Apache 2.0, free to implement.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# implementations/mcp-server/ -> ../../schemas, ../../verticals
_REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMAS_DIR = _REPO_ROOT / "schemas"
VERTICALS_DIR = _REPO_ROOT / "verticals"


def list_schemas() -> list[str]:
    """List all available schema names."""
    names: list[str] = []
    if SCHEMAS_DIR.exists():
        names.extend(f.stem for f in SCHEMAS_DIR.glob("*.json"))
    if VERTICALS_DIR.exists():
        names.extend(f"verticals/{f.stem}" for f in VERTICALS_DIR.glob("*.json"))
    return sorted(names)


def load_schema(name: str) -> dict[str, Any] | None:
    """Load a schema by name. Returns None if not found.

    Core/message schemas: name -> schemas/{name}.json
    Vertical schemas: verticals/{name} -> verticals/{name}.json
    """
    if name.startswith("verticals/"):
        path = VERTICALS_DIR / f"{name.removeprefix('verticals/')}.json"
    else:
        path = SCHEMAS_DIR / f"{name}.json"

    if not path.exists():
        return None

    with open(path) as f:
        return json.load(f)