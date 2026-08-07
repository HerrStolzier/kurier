"""Gemeinsame Texte und Empfehlungen für die Einrichtungswege.

TUI-Erststart (tui/app.py) und `kurier init` (commands/setup.py) sollen
dieselbe Modell-Empfehlung und dieselben Hinweise verwenden, damit die
beiden Wege nicht auseinanderlaufen.
"""

from __future__ import annotations

RECOMMENDED_MODEL = "qwen2.5:7b"

# Modellname-Präfix -> (Beschreibung, empfohlen?)
MODEL_HINTS: dict[str, tuple[str, bool]] = {
    "qwen2.5:7b": ("Schnell & genau — empfohlen für Kurier", True),
    "qwen2.5:3b": ("Leichtgewicht, gut für ältere Rechner", False),
    "qwen2.5:1.5b": ("Minimal, kann ungenau klassifizieren", False),
    "qwen3.5": ("Langsam (Denkpause ~100s pro Datei)", False),
    "llama3.1:8b": ("Solide Alternative zu Qwen", False),
    "llama3.1": ("Solide Alternative zu Qwen", False),
    "mistral": ("Gut für englische Texte", False),
    "gemma": ("Google-Modell, mittlere Qualität", False),
    "phi": ("Microsoft, klein aber fähig", False),
    "nomic-embed": ("Nur für Embeddings — nicht geeignet", False),
    "minimax": ("Cloud-Modell, nicht lokal", False),
}

OLLAMA_MISSING_STEPS = (
    "1. Öffne https://ollama.com und installiere Ollama.\n"
    "2. Starte die Ollama-App.\n"
    "3. Danach hier erneut prüfen."
)


def model_hint(model_name: str) -> tuple[str, bool]:
    """Gibt Beschreibung und Empfehlung für ein Modell zurück."""
    for prefix, (desc, rec) in MODEL_HINTS.items():
        if model_name.startswith(prefix):
            return desc, rec
    return "", False
