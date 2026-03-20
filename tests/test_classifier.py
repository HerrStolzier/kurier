"""Tests for the classifier (unit tests with mocked LLM)."""

import json
from unittest.mock import MagicMock, patch

from lotse.core.classifier import Classifier
from lotse.core.config import LLMConfig


def _mock_completion_response(data: dict) -> MagicMock:
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = json.dumps(data)
    return mock


def test_classify_parses_response() -> None:
    config = LLMConfig(provider="ollama", model="mistral")
    classifier = Classifier(config)

    expected = {
        "category": "rechnung",
        "confidence": 0.92,
        "summary": "Telekom invoice",
        "tags": ["telekom", "monthly"],
        "language": "de",
    }

    with patch("lotse.core.classifier.completion") as mock_llm:
        mock_llm.return_value = _mock_completion_response(expected)
        result = classifier.classify("Telekom Rechnung 2026")

    assert result.category == "rechnung"
    assert result.confidence == 0.92
    assert result.language == "de"


def test_classify_handles_markdown_wrapped_json() -> None:
    config = LLMConfig(provider="ollama", model="mistral")
    classifier = Classifier(config)

    json_data = {
        "category": "artikel",
        "confidence": 0.8,
        "summary": "Python article",
        "tags": ["python"],
        "language": "en",
    }

    with patch("lotse.core.classifier.completion") as mock_llm:
        mock = MagicMock()
        mock.choices = [MagicMock()]
        mock.choices[0].message.content = f"```json\n{json.dumps(json_data)}\n```"
        mock_llm.return_value = mock

        result = classifier.classify("Python async patterns")

    assert result.category == "artikel"


def test_classify_returns_low_confidence_on_error() -> None:
    config = LLMConfig(provider="ollama", model="mistral")
    classifier = Classifier(config)

    with patch("lotse.core.classifier.completion") as mock_llm:
        mock_llm.side_effect = Exception("Connection refused")
        result = classifier.classify("some content")

    assert result.category == "unknown"
    assert result.confidence == 0.0
