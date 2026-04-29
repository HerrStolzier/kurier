"""OS-native background service management for Kurier."""

from __future__ import annotations

import contextlib
import logging
import platform
import plistlib
import shutil
import subprocess
from pathlib import Path

from arkiv.core.config import DEFAULT_CONFIG_FILE

logger = logging.getLogger(__name__)

PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "local.kurier.watch.plist"
SYSTEMD_PATH = Path.home() / ".config" / "systemd" / "user" / "kurier.service"
LOG_PATH_MACOS = Path.home() / "Library" / "Logs" / "kurier.log"
LOG_PATH_LINUX: Path | None = None  # journalctl handles this

_SYSTEMD_UNIT_TEMPLATE = """\
[Unit]
Description=Kurier File Watcher
After=network.target

[Service]
ExecStart={kurier_path} watch --verbose --drain-existing
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install() -> tuple[bool, str]:
    """Install and start the background service. Returns (success, message)."""
    if not DEFAULT_CONFIG_FILE.exists():
        return False, (
            f"Keine Konfigurationsdatei gefunden: {DEFAULT_CONFIG_FILE}. "
            "Bitte zuerst 'kurier init' ausführen."
        )

    if is_installed():
        return False, "Dienst ist bereits installiert."

    kurier_path = shutil.which("kurier")
    if kurier_path is None:
        return False, (
            "'kurier' nicht im PATH gefunden. Stelle sicher, dass das Paket installiert ist."
        )

    system = platform.system()
    if system == "Darwin":
        return _install_macos(kurier_path)
    elif system == "Linux":
        return _install_linux(kurier_path)
    else:
        return False, f"Betriebssystem '{system}' wird nicht unterstützt."


def uninstall() -> tuple[bool, str]:
    """Stop and remove the background service."""
    system = platform.system()
    if system == "Darwin":
        return _uninstall_macos()
    elif system == "Linux":
        return _uninstall_linux()
    else:
        return False, f"Betriebssystem '{system}' wird nicht unterstützt."


def status() -> dict[str, object]:
    """Get service status.

    Returns dict with keys: installed, running, pid, log_path, recent_logs.
    """
    system = platform.system()
    if system == "Darwin":
        return _status_macos()
    elif system == "Linux":
        return _status_linux()
    else:
        return {
            "installed": False,
            "running": False,
            "pid": None,
            "log_path": None,
            "recent_logs": [],
        }


def is_installed() -> bool:
    """Check if service is installed."""
    system = platform.system()
    if system == "Darwin":
        return PLIST_PATH.exists()
    elif system == "Linux":
        return SYSTEMD_PATH.exists()
    return False


# ---------------------------------------------------------------------------
# macOS helpers
# ---------------------------------------------------------------------------


def _install_macos(kurier_path: str) -> tuple[bool, str]:
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    plist: dict[str, object] = {
        "Label": "local.kurier.watch",
        "ProgramArguments": [kurier_path, "watch", "--verbose", "--drain-existing"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(LOG_PATH_MACOS),
        "StandardErrorPath": str(LOG_PATH_MACOS.with_suffix(".error.log")),
    }
    try:
        with open(PLIST_PATH, "wb") as f:
            plistlib.dump(plist, f)
    except OSError as exc:
        return False, f"Plist konnte nicht geschrieben werden: {exc}"

    result = _run(["launchctl", "load", "-w", str(PLIST_PATH)])
    if result is None or result.returncode != 0:
        stderr = result.stderr.strip() if result else "Timeout"
        return False, f"launchctl load fehlgeschlagen: {stderr}"

    return True, f"Dienst erfolgreich installiert und gestartet. Log: {LOG_PATH_MACOS}"


def _uninstall_macos() -> tuple[bool, str]:
    if not PLIST_PATH.exists():
        return False, "Dienst ist nicht installiert."

    result = _run(["launchctl", "unload", "-w", str(PLIST_PATH)])
    if result is None:
        return False, "launchctl unload: Timeout."

    try:
        PLIST_PATH.unlink()
    except OSError as exc:
        return False, f"Plist konnte nicht gelöscht werden: {exc}"

    return True, "Dienst erfolgreich gestoppt und deinstalliert."


def _status_macos() -> dict[str, object]:
    installed = PLIST_PATH.exists()
    running = False
    pid: int | None = None

    if installed:
        result = _run(["launchctl", "list", "local.kurier.watch"])
        if result and result.returncode == 0:
            running = True
            output = result.stdout
            for line in output.splitlines():
                line = line.strip()
                if line.startswith('"PID"'):
                    # Format: "PID" = 12345;
                    parts = line.split("=")
                    if len(parts) == 2:
                        with contextlib.suppress(ValueError):
                            pid = int(parts[1].strip().rstrip(";").strip())

    recent_logs: list[str] = []
    if LOG_PATH_MACOS.exists():
        try:
            lines = LOG_PATH_MACOS.read_text(errors="replace").splitlines()
            recent_logs = lines[-20:]
        except OSError:
            pass

    return {
        "installed": installed,
        "running": running,
        "pid": pid,
        "log_path": str(LOG_PATH_MACOS) if LOG_PATH_MACOS.exists() else None,
        "recent_logs": recent_logs,
    }


# ---------------------------------------------------------------------------
# Linux helpers
# ---------------------------------------------------------------------------


def _install_linux(kurier_path: str) -> tuple[bool, str]:
    SYSTEMD_PATH.parent.mkdir(parents=True, exist_ok=True)
    unit_content = _SYSTEMD_UNIT_TEMPLATE.format(kurier_path=kurier_path)
    try:
        SYSTEMD_PATH.write_text(unit_content)
    except OSError as exc:
        return False, f"Systemd-Unit konnte nicht geschrieben werden: {exc}"

    for cmd in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "kurier"],
    ):
        result = _run(cmd)
        if result is None or result.returncode != 0:
            stderr = result.stderr.strip() if result else "Timeout"
            return False, f"'{' '.join(cmd)}' fehlgeschlagen: {stderr}"

    return True, "Dienst erfolgreich installiert und gestartet (systemd --user)."


def _uninstall_linux() -> tuple[bool, str]:
    if not SYSTEMD_PATH.exists():
        return False, "Dienst ist nicht installiert."

    result = _run(["systemctl", "--user", "disable", "--now", "kurier"])
    if result is None:
        return False, "systemctl disable: Timeout."

    try:
        SYSTEMD_PATH.unlink()
    except OSError as exc:
        return False, f"Unit-Datei konnte nicht gelöscht werden: {exc}"

    _run(["systemctl", "--user", "daemon-reload"])
    return True, "Dienst erfolgreich gestoppt und deinstalliert."


def _status_linux() -> dict[str, object]:
    installed = SYSTEMD_PATH.exists()
    running = False

    if installed:
        result = _run(["systemctl", "--user", "is-active", "kurier"])
        if result and result.stdout.strip() == "active":
            running = True

    # Fetch recent journal entries; fails gracefully if journalctl unavailable
    recent_logs: list[str] = []
    result = _run(["journalctl", "--user", "-u", "kurier", "-n", "20", "--no-pager"])
    if result and result.returncode == 0:
        recent_logs = result.stdout.splitlines()

    return {
        "installed": installed,
        "running": running,
        "pid": None,
        "log_path": LOG_PATH_LINUX,
        "recent_logs": recent_logs,
    }


# ---------------------------------------------------------------------------
# Internal utility
# ---------------------------------------------------------------------------


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run a subprocess with timeout=10. Returns None on timeout."""
    try:
        return subprocess.run(cmd, timeout=10, capture_output=True, text=True, check=False)
    except subprocess.TimeoutExpired:
        logger.warning("Befehl '%s' hat das Zeitlimit überschritten.", " ".join(cmd))
        return None
    except FileNotFoundError:
        logger.debug("Befehl nicht gefunden: %s", cmd[0])
        return None
    except Exception as exc:
        logger.debug("Fehler beim Ausführen von '%s': %s", " ".join(cmd), exc)
        return None
