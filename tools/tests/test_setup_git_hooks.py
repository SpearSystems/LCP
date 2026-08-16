import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tools.setup_git_hooks import configure_hooks


class GitHookSetupTests(unittest.TestCase):
    @patch("tools.setup_git_hooks.subprocess.run")
    def test_configure_hooks_sets_relative_hooks_path(self, run) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hook = root / ".githooks" / "commit-msg"
            hook.parent.mkdir()
            hook.write_text("#!/bin/sh\n", encoding="utf-8")
            hook.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

            configure_hooks(root)

            run.assert_called_once_with(
                ["git", "config", "--local", "core.hooksPath", ".githooks"],
                cwd=root,
                check=True,
            )

    def test_configure_hooks_requires_the_tracked_hook(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                configure_hooks(Path(directory))


if __name__ == "__main__":
    unittest.main()
