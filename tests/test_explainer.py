"""Tests for the local plain-language document explanation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from arkiv.core.config import ArkivConfig, ExplanationConfig
from arkiv.core.explainer import DocumentExplainer, ExplanationError


def _response(payload: dict[str, object]) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(payload)
    return response


def test_explanations_are_opt_in_by_default() -> None:
    assert ExplanationConfig().enabled is False


def test_explainer_returns_only_points_with_source_ids() -> None:
    content = "Der Vertrag kostet 20 Euro im Monat. Er kann mit einem Monat Frist gekündigt werden."
    payload: dict[str, object] = {
        "kurz_gesagt": {
            "text": "Der Vertrag kostet monatlich Geld und kann gekündigt werden.",
            "source_ids": ["S1"],
        },
        "punkte": [
            {
                "thema": "Kosten",
                "erklaerung": "Du zahlst 20 Euro im Monat.",
                "source_ids": ["S1"],
            },
            {
                "thema": "Ohne Beleg",
                "erklaerung": "Das darf nicht erscheinen.",
                "source_ids": [],
            },
        ],
        "offene_punkte": [],
    }

    with patch("arkiv.core.explainer.completion", return_value=_response(payload)) as complete:
        result = DocumentExplainer(ExplanationConfig(enabled=True)).explain(content)

    assert result.overview.startswith("Der Vertrag")
    assert result.overview_sources[0].identifier == "S1"
    assert result.source_is_partial is False
    assert len(result.points) == 1
    assert result.points[0].topic == "Kosten"
    assert result.points[0].sources[0].text == content
    assert complete.call_args.kwargs["model"] == "ollama_chat/qwen3.5:9b"
    assert complete.call_args.kwargs["think"] is False
    assert complete.call_args.kwargs["api_base"] == "http://localhost:11434"


def test_explainer_retries_and_fails_without_source_ids() -> None:
    payload: dict[str, object] = {
        "kurz_gesagt": {"text": "Eine Erklärung ohne Beleg.", "source_ids": []},
        "punkte": [{"thema": "Kosten", "erklaerung": "20 Euro", "source_ids": []}],
        "offene_punkte": [],
    }

    with (
        patch("arkiv.core.explainer.completion", return_value=_response(payload)) as complete,
        pytest.raises(ExplanationError, match="belegt"),
    ):
        DocumentExplainer(ExplanationConfig(enabled=True)).explain("Der Vertrag kostet 20 Euro.")

    assert complete.call_count == 2


def test_explainer_marks_a_limited_source_as_partial() -> None:
    payload: dict[str, object] = {
        "kurz_gesagt": {"text": "Eine kurze Erklärung.", "source_ids": ["S1"]},
        "punkte": [{"thema": "Kosten", "erklaerung": "Ein Punkt.", "source_ids": ["S1"]}],
        "offene_punkte": [],
    }
    config = ExplanationConfig(enabled=True, max_source_characters=20)

    with patch("arkiv.core.explainer.completion", return_value=_response(payload)):
        result = DocumentExplainer(config).explain("A" * 21)

    assert result.source_is_partial is True


def test_explainer_keeps_partial_warning_when_boundary_is_whitespace() -> None:
    payload: dict[str, object] = {
        "kurz_gesagt": {"text": "Eine kurze Erklärung.", "source_ids": ["S1"]},
        "punkte": [{"thema": "Kosten", "erklaerung": "Ein Punkt.", "source_ids": ["S1"]}],
        "offene_punkte": [],
    }
    config = ExplanationConfig(enabled=True, max_source_characters=20)

    with patch("arkiv.core.explainer.completion", return_value=_response(payload)):
        result = DocumentExplainer(config).explain("A" * 20 + " " + "später")

    assert result.source_is_partial is True


def test_explanation_model_check_requires_the_configured_model() -> None:
    from arkiv.application import explain as explain_workflow

    explain_workflow._model_availability_cache.clear()
    context = MagicMock()
    context.config.explanation = ExplanationConfig(enabled=True)
    response = MagicMock()
    response.read.return_value = json.dumps({"models": [{"name": "qwen3.5:9b"}]}).encode()

    with patch("arkiv.application.explain.urlopen") as open_url:
        open_url.return_value.__enter__.return_value = response

        assert explain_workflow.explanation_model_available(context) is True
        assert explain_workflow.explanation_model_available(context) is True

    assert open_url.call_count == 1


def test_explanation_model_check_accepts_ollama_latest_alias() -> None:
    from arkiv.application import explain as explain_workflow

    explain_workflow._model_availability_cache.clear()
    context = MagicMock()
    context.config.explanation = ExplanationConfig(enabled=True, model="qwen3.5")
    response = MagicMock()
    response.read.return_value = json.dumps({"models": [{"name": "qwen3.5:latest"}]}).encode()

    with patch("arkiv.application.explain.urlopen") as open_url:
        open_url.return_value.__enter__.return_value = response

        assert explain_workflow.explanation_model_available(context) is True


def test_text_ingest_keeps_full_content_when_storage_is_enabled(tmp_path) -> None:
    from arkiv.core.classifier import Classification
    from arkiv.core.engine import Engine

    config = ArkivConfig(
        database={"path": tmp_path / "test.db", "store_content": True},
        explanation={"enabled": True},
    )
    text = "A" * 9000
    engine = Engine(config)
    with patch.object(
        engine.classifier,
        "classify",
        return_value=Classification("vertrag", 0.9, "Vertrag", [], "de"),
    ):
        engine.ingest_text(text, name="vertrag")

    assert engine.store.get_recent()[0]["content_text"] == text[:8001]


def test_disabled_explanation_keeps_previous_text_storage_limit(tmp_path) -> None:
    from arkiv.core.classifier import Classification
    from arkiv.core.engine import Engine

    config = ArkivConfig(database={"path": tmp_path / "test.db", "store_content": True})
    engine = Engine(config)
    with patch.object(
        engine.classifier,
        "classify",
        return_value=Classification("vertrag", 0.9, "Vertrag", [], "de"),
    ):
        engine.ingest_text("A" * 9000, name="vertrag")

    assert engine.store.get_recent()[0]["content_text"] == "A" * 2000


def test_enabled_explanation_keeps_non_contract_storage_limit(tmp_path) -> None:
    from arkiv.core.classifier import Classification
    from arkiv.core.engine import Engine

    config = ArkivConfig(
        database={"path": tmp_path / "test.db", "store_content": True},
        explanation={"enabled": True},
    )
    engine = Engine(config)
    with patch.object(
        engine.classifier,
        "classify",
        return_value=Classification("notiz", 0.9, "Notiz", [], "de"),
    ):
        engine.ingest_text("A" * 9000, name="notiz")

    assert engine.store.get_recent()[0]["content_text"] == "A" * 2000


def test_file_explanation_reads_beyond_classification_excerpt(tmp_path) -> None:
    from arkiv.core.engine import Engine

    path = tmp_path / "vertrag.txt"
    text = "A" * 9000
    path.write_text(text, encoding="utf-8")

    content, is_partial = Engine(
        ArkivConfig(database={"path": tmp_path / "test.db"})
    ).extract_document_text(path)

    assert content == text[:8001]
    assert is_partial is True


def test_legacy_text_excerpt_is_conservatively_marked_partial() -> None:
    from arkiv.application.explain import _read_document_text

    context = MagicMock()

    content, is_partial = _read_document_text(
        context,
        {"original_path": "text://alter-vertrag", "content_text": "A" * 2000},
    )

    assert content == "A" * 2000
    assert is_partial is True


def test_explanation_hides_unsupported_existing_files(tmp_path) -> None:
    from arkiv.application.explain import document_is_explainable

    unsupported = tmp_path / "vertrag.docx"
    unsupported.touch()
    supported = tmp_path / "vertrag.txt"
    supported.touch()

    assert document_is_explainable({"destination": str(unsupported)}) is False
    assert document_is_explainable({"destination": str(supported)}) is True


@pytest.mark.parametrize(
    ("category", "status"),
    [("rechnung", "routed"), ("vertrag", "duplicate")],
)
def test_explain_document_rejects_non_contract_scope(category: str, status: str) -> None:
    from arkiv.application.explain import explain_document

    context = MagicMock()
    context.engine.store.get_item.return_value = {
        "category": category,
        "status": status,
        "original_path": "text://dokument",
        "content_text": "Text",
    }

    with pytest.raises(ExplanationError, match="nur für Verträge"):
        explain_document(context, 42)
