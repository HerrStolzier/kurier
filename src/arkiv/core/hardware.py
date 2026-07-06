"""Hardware and local-model fit helpers."""

from __future__ import annotations

import logging
import platform
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelRecommendation:
    min_ram_gb: int
    model_id: str
    display_name: str
    size_note: str


MODEL_RECOMMENDATIONS = [
    ModelRecommendation(16, "qwen2.5:14b", "Qwen 2.5 14B", "~9 GB, best quality"),
    ModelRecommendation(8, "qwen2.5:7b", "Qwen 2.5 7B", "~4.7 GB, recommended default"),
    ModelRecommendation(4, "qwen2.5:3b", "Qwen 2.5 3B", "~2 GB, fast but less accurate"),
    ModelRecommendation(4, "qwen2.5:1.5b", "Qwen 2.5 1.5B", "~1 GB, minimal (may misclassify)"),
]

MODEL_MIN_RAM_GB = {
    "qwen2.5:14b": 16,
    "qwen2.5:7b": 8,
    "qwen2.5:3b": 4,
    "qwen2.5:1.5b": 4,
    "qwen3.5:9b": 12,
    "llama3.1:8b": 8,
    "llama3.1": 8,
    "mistral": 8,
    "gemma": 8,
    "phi": 4,
}


def detect_ram_gb() -> int:
    """Detect total physical system RAM in GB."""
    system = platform.system()
    try:
        if system == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return int(result.stdout.strip()) // (1024**3)
        if system == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return kb // (1024 * 1024)
        if system == "Windows":
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            c_ulong = ctypes.c_ulong

            class MEMORYSTATUS(ctypes.Structure):
                _fields_ = [
                    ("dwLength", c_ulong),
                    ("dwMemoryLoad", c_ulong),
                    ("dwTotalPhys", ctypes.c_uint64),
                    ("dwAvailPhys", ctypes.c_uint64),
                    ("dwTotalPageFile", ctypes.c_uint64),
                    ("dwAvailPageFile", ctypes.c_uint64),
                    ("dwTotalVirtual", ctypes.c_uint64),
                    ("dwAvailVirtual", ctypes.c_uint64),
                    ("dwAvailExtendedVirtual", ctypes.c_uint64),
                ]

            mem = MEMORYSTATUS()
            mem.dwLength = ctypes.sizeof(MEMORYSTATUS)
            kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
            return int(mem.dwTotalPhys) // (1024**3)
    except Exception as exc:
        logger.debug("RAM detection failed: %s", exc)
    return 0


def recommended_models_for_ram(ram_gb: int) -> list[ModelRecommendation]:
    """Return setup recommendations that fit the detected RAM."""
    suitable = [model for model in MODEL_RECOMMENDATIONS if ram_gb >= model.min_ram_gb]
    return suitable or MODEL_RECOMMENDATIONS[-1:]


def default_eval_ollama_model(ram_gb: int | None = None) -> str:
    """Return a conservative Ollama model for automated benchmarks."""
    ram = detect_ram_gb() if ram_gb is None else ram_gb
    if ram and ram < 8:
        return "qwen2.5:3b"
    return "qwen2.5:7b"


def model_min_ram_gb(model_name: str) -> int | None:
    """Estimate minimum RAM for a local Ollama model."""
    normalized = model_name.removeprefix("ollama:").removeprefix("ollama_chat/")
    for prefix, ram in MODEL_MIN_RAM_GB.items():
        if normalized.startswith(prefix):
            return ram

    match = re.search(r":(\d+(?:\.\d+)?)b\b", normalized)
    if not match:
        return None
    params_b = float(match.group(1))
    if params_b <= 3:
        return 4
    if params_b <= 8:
        return 8
    if params_b <= 10:
        return 12
    if params_b <= 14:
        return 16
    return 32


def model_fits_ram(model_name: str, ram_gb: int | None = None) -> tuple[bool | None, str]:
    """Assess whether a local model likely fits available RAM."""
    ram = detect_ram_gb() if ram_gb is None else ram_gb
    required = model_min_ram_gb(model_name)
    if required is None:
        return None, f"Keine RAM-Empfehlung für {model_name} bekannt."
    if not ram:
        return None, f"RAM konnte nicht erkannt werden; {model_name} braucht ca. {required} GB."
    if ram >= required:
        return (
            True,
            f"{model_name} passt voraussichtlich zu {ram} GB RAM (Minimum ca. {required} GB).",
        )
    return False, f"{model_name} braucht ca. {required} GB RAM, erkannt wurden {ram} GB."
