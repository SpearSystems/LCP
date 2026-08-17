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


def _safe_schema_path(base: Path, relative_name: str) -> Path | None:
    """Resolve a schema path without allowing traversal or symlink escapes."""
    if not relative_name or "\x00" in relative_name or "\\" in relative_name:
        return None
    relative = Path(relative_name)
    if relative.is_absolute():
        return None
    try:
        base_resolved = base.resolve()
        path = (base_resolved / relative).resolve()
    except OSError:
        return None
    if path.suffix.lower() != ".json" or base_resolved not in path.parents:
        return None
    return path if path.is_file() else None


def load_schema(name: str) -> dict[str, Any] | None:
    """Load a schema by name while enforcing the local repository boundary.

    Core/message schemas: name -> schemas/{name}.json
    Vertical schemas: verticals/{name} -> verticals/{name}.json
    """
    if not isinstance(name, str) or not name:
        return None
    if name.startswith("verticals/"):
        relative_name = name.removeprefix("verticals/")
        path = _safe_schema_path(VERTICALS_DIR, f"{relative_name}.json")
    else:
        relative_name = name.removeprefix("schemas/")
        path = _safe_schema_path(SCHEMAS_DIR, f"{relative_name}.json")
    if path is None:
        return None

    with path.open(encoding="utf-8") as handle:
        return json.load(handle)