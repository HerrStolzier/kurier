#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    result = subprocess.run([sys.executable, "scripts/workflow_check.py"], cwd=ROOT)
    log_dir = ROOT / ".agents"
    log_dir.mkdir(exist_ok=True)
    status = "OK" if result.returncode == 0 else "FAIL"
    with (log_dir / "workflow_guard_runs.md").open("a", encoding="utf-8") as file:
        file.write(f"## {datetime.now(UTC).isoformat()} - {status}\n\n")
        file.write("- command: uv run python scripts/agent_finish.py\n\n")
        file.write(f"- exit_code: {result.returncode}\n\n")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
