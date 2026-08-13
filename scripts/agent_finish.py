#!/usr/bin/env python3
"""Ein-Befehl-Abschluss fuer Agenten.

Reihenfolge:
  1. Struktur-Guard (workflow_check.py)
  2. Doku-Drift-Gate (doc_drift_check.py) - behauptete Pfade muessen existieren
  3. Review-Gate (review_gate.py) - opt-in via .agents/review_required
  4. Technischer Projektcheck (Befehl aus .agents/project_check, Pflicht)
  5. Optional: Claim-Check (--auto-claims)
  6. Gemeinsames Modellbudget nach gruenem Abschluss schliessen
  7. Lauf-Log nach .agents/finish_runs.jsonl

Exit-Code 2 bei Fehlschlag, damit ein Stop-Hook den Abschluss blockiert.
"""

import argparse
import datetime
import json
import pathlib
import subprocess
import sys

# Am Script-Pfad verankern, NICHT am cwd: Der Stop-Hook kann mit einem beliebigen
# Arbeitsverzeichnis starten. Mit cwd-relativem ROOT wuerde .agents/project_check
# dann nicht gefunden und der Projektcheck STILL uebersprungen - ein Guard, der
# "OK" meldet, ohne geprueft zu haben.
ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AGENTS = ROOT / ".agents"
RUNS = AGENTS / "command_runs.jsonl"
FINISH_LOG = AGENTS / "finish_runs.jsonl"
FINISH_REPORT = AGENTS / "last_finish_report.json"


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def record_command(command, exit_code):
    AGENTS.mkdir(exist_ok=True)
    with RUNS.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"command": command, "exit_code": exit_code, "ts": now()},
                ensure_ascii=False,
            )
            + "\n"
        )


def run(cmd):
    print(f"$ {cmd}")
    return subprocess.run(cmd, shell=True, cwd=ROOT).returncode


def read_stdin_json():
    try:
        if not sys.stdin.isatty():
            return json.loads(sys.stdin.read() or "{}")
    except Exception:
        pass
    return {}


def git_text(*args):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return (result.stdout or "").strip() if result.returncode == 0 else "unbekannt"


def write_completion_report(steps, project_command):
    """Einen knappen, pruefbaren Abschluss statt einer blossen OK-Zeile schreiben."""
    porcelain = git_text("status", "--porcelain")
    changed = len([line for line in porcelain.splitlines() if line.strip()])
    report = {
        "ts": now(),
        "code": {"changed_paths": changed},
        "review": {"gate": "ok" if dict(steps).get("review_gate") == 0 else "failed"},
        "tests": {"command": project_command, "status": "ok"},
        "git": {
            "branch": git_text("branch", "--show-current") or "detached",
            "head": git_text("rev-parse", "--short", "HEAD"),
            "working_tree": "clean" if not porcelain else "dirty",
        },
        "distribution": {
            "local_guard": "ok" if dict(steps).get("workflow_check") == 0 else "failed",
            "workspace_rollout": "separate tools/guard health verification required",
        },
    }
    FINISH_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("ABSCHLUSSBERICHT")
    print(f"Code: {changed} geaenderte Pfade im Arbeitsbaum")
    print("Review: Gate OK")
    print(f"Tests: OK ({project_command})")
    print(
        f"Git: {report['git']['branch']} @ {report['git']['head']}, "
        f"Arbeitsbaum {report['git']['working_tree']}"
    )
    print("Verteilung: lokale Guard-Kopie OK; Workspace-Rollout separat pruefen")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto-claims", action="store_true")
    args = ap.parse_args()

    read_stdin_json()  # Hook-Payload konsumieren (Stop-Hook uebergibt JSON via stdin)

    steps = []

    # 1) Struktur-Guard
    steps.append(("workflow_check", run("python3 scripts/workflow_check.py")))

    # 2) Doku-Drift: behauptet die Doku Pfade, die es nicht gibt?
    steps.append(("doc_drift", run("python3 scripts/doc_drift_check.py")))

    # 3) Cross-Model-Review (nur wenn .agents/review_required existiert)
    steps.append(("review_gate", run("python3 scripts/review_gate.py")))

    # 4) Technischer Projektcheck (Pflicht und in Git versioniert)
    pc = AGENTS / "project_check"
    rel_pc = ".agents/project_check"
    tracked = (
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel_pc],
            cwd=ROOT,
            capture_output=True,
        ).returncode
        == 0
    )
    project_command = "nicht gelaufen"
    if pc.exists() and pc.read_text(encoding="utf-8").strip() and tracked:
        project_command = pc.read_text(encoding="utf-8").strip()
        crc = run(project_command)
        record_command(project_command, crc)
        steps.append(("project_check", crc))
    else:
        if not pc.exists():
            reason = "fehlt"
        elif not pc.read_text(encoding="utf-8").strip():
            reason = "ist leer"
        else:
            reason = "ist nicht in Git versioniert"
        print(f"project_check: FEHLER ({rel_pc} {reason})", file=sys.stderr)
        steps.append(("project_check", 1))

    # 4) Claims
    if args.auto_claims:
        steps.append(("claim_check", run("python3 scripts/claim_check.py")))

    failed = [n for n, c in steps if c != 0]
    if not failed:
        steps.append(
            (
                "model_budget_close",
                run('python3 scripts/model_budget.py close --reason "agent_finish erfolgreich"'),
            )
        )
        failed = [n for n, c in steps if c != 0]

    # Den Abschlussbefehl selbst als Evidenz protokollieren - ERST JETZT, nach ALLEN
    # Schritten. Vorher geloggt wuerde ein fehlgeschlagener claim_check als
    # "exit_code: 0" im Beleg-Log stehen: falsche Evidenz fuer kuenftige Claims.
    finish_cmd = "python3 scripts/agent_finish.py" + (" --auto-claims" if args.auto_claims else "")
    record_command(finish_cmd, 0 if not failed else 1)

    FINISH_LOG.parent.mkdir(exist_ok=True)
    with FINISH_LOG.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"ts": now(), "steps": dict(steps), "ok": not failed},
                ensure_ascii=False,
            )
            + "\n"
        )

    if failed:
        print("AGENT-FINISH: FAIL -> " + ", ".join(failed), file=sys.stderr)
        print(
            "Arbeit ist nicht abgeschlossen. Bitte beheben und erneut abschliessen.",
            file=sys.stderr,
        )
        return 2

    write_completion_report(steps, project_command)
    print("AGENT-FINISH: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
