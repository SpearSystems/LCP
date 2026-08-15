#!/usr/bin/env python3
"""Reconcile a full Trivy SARIF report with reviewed exceptions.

The blocking Trivy scan remains responsible for fixed OS-package findings.
This tool gives the non-gating full report an explicit review gate: every
HIGH/CRITICAL finding must be represented in the machine-readable exception
register with the expected package and installed version.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any


_PACKAGE_RE = re.compile(r"^Package:\s*(?P<value>.+)$", re.MULTILINE)
_INSTALLED_RE = re.compile(r"^Installed Version:\s*(?P<value>.+)$", re.MULTILINE)
_FIXED_RE = re.compile(r"^Fixed Version:\s*(?P<value>.+)$", re.MULTILINE)
_SEVERITY_RE = re.compile(r"^Severity:\s*(?P<value>.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Finding:
    vulnerability_id: str
    package: str
    installed_version: str
    fixed_version: str | None
    severity: str
    target: str

    @property
    def key(self) -> str:
        return f"{self.vulnerability_id} {self.package}@{self.installed_version}"


def _field(pattern: re.Pattern[str], text: str, default: str = "") -> str:
    match = pattern.search(text)
    return match.group("value").strip() if match else default


def load_findings(path: Path) -> list[Finding]:
    document = json.loads(path.read_text(encoding="utf-8"))
    findings: list[Finding] = []
    for run in document.get("runs", []):
        for result in run.get("results", []):
            message = result.get("message", {}).get("text", "")
            locations = result.get("locations", [])
            target = "unknown"
            if locations:
                physical = locations[0].get("physicalLocation", {})
                target = physical.get("artifactLocation", {}).get("uri", target)
            vulnerability_id = str(result.get("ruleId", "")).strip()
            package = _field(_PACKAGE_RE, message, "unknown")
            installed = _field(_INSTALLED_RE, message, "unknown")
            fixed = _field(_FIXED_RE, message) or None
            severity = _field(_SEVERITY_RE, message).upper()
            if not vulnerability_id or severity not in {"HIGH", "CRITICAL"}:
                continue
            findings.append(
                Finding(
                    vulnerability_id=vulnerability_id,
                    package=package,
                    installed_version=installed,
                    fixed_version=fixed,
                    severity=severity,
                    target=target,
                )
            )
    return findings


def load_exceptions(path: Path) -> dict[str, dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError(f"Unsupported exception register schema: {path}")
    exceptions = document.get("exceptions")
    if not isinstance(exceptions, dict):
        raise ValueError(f"Exception register must contain an exceptions object: {path}")
    return exceptions


def reconcile(findings: list[Finding], exceptions: dict[str, dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    for finding in findings:
        exception = exceptions.get(finding.vulnerability_id)
        if exception is None:
            problems.append(f"unreviewed {finding.key} ({finding.severity})")
            continue
        packages = exception.get("packages", [])
        installed_versions = exception.get("installed_versions", [])
        if finding.package not in packages:
            problems.append(
                f"package mismatch for {finding.key}: register allows {packages!r}"
            )
        if finding.installed_version not in installed_versions:
            problems.append(
                f"installed-version mismatch for {finding.key}: "
                f"register allows {installed_versions!r}"
            )
        if finding.fixed_version and not exception.get("allow_fixed", False):
            problems.append(
                f"fixed finding is not explicitly allowed by register: {finding.key} "
                f"(fixed in {finding.fixed_version})"
            )
    return problems


def render_summary(
    sarif_path: Path,
    findings: list[Finding],
    problems: list[str],
) -> str:
    lines = [
        "# Trivy SARIF reconciliation",
        "",
        f"- Report: `{sarif_path}`",
        f"- HIGH/CRITICAL findings: **{len(findings)}**",
        f"- Review status: **{'FAIL' if problems else 'PASS'}**",
        "",
    ]
    if findings:
        lines.extend(
            [
                "| Finding | Package | Installed | Fixed | Severity | Target |",
                "|---|---|---:|---:|---:|---|",
            ]
        )
        for finding in findings:
            lines.append(
                f"| `{finding.vulnerability_id}` | `{finding.package}` | "
                f"`{finding.installed_version}` | `{finding.fixed_version or 'unfixed'}` | "
                f"{finding.severity} | `{finding.target}` |"
            )
        lines.append("")
    if problems:
        lines.append("## Review failures")
        lines.extend(f"- {problem}" for problem in problems)
    else:
        lines.append("Every HIGH/CRITICAL finding matches the reviewed exception register.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sarif", type=Path, required=True)
    parser.add_argument("--exceptions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        findings = load_findings(args.sarif)
        exceptions = load_exceptions(args.exceptions)
        problems = reconcile(findings, exceptions)
        args.output.write_text(
            render_summary(args.sarif, findings, problems), encoding="utf-8"
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"SARIF reconciliation error: {exc}", file=sys.stderr)
        return 2

    if problems:
        print("SARIF reconciliation failed:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print(f"SARIF reconciliation passed: {len(findings)} HIGH/CRITICAL finding(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
