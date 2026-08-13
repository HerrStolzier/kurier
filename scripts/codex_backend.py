#!/usr/bin/env python3
"""Gemeinsamer Unterbau der drei Cross-Model-Scripts (agent_review, agent_hunt,
agent_plan_review).

Warum es diese Datei gibt: Der Teil, der die codex-CLI startet und ihre Fehler
deutet, stand dreimal fast wortgleich da. Jede Backend-Aenderung musste dreimal
gemacht und dreimal richtig gemacht werden - die Git-History zeigt genau das
("Default zurueck auf codex in allen drei Cross-Model-Scripts", "GLM auch fuer
Hunt und Plan-Review"). Schlimmer als der Umfang war das Risiko: in diesem Block
stecken teuer erkaufte Fixes (Start ueber die Login-Shell, Fehlererkennung nur
auf echten ERROR-Zeilen). Eine Kopie, die einen davon verpasst, sieht aus wie
ein Lauf, der nichts gefunden hat.

Bewusst NICHT hier: DEFAULT_MODEL. agent_review laeuft auf sol, die anderen
beiden auf terra - das ist eine Absicht pro Werkzeug (siehe die Begruendungen
dort), keine Gemeinsamkeit.

Diese Datei wird wie die uebrigen Guard-Scripts per `tools/guard sync` in die
Repos kopiert und liegt dort neben ihren Aufrufern.
"""

import datetime
import json
import os
import shlex
import shutil
import subprocess
import sys

# Effort ist bei "medium" gedeckelt (Nutzervorgabe 2026-07-14, bei der
# Umstellung von agent_review auf sol am 2026-07-31 ausdruecklich bestaetigt).
# "high" ist bewusst KEINE Option: ein Lauf auf hohem Effort frisst spuerbar
# Kontingent, und alle drei Werkzeuge teilen EINEN Pool - ein Gate, das sich das
# Kontingent selbst leerlaeuft, blockiert am Ende alles.
#
# Nicht die codex-Konfiguration erben lassen: ~/.codex/config.toml steht auf
# model_reasoning_effort = "low". Fuer interaktives Arbeiten sinnvoll, fuer einen
# Zweitleser falsch - Gegenlesen ist die haerteste Denkaufgabe in der Kette,
# nicht die billigste.
EFFORT_CHOICES = ["low", "medium"]
DEFAULT_EFFORT = "medium"

# Echte codex-Fehler, die KEIN Ergebnis sind, sondern ein nicht gelaufener Lauf.
AUTH_MARKERS = (
    "token_expired",
    "401 Unauthorized",
    "refresh token was already used",
    "Please log out and sign in again",
)
QUOTA_MARKERS = ("hit your usage limit", "usage_limit_reached", "rate_limit_exceeded")


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def record_command(repo, command, exit_code):
    agents = repo / ".agents"
    agents.mkdir(exist_ok=True)
    with (agents / "command_runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"command": command, "exit_code": exit_code, "ts": now()},
                ensure_ascii=False,
            )
            + "\n"
        )


def find_codex():
    """Die Tool-Shell hat nicht denselben PATH wie die Login-Shell."""
    exe = shutil.which("codex")
    if exe:
        return exe
    for cand in (
        "/usr/local/bin/codex",
        "/opt/homebrew/bin/codex",
        os.path.expanduser("~/.local/bin/codex"),
    ):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def effort_flag(effort):
    """Effort pro Aufruf setzen, statt den globalen "low"-Default zu erben.

    -c ueberschreibt config.toml nur fuer diesen Lauf; die interaktive
    codex-Nutzung des Users bleibt unangetastet.
    """
    return ["-c", f'model_reasoning_effort="{effort}"']


