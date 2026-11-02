from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from check_logging_hygiene import check_logging_hygiene  # noqa: E402


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class LoggingHygieneTests(unittest.TestCase):
    def scan(self, files: dict[str, str]) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative, text in files.items():
                write(root, relative, text)
            return check_logging_hygiene(root)

    def test_stdout_print_in_platform_code_is_flagged(self):
        problems = self.scan(
            {
                "implementations/reference-platform/lcp_platform/router.py": "def f(envelope):\n    print(envelope['lead_id'])\n",
            }
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("OUTSIDE_STDOUT_PRINT", problems[0])
        self.assertIn("router.py:2", problems[0])

    def test_stderr_operational_message_is_allowed(self):
        problems = self.scan(
            {
                "implementations/reference-platform/lcp_platform/server.py": "import sys\nprint('listening', file=sys.stderr)\n",
            }
        )
        self.assertEqual(problems, [])

    def test_payload_dump_to_stderr_is_still_flagged(self):
        problems = self.scan(
            {
                "implementations/reference-platform/lcp_platform/worker.py": "import json, sys\n"
                "print(json.dumps(envelope), file=sys.stderr)\n",
            }
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("PAYLOAD_DUMP", problems[0])

    def test_console_log_in_sdk_js_is_flagged(self):
        problems = self.scan(
            {
                "implementations/sdk/typescript/src/index.ts": 'export const x = 1;\nconsole.log("ready");\n',
            }
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("OUTSIDE_STDOUT_PRINT", problems[0])

    def test_wire_serialization_is_not_flagged(self):
        problems = self.scan(
            {
                "implementations/reference-platform/lcp_platform/messages.py": "import json\n"
                "def body(envelope):\n    return json.dumps(envelope)\n",
            }
        )
        self.assertEqual(problems, [])

    def test_operator_cli_and_test_harnesses_are_exempt(self):
        problems = self.scan(
            {
                "implementations/reference-platform/lcp_platform/admin.py": "import json\nprint(json.dumps(rows))\n",
                "implementations/sdk/python/tests/test_client.py": "print('runner status')\n",
                "tools/check_commit_attribution.py": "print('cli output')\n",
                "test-vectors/conformance.py": "print('PASS')\n",
            }
        )
        self.assertEqual(problems, [])

    def test_example_header_required_and_accepted(self):
        body = "import json\nprint(json.dumps(envelope))\n"
        missing = self.scan({"examples/integrations/buyer_webhook.py": body})
        self.assertEqual(len(missing), 1)
        self.assertIn("MISSING_EXAMPLE_HEADER", missing[0])

        ok = self.scan(
            {
                "examples/integrations/buyer_webhook.py": "#!/usr/bin/env python3\n"
                "# example — not for production: illustrative template.\n" + body,
                "examples/integrations/publisher_node.mjs": "#!/usr/bin/env node\n"
                "// example — not for production: illustrative template.\n"
                "console.log(text);\n",
                "examples/integrations/README.md": "# Integration examples\n\n"
                "> Example — not for production.\n",
            }
        )
        self.assertEqual(ok, [])

    def test_vendored_trees_are_skipped(self):
        problems = self.scan(
            {
                "implementations/mcp-server/.venv/lib/site/httpx.py": "print('vendored')\n",
                "implementations/sdk/typescript/node_modules/pkg/index.js": "console.log('vendored')\n",
            }
        )
        self.assertEqual(problems, [])

    def test_go_fmt_println_in_sdk_is_flagged(self):
        problems = self.scan(
            {
                "implementations/sdk/go/lcp.go": "package lcp\n"
                'import "fmt"\n'
                'func sign(body string) { fmt.Println("body", body) }\n',
            }
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("OUTSIDE_STDOUT_PRINT", problems[0])
        self.assertIn("lcp.go:3", problems[0])

    def test_go_log_print_is_flagged(self):
        problems = self.scan(
            {
                "implementations/sdk/go/validation.go": "package lcp\n"
                'import "log"\n'
                'func check() { log.Printf("envelope %s", env) }\n',
            }
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("OUTSIDE_STDOUT_PRINT", problems[0])

    def test_go_os_stdout_write_is_flagged(self):
        problems = self.scan(
            {
                "implementations/sdk/go/lcp.go": "package lcp\n"
                'import "os"\n'
                "func dump(b []byte) { os.Stdout.Write(b) }\n",
            }
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("OUTSIDE_STDOUT_PRINT", problems[0])

    def test_go_fprint_to_stdout_is_flagged_but_stderr_is_allowed(self):
        flag = self.scan(
            {
                "implementations/sdk/go/lcp.go": "package lcp\n"
                'import ("fmt"; "os")\n'
                'func leak(e string) { fmt.Fprintf(os.Stdout, "%s\\n", e) }\n',
            }
        )
        self.assertEqual(len(flag), 1)
        self.assertIn("OUTSIDE_STDOUT_PRINT", flag[0])
        allow = self.scan(
            {
                "implementations/sdk/go/lcp.go": "package lcp\n"
                'import ("fmt"; "os")\n'
                'func note() { fmt.Fprintln(os.Stderr, "starting") }\n',
            }
        )
        self.assertEqual(allow, [])

    def test_go_string_building_is_not_flagged(self):
        problems = self.scan(
            {
                "implementations/sdk/go/lcp.go": "package lcp\n"
                'import ("errors"; "fmt")\n'
                'func id(s string) string { return fmt.Sprintf("%s-%s", s, s) }\n'
                'func err() error { return errors.New(fmt.Sprint("x")) }\n',
            }
        )
        self.assertEqual(problems, [])

    def test_go_test_files_are_exempt(self):
        problems = self.scan(
            {
                "implementations/sdk/go/log_capture_test.go": "package lcp\n"
                'import ("fmt"; "os"; "testing")\n'
                'func TestX(t *testing.T) { fmt.Fprintf(os.Stderr, "%v\\n", x); fmt.Println("status") }\n',
            }
        )
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
