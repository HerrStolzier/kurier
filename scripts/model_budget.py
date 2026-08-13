#!/usr/bin/env python3
"""Hartes gemeinsames Startbudget fuer teure Modellwerkzeuge.

Das Budget liegt im gemeinsamen Git-Verzeichnis. Dadurch sehen alle
Arbeitsbaeume desselben Repos denselben Zaehler. Eine Reservierung passiert vor
dem Modellstart und bleibt im Zweifel bestehen. Nur ein nachweislich nicht
gestarteter Lauf darf sie wieder freigeben.
"""

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import uuid

STATE_VERSION = 1
TOTAL_LIMIT = 3
TOOL_LIMITS = {
    "review": 2,
    "plan-review": 1,
    "hunt": 1,
    "security": 1,
}
STATE_PREFIX = "model-budget-"


class ModelBudgetError(RuntimeError):
    """Der gemeinsame Budgetstand ist nicht verlaesslich nutzbar."""


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def git_output(repo, *args):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or f"Exit {result.returncode}"
        raise ModelBudgetError(f"git {' '.join(args)}: {detail}")
    return (result.stdout or "").strip()


def budget_key(repo):
    branch = subprocess.run(
        ["git", "symbolic-ref", "--short", "-q", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    label = (branch.stdout or "").strip()
    if branch.returncode != 0 or not label:
        label = f"detached-{git_output(repo, 'rev-parse', 'HEAD')[:12]}"
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in label)
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:10]
    return f"{safe[:48]}-{digest}"


def store_dir(repo):
    raw = pathlib.Path(git_output(repo, "rev-parse", "--git-common-dir"))
    common = raw if raw.is_absolute() else pathlib.Path(repo) / raw
    return common.resolve() / "workflow-guard"


def state_paths(repo):
    key = budget_key(repo)
    store = store_dir(repo)
    return key, store / f"{STATE_PREFIX}{key}.json", store / f"{STATE_PREFIX}{key}.lock"


def write_json_atomic(path, value):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_state(path):
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelBudgetError(f"Modellbudget ist nicht lesbar: {exc}") from exc
    if (
        not isinstance(state, dict)
        or state.get("version") != STATE_VERSION
        or state.get("status") not in {"active", "closed"}
        or not isinstance(state.get("starts"), list)
    ):
        raise ModelBudgetError("Modellbudget hat ein ungueltiges Format")
    return state


def _new_state(key):
    return {
        "version": STATE_VERSION,
        "status": "active",
        "key": key,
        "series_id": uuid.uuid4().hex,
        "started_at": now(),
        "total_limit": TOTAL_LIMIT,
        "tool_limits": TOOL_LIMITS,
        "starts": [],
    }


def reserve(repo, tool, detail=None):
    """Einen teuren Modellstart atomar reservieren."""
    if tool not in TOOL_LIMITS:
        raise ModelBudgetError(f"unbekanntes teures Werkzeug: {tool}")
    key, state_path, lock_path = state_paths(repo)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = read_state(state_path)
        if state is None or state["status"] == "closed":
            state = _new_state(key)
        total_used = len(state["starts"])
        tool_used = sum(1 for item in state["starts"] if item.get("tool") == tool)
        tool_limit = TOOL_LIMITS[tool]
        if total_used >= TOTAL_LIMIT or tool_used >= tool_limit:
            return {
                "allowed": False,
                "total_used": total_used,
                "total_limit": TOTAL_LIMIT,
                "tool_used": tool_used,
                "tool_limit": tool_limit,
                "tool": tool,
            }
        reservation_id = uuid.uuid4().hex
        state["starts"].append(
            {
                "reservation_id": reservation_id,
                "tool": tool,
                "reserved_at": now(),
                "detail": (detail or "").strip() or None,
            }
        )
        write_json_atomic(state_path, state)
        return {
            "allowed": True,
            "key": key,
            "series_id": state["series_id"],
            "reservation_id": reservation_id,
            "total_used": total_used + 1,
            "total_limit": TOTAL_LIMIT,
            "tool_used": tool_used + 1,
            "tool_limit": tool_limit,
            "tool": tool,
        }


def release(repo, reservation, reason):
    """Nur einen nachweislich nicht gestarteten Modelllauf zuruecknehmen."""
    clean_reason = (reason or "").strip()
    if not clean_reason:
        raise ModelBudgetError("Freigabe braucht einen Grund")
    _, state_path, lock_path = state_paths(repo)
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = read_state(state_path)
        if (
            state is None
            or state["status"] != "active"
            or state.get("series_id") != reservation.get("series_id")
        ):
            return False
        for index, item in enumerate(state["starts"]):
            if item.get("reservation_id") == reservation.get("reservation_id"):
                released = state["starts"].pop(index)
                state.setdefault("released", []).append(
                    {**released, "released_at": now(), "release_reason": clean_reason}
                )
                write_json_atomic(state_path, state)
                return True
        return False


def close(repo, reason):
    """Das Aufgabenbudget nach einem belegten Abschluss schliessen."""
    clean_reason = (reason or "").strip()
    if not clean_reason:
        raise ModelBudgetError("Abschluss braucht einen Grund")
    _, state_path, lock_path = state_paths(repo)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = read_state(state_path)
        if state is None or state["status"] == "closed":
            return False
        state["status"] = "closed"
        state["closed_at"] = now()
        state["close_reason"] = clean_reason
        write_json_atomic(state_path, state)
        return True


def status(repo):
    """Maschinenlesbaren Budgetstand liefern, ohne ihn zu veraendern."""
    key, state_path, _ = state_paths(repo)
    state = read_state(state_path)
    if state is None:
        return {
            "version": STATE_VERSION,
            "status": "unused",
            "key": key,
            "total_limit": TOTAL_LIMIT,
            "tool_limits": TOOL_LIMITS,
            "starts": [],
        }
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "close"))
    parser.add_argument("--repo", default=".")
    parser.add_argument("--reason")
    args = parser.parse_args()
    repo = pathlib.Path(args.repo).resolve()
    try:
        if args.command == "status":
            print(json.dumps(status(repo), ensure_ascii=False, indent=2))
            return 0
        if not (args.reason or "").strip():
            parser.error("close braucht --reason")
        close(repo, args.reason)
        print("MODELLBUDGET: geschlossen")
        return 0
    except ModelBudgetError as exc:
        print(f"MODELLBUDGET: FEHLER - {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
