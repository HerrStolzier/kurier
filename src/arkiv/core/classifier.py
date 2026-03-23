"""LLM-powered classification engine."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import litellm
from litellm import completion

from arkiv.core.config import ArkivConfig, LLMConfig

# Disable LiteLLM telemetry — user data must never leave the system
litellm.telemetry = False

logger = logging.getLogger(__name__)

DEFAULT_CATEGORIES: dict[str, str] = {
    "rechnung": "invoice, bill, payment request, price list",
    "vertrag": "contract, lease, agreement, terms",
    "brief": "letter, correspondence, official notice",
    "bescheid": "government notice, tax assessment, official decision",
    "artikel": "article, tutorial, guide, blog post, documentation",
    "paper": "academic paper, research, study",
    "code": "source code, script, configuration file",
    "notiz": "personal note, memo, reminder",
}

_PROMPT_HEADER = """\
You are a strict document classifier. Read the content carefully and classify it \
into exactly ONE of these categories:

{category_lines}
Choose the MOST SPECIFIC category. An invoice is "rechnung", not "vertrag".
A tutorial is "artikel", not "code" (even if it contains code examples).

Return ONLY a JSON object (no other text, no markdown):
{{"category": "...", "confidence": <float 0.0-1.0, 0.5=ambiguous, 0.9+=certain>, \
"summary": "one line in document language", "tags": ["..."], "language": "de or en", \
"suggested_filename": "kurzer deutscher Dateiname, max 5 Wörter, \
beschreibt den Inhalt so dass man die Datei in 6 Monaten wiederfindet. \
Regeln: Leerzeichen zwischen Wörtern (KEINE Unterstriche), normale Groß/Kleinschreibung, \
nichts abkürzen, keine Dateiendung anhängen. \
Beispiele: Rechnung Telekom März 2026, Mietvertrag Schillerstraße München, Steuerbescheid 2025"}}

Content:
---
{content}
---
"""


def _build_prompt(categories: dict[str, str], content: str) -> str:
    """Build the classification prompt from a categories dict."""
    lines = "\n".join(f'- "{key}" = {desc}' for key, desc in categories.items())
    return _PROMPT_HEADER.format(category_lines=lines + "\n\n", content=content)


@dataclass
class Classification:
    """Result of classifying an item."""

    category: str
    confidence: float
    summary: str
    tags: list[str]
    language: str
    suggested_filename: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Classification:
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return cls(
            category=data.get("category", "unknown"),
            confidence=confidence,
            summary=data.get("summary", ""),
            tags=data.get("tags", []),
            language=data.get("language", "unknown"),
            suggested_filename=data.get("suggested_filename", ""),
        )

    @classmethod
    def low_confidence(cls, reason: str = "classification failed") -> Classification:
        return cls(
            category="unknown",
            confidence=0.0,
            summary=reason,
            tags=[],
            language="unknown",
            suggested_filename="",
        )


class Classifier:
    """Classifies content using an LLM backend."""

    _cloud_warning_shown = False

    def __init__(self, config: LLMConfig, arkiv_config: ArkivConfig | None = None) -> None:
        self.config = config
        self._retries: int = arkiv_config.classifier_retries if arkiv_config is not None else 3
        self._timeout: int = arkiv_config.classifier_timeout if arkiv_config is not None else 30

        # Merge categories: defaults first, config overrides
        merged: dict[str, str] = dict(DEFAULT_CATEGORIES)
        if arkiv_config is not None and arkiv_config.categories:
            merged.update(arkiv_config.categories)
        self._categories: dict[str, str] = merged

        # LiteLLM requires "ollama_chat/" prefix for Ollama models.
        # Plain "ollama/" uses legacy /api/generate which drops messages.
        if config.provider == "ollama":
            self._model_id = f"ollama_chat/{config.model}"
        elif config.provider == "openai":
            self._model_id = config.model
        else:
            self._model_id = f"{config.provider}/{config.model}"

        # Warn once if using a cloud provider
        if config.provider not in ("ollama",) and not Classifier._cloud_warning_shown:
            logger.warning(
                "Using cloud LLM provider '%s'. Document content will be "
                "sent to external servers. For privacy, use Ollama (local).",
                config.provider,
            )
            Classifier._cloud_warning_shown = True

    def classify(self, content: str, max_chars: int = 4000) -> Classification:
        """Classify text content and return structured result."""
        truncated = content[:max_chars]
        prompt = _build_prompt(self._categories, truncated)

        last_exc: Exception = Exception("no attempts made")

        for attempt in range(self._retries):
            try:
                kwargs: dict[str, Any] = {
                    "model": self._model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": self.config.temperature,
                    "max_tokens": self.config.max_tokens,
                    "timeout": self._timeout,
                }
                if self.config.base_url:
                    kwargs["api_base"] = self.config.base_url
                if self.config.api_key:
                    kwargs["api_key"] = self.config.api_key

                response = completion(**kwargs)
                raw = response.choices[0].message.content or ""

                # Extract JSON from response (handle markdown code blocks)
                raw = raw.strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

                data = json.loads(raw)
                return Classification.from_dict(data)

            except json.JSONDecodeError as e:
                logger.warning("Failed to parse LLM response as JSON: %s", e)
                return Classification.low_confidence(f"JSON parse error: {e}")
            except Exception as e:
                last_exc = e
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s",
                    attempt + 1,
                    self._retries,
                    e,
                )
                if attempt < self._retries - 1:
                    time.sleep(3**attempt)

        return Classification.low_confidence(str(last_exc))
