#!/usr/bin/env python3
"""Enable the tracked Git hooks for the current checkout."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


HOOKS_PATH = Path(".githooks")
COMMIT_MSG_HOOK = HOOKS_PATH / "commit-msg"


def repository_root() -> Path:
    """Return the root of the current Git checkout."""

    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()).resolve()


def configure_hooks(root: Path) -> None:
    """Configure the checkout to use the repository's tracked hooks."""

    hook = root / COMMIT_MSG_HOOK
    if not hook.is_file():
        raise FileNotFoundError(f"tracked hook is missing: {hook}")
    if not hook.stat().st_mode & 0o111:
        raise PermissionError(f"tracked hook is not executable: {hook}")

    subprocess.run(
        ["git", "config", "--local", "core.hooksPath", str(HOOKS_PATH)],
        cwd=root,
        check=True,
    )


def main() -> int:
    try:
        root = repository_root()
        configure_hooks(root)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Git hook setup failed: {exc}", file=sys.stderr)
        return 2

    print(f"Git hooks enabled for {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
