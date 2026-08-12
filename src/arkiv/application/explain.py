"""Workflow for source-linked document explanations."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from arkiv.application.context import AppContext
from arkiv.core.engine import can_extract_document_text
from arkiv.core.explainer import (
    DocumentExplainer,
    DocumentExplanation,
    ExplanationError,
)
from arkiv.core.llm import ollama_model_is_available

_MODEL_CHECK_TTL_SECONDS = 30
_model_availability_cache: dict[tuple[str, str], tuple[float, bool]] = {}


def explanation_model_available(ctx: AppContext) -> bool:
    """Return whether the configured local explanation model is ready to use."""
    config = ctx.config.explanation
    if not config.enabled:
        return False
    cache_key = (config.base_url.rstrip("/"), config.model)
    cached = _model_availability_cache.get(cache_key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _MODEL_CHECK_TTL_SECONDS:
        return cached[1]
    try:
        with urlopen(f"{config.base_url.rstrip('/')}/api/tags", timeout=2) as response:
            payload: Any = json.loads(response.read())
    except Exception:
        _model_availability_cache[cache_key] = (now, False)
        return False
    models = payload.get("models", []) if isinstance(payload, dict) else []
    names = [
        str(model.get("name")) for model in models if isinstance(model, dict) and model.get("name")
    ]
    available = ollama_model_is_available(config.model, names)
    _model_availability_cache[cache_key] = (now, available)
    return available


def document_is_explainable(item: dict[str, Any]) -> bool:
    """Return whether the source text for a stored item is still available."""
    original_path = str(item.get("original_path") or "")
    if original_path.startswith("text://"):
        return bool(str(item.get("content_text") or "").strip())
    candidates = (str(item.get("destination") or ""), original_path)
    return any(
        Path(value).is_file() and can_extract_document_text(Path(value)) for value in candidates
    )


def _read_document_text(ctx: AppContext, item: dict[str, Any]) -> tuple[str, bool]:
    """Read the local document, preferring its final destination over a stale source path."""
    original_path = str(item.get("original_path") or "")
    if original_path.startswith("text://"):
        content = str(item.get("content_text") or "")
        if not content.strip():
            raise ExplanationError("Der gespeicherte Text ist nicht verfügbar.")
        # Bis zu dieser Version wurden rohe Texte ohne Längenkennung bei exakt
        # 2.000 Zeichen abgeschnitten. In diesem Grenzfall warnen wir lieber
        # einmal zu viel, statt einen alten unvollständigen Vertrag als
        # vollständig zu erklären.
        return content, len(content) == 2000

    candidates = (str(item.get("destination") or ""), original_path)
    for value in candidates:
        path = Path(value)
        if path.is_file():
            try:
                return ctx.engine.extract_document_text(path)
            except ValueError as exc:
                raise ExplanationError(str(exc)) from exc
    raise ExplanationError("Die abgelegte Datei wurde nicht gefunden.")


def explain_document(ctx: AppContext, item_id: int) -> DocumentExplanation:
    """Explain one locally stored contract or set of terms on demand."""
    item = ctx.engine.store.get_item(item_id)
    if item is None:
        raise ExplanationError("Dieses Dokument wurde nicht gefunden.")
    if item.get("category") != "vertrag" or item.get("status") == "duplicate":
        raise ExplanationError("Die einfache Erklärung ist nur für Verträge verfügbar.")
    content, source_is_partial = _read_document_text(ctx, item)
    return DocumentExplainer(ctx.config.explanation).explain(
        content, source_is_partial=source_is_partial
    )
