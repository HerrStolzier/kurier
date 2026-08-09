"""Tests for the classifier (unit tests with mocked LLM)."""

import json
from unittest.mock import MagicMock, patch

from arkiv.core.classifier import (
    Classification,
    Classifier,
    _build_prompt,
    _postprocess_classification,
)
from arkiv.core.config import ArkivConfig, LLMConfig


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

    with patch("arkiv.core.classifier.completion") as mock_llm:
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

    with patch("arkiv.core.classifier.completion") as mock_llm:
        mock = MagicMock()
        mock.choices = [MagicMock()]
        mock.choices[0].message.content = f"```json\n{json.dumps(json_data)}\n```"
        mock_llm.return_value = mock

        result = classifier.classify("Python async patterns")

    assert result.category == "artikel"


def test_classify_returns_low_confidence_on_error() -> None:
    config = LLMConfig(provider="ollama", model="mistral")
    classifier = Classifier(config)

    with patch("arkiv.core.classifier.completion") as mock_llm:
        mock_llm.side_effect = Exception("Connection refused")
        result = classifier.classify("some content")

    assert result.category == "unknown"
    assert result.confidence == 0.0


def test_prompt_warns_not_to_copy_example_entities() -> None:
    prompt = _build_prompt({"rechnung": "invoice"}, "Rechnung der Stadtwerke")

    assert "keine Namen/Organisationen erfinden" in prompt
    assert "muss dieser Name im suggested_filename vorkommen" in prompt
    assert "keine zu kopierenden Inhalte" in prompt
    assert "Rechnung Telekom" not in prompt


def test_postprocess_replaces_generic_invoice_provider_with_issuer() -> None:
    classification = Classification(
        category="rechnung",
        confidence=0.95,
        summary="Stadtwerke Musterstadt Rechnung",
        tags=["rechnung"],
        language="de",
        suggested_filename="Rechnung Anbieter Juli 2026",
    )

    content = "Rechnung der Stadtwerke Musterstadt\nLeistungszeitraum: Juli 2026\n42 EUR"
    result = _postprocess_classification(content, classification)

    assert result.suggested_filename == "Rechnung Stadtwerke Musterstadt Juli 2026"


def test_postprocess_replaces_abbreviated_invoice_provider_with_issuer() -> None:
    classification = Classification(
        category="rechnung",
        confidence=0.95,
        summary="Stadtwerke Musterstadt Rechnung",
        tags=["rechnung"],
        language="de",
        suggested_filename="Rechnung StW Musterstadt July26",
    )
    content = "Rechnung der Stadtwerke Musterstadt\nLeistungszeitraum: Juli 2026\n42 EUR"

    result = _postprocess_classification(content, classification)

    assert result.suggested_filename == "Rechnung Stadtwerke Musterstadt Juli 2026"


def test_postprocess_replaces_underscores_in_suggested_filename() -> None:
    classification = Classification(
        category="notiz",
        confidence=0.9,
        summary="Teammeeting",
        tags=["notiz"],
        language="de",
        suggested_filename="Teammeeting_Projekt_Kurier",
    )

    result = _postprocess_classification("Teammeeting Projekt Kurier", classification)

    assert result.suggested_filename == "Teammeeting Projekt Kurier"


def test_disabled_categories_entfernt_eingebaute_kategorie() -> None:
    cfg = ArkivConfig(disabled_categories=["rechnung"])
    c = Classifier(cfg.llm, arkiv_config=cfg)

    assert "rechnung" not in c._categories
    assert "notiz" in c._categories


def test_eigene_kategorien_stehen_im_prompt_vor_den_eingebauten() -> None:
    cfg = ArkivConfig(categories={"gesundheit": "Arzt, Zahnarzt"})
    c = Classifier(cfg.llm, arkiv_config=cfg)

    reihenfolge = list(c._categories)
    assert reihenfolge[0] == "gesundheit"
    assert reihenfolge.index("gesundheit") < reihenfolge.index("rechnung")


def test_ueberschriebene_kategorie_behaelt_eigene_beschreibung() -> None:
    cfg = ArkivConfig(categories={"rechnung": "nur was sonst nirgends passt"})
    c = Classifier(cfg.llm, arkiv_config=cfg)

    assert c._categories["rechnung"] == "nur was sonst nirgends passt"


def test_erfundene_kategorie_landet_in_der_pruefliste() -> None:
    """Modelle antworten mit Woertern aus dem Dokument, auch wenn die Kategorie
    gar nicht angeboten wurde. Ungeprueft uebernommen trifft sie keine Route."""
    cfg = ArkivConfig(disabled_categories=["rechnung"])
    c = Classifier(cfg.llm, arkiv_config=cfg)

    result = c._reject_unknown_category(
        Classification(
            category="rechnung",
            confidence=0.95,
            summary="Zahnarztrechnung",
            tags=[],
            language="de",
        )
    )

    assert result.category == "unknown"
    assert result.confidence == 0.0
    assert result.summary == "Zahnarztrechnung"


def test_angebotene_kategorie_bleibt_unveraendert() -> None:
    cfg = ArkivConfig()
    c = Classifier(cfg.llm, arkiv_config=cfg)

    original = Classification(
        category="notiz", confidence=0.9, summary="Notiz", tags=[], language="de"
    )

    assert c._reject_unknown_category(original) is original


def test_kategorie_als_liste_landet_ohne_absturz_in_der_pruefliste() -> None:
    """Modelle liefern manchmal {"category": ["rechnung"]} — eine Liste als
    dict-Schluessel wuerde TypeError werfen und einen Retry ausloesen."""
    cfg = ArkivConfig()
    c = Classifier(cfg.llm, arkiv_config=cfg)

    result = c._reject_unknown_category(
        Classification(
            category=["rechnung"],  # type: ignore[arg-type]
            confidence=0.9,
            summary="Test",
            tags=[],
            language="de",
        )
    )

    assert result.category == "unknown"