def internal_flags():
    """Projekt-Hooks im internen Zweitmodell-Lauf abschalten.

    Sonst startet `codex exec review` am eigenen Stop erneut agent_finish.py.
    Das Review-Gate wartet zu diesem Zeitpunkt aber genau auf diesen Review:
    eine zirkulaere Freigabe, die nur durch den Schleifenschutz enden wuerde.
    Der aeussere Codex-Task behaelt seine Hooks; nur der bewusst gestartete,
    read-only Zweitleser bekommt diese lokale Config-Ueberschreibung.
    """
    return ["-c", "features.hooks=false"]


def read_only_cmd(backend_bin, model, effort, prompt):
    """Lesender codex-Lauf mit freiem Prompt (agent_hunt, agent_plan_review).

    Read-only-Sandbox: der Zweitleser diagnostiziert bzw. prueft, er repariert
    nicht. Damit kann er auch keinen Zustand zerstoeren, waehrend die Session
    parallel am selben Arbeitsbaum haengt.

    agent_review baut sein Kommando selbst: es nutzt den `review`-Unterbefehl
    der codex-CLI mit Scope-Flags, nicht einen freien Prompt.
    """
    return [
        backend_bin,
        "exec",
        *internal_flags(),
        "--sandbox",
        "read-only",
        "--model",
        model,
        *effort_flag(effort),
        prompt,
    ]


def run(cmd, repo):
    """Startet codex und liefert (output, returncode). stdout und stderr gemischt.

    Ueber die LOGIN-Shell starten: codex braucht CODEX_HOME/OPENAI_API_KEY aus
    der zshrc. Ein direkter subprocess erbt die nicht und laeuft in einen
    irrefuehrenden "Login abgelaufen"-Fehler.
    """
    shell_cmd = " ".join(shlex.quote(c) for c in cmd)
    proc = subprocess.run(["zsh", "-lic", shell_cmd], cwd=repo, capture_output=True, text=True)
    return (proc.stdout or "") + (proc.stderr or ""), proc.returncode


def classify_failure(output, returncode):
    """Gibt "auth", "quota" oder None zurueck.

    NUR echte codex-Fehlerzeilen auswerten, nicht die gesamte Ausgabe.

    Vorher wurde im kompletten Output gesucht - und der enthaelt den GELESENEN
    CODE. Sobald Codex diese Scripts selbst las, fand der Detektor seine eigenen
    Suchmuster ("token_expired", "401 Unauthorized", ...) im Diff und meldete
    faelschlich "Login abgelaufen". Ein Mechanismus, der sich selbst liest und
    daran scheitert. codex meldet echte Fehler als "ERROR: ..." und endet dann
    mit exit != 0 - beides muss zutreffen.
    """
    if returncode == 0:
        return None
    error_lines = "\n".join(
        line for line in output.splitlines() if line.strip().startswith("ERROR:")
    )
    if any(s in error_lines for s in AUTH_MARKERS):
        return "auth"
    # Kontingent erschoepft sieht sonst aus wie ein Lauf, der nichts gefunden hat.
    if any(s in error_lines for s in QUOTA_MARKERS):
        return "quota"
    return None


def report_failure(kind, label, output, extra=()):
    """Druckt die Standardmeldung fuer einen NICHT gelaufenen Lauf nach stderr.

    `label` ist das Werkzeug in Grossbuchstaben ("REVIEW", "HUNT", ...), `extra`
    sind zusaetzliche Hinweiszeilen des Aufrufers.
    """
    if kind == "auth":
        print(f"{label} NICHT GELAUFEN: codex-Login abgelaufen.", file=sys.stderr)
        print("Behebung (interaktives Terminal noetig):  codex login", file=sys.stderr)
    elif kind == "quota":
        print(f"{label} NICHT GELAUFEN: codex-Kontingent erschoepft.", file=sys.stderr)
        for line in output.splitlines():
            if "usage limit" in line:
                print(f"  {line.strip()}", file=sys.stderr)
                break
    else:
        raise ValueError(f"unbekannte Fehlerart: {kind!r}")
    for line in extra:
        print(line, file=sys.stderr)
