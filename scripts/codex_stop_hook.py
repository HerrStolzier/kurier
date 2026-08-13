#!/usr/bin/env python3
"""Codex-Adapter fuer den projektlokalen Workflow-Guard-Stop-Hook."""

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FINISH = ROOT / "scripts" / "agent_finish.py"
MAX_REASON_CHARS = 6000


def read_payload():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, OSError):
        return {}


def finish_output(proc):
    chunks = [part.strip() for part in (proc.stdout, proc.stderr) if part and part.strip()]
    text = "\n".join(chunks) or f"agent_finish.py endete mit Exit {proc.returncode}."
    if len(text) > MAX_REASON_CHARS:
        text = "... gekuerzt ...\n" + text[-MAX_REASON_CHARS:]
    return text


def main():
    payload = read_payload()
    proc = subprocess.run(
        [sys.executable, str(FINISH), "--auto-claims"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    detail = finish_output(proc)

    if proc.returncode == 0:
        print(json.dumps({"continue": True, "systemMessage": "Workflow Guard: OK"}))
        return 0

    if payload.get("stop_hook_active"):
        print(
            json.dumps(
                {
                    "continue": False,
                    "stopReason": "Workflow Guard weiterhin fehlgeschlagen.",
                    "systemMessage": detail,
                },
                ensure_ascii=False,
            )
        )
        return 0

    print(detail, file=sys.stderr)
    print(
        "Workflow Guard blockiert den Abschluss. Fehler beheben und erneut abschliessen.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
