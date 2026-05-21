#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "AGENTS.md",
    "WORKFLOWS.md",
    "KNOWN_ERRORS.md",
    "CHECKS.md",
    "scripts/workflow_check.py",
    "scripts/agent_finish.py",
]
WORKFLOW_HEADINGS = [
    "Zweck",
    "Start",
    "Input",
    "Output",
    "Wichtige Dateien",
    "Abhaengigkeiten",
    "Bekannte Fehlerfaelle",
    "Pruefung",
    "Letzter Review",
]
PROJECT_CHECKS = [
    ["uv", "run", "ruff", "check", "src/", "tests/"],
    ["uv", "run", "mypy", "src/arkiv/", "--ignore-missing-imports"],
    ["uv", "run", "pytest", "tests/", "-x", "-q"],
]


def main() -> int:
    failures = _guard_file_failures()
    if not shutil.which("uv"):
        failures.append("missing command: uv")
    else:
        failures.extend(_run_project_checks())

    if failures:
        print("Workflow Guard Check: FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Workflow Guard Check: OK")
    return 0


def _guard_file_failures() -> list[str]:
    failures = []
    for relative in REQUIRED:
        if not (ROOT / relative).exists():
            failures.append(f"missing: {relative}")

    workflow_path = ROOT / "WORKFLOWS.md"
    workflow_text = workflow_path.read_text(encoding="utf-8") if workflow_path.exists() else ""
    for heading in WORKFLOW_HEADINGS:
        if f"### {heading}" not in workflow_text:
            failures.append(f"WORKFLOWS.md missing section: {heading}")

    for doc in ["WORKFLOWS.md", "KNOWN_ERRORS.md", "CHECKS.md"]:
        path = ROOT / doc
        if path.exists() and "TODO" in path.read_text(encoding="utf-8"):
            failures.append(f"{doc} contains TODO")

    return failures


def _run_project_checks() -> list[str]:
    failures = []
    for command in PROJECT_CHECKS:
        print(f"$ {' '.join(command)}")
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode != 0:
            failures.append(f"command failed: {' '.join(command)}")
            break
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
