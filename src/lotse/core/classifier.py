"""LLM-powered classification engine."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import litellm
from litellm import completion

from lotse.core.config import LLMConfig

# Disable LiteLLM telemetry — user data must never leave the system
litellm.telemetry = False

logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = """\
You are a document classifier. Analyze the following content and classify it.

Return a JSON object with exactly these fields:
- "category": a short lowercase label (e.g., "rechnung", "vertrag", "artikel", "code")
- "confidence": a float between 0.0 and 1.0
- "summary": a one-line summary of the content
- "tags": a list of relevant tags
- "language": detected language code (e.g., "de", "en")

Content to classify:
---
{content}
---

Respond with ONLY the JSON object, no other text.
"""


@dataclass
class Classification:
    """Result of classifying an item."""

    category: str
    confidence: float
    summary: str
    tags: list[str]
    language: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Classification:
        return cls(
            category=data.get("category", "unknown"),
            confidence=float(data.get("confidence", 0.0)),
            summary=data.get("summary", ""),
            tags=data.get("tags", []),
            language=data.get("language", "unknown"),
        )

    @classmethod
    def low_confidence(cls, reason: str = "classification failed") -> Classification:
        return cls(
            category="unknown",
            confidence=0.0,
            summary=reason,
            tags=[],
            language="unknown",
        )


class Classifier:
    """Classifies content using an LLM backend."""

    _cloud_warning_shown = False

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._model_id = (
            f"{config.provider}/{config.model}"
            if config.provider != "openai"
            else config.model
        )

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

        try:
            kwargs: dict[str, Any] = {
                "model": self._model_id,
                "messages": [
                    {"role": "user", "content": CLASSIFICATION_PROMPT.format(content=truncated)}
                ],
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
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
            logger.error("Classification failed: %s", e)
            return Classification.low_confidence(str(e))
