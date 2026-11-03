"""Test bootstrap: make ``lcp_platform`` importable from the repository root.

CI runs the suite with ``PYTHONPATH=implementations/reference-platform``; this
hook gives the same resolution when pytest is invoked with a path from the
repository root (as the v1.2 acceptance command is).
"""

from __future__ import annotations

import sys
from pathlib import Path

_PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(_PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_ROOT))
