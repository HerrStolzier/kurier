"""Tests for the setup wizard system detection."""

from __future__ import annotations

from arkiv.setup_wizard import (
    MODEL_RECOMMENDATIONS,
    _check_ollama_running,
    _detect_ram,
)


def test_detect_ram_returns_positive() -> None:
    """RAM detection should return a positive number on any OS."""
    ram = _detect_ram()
    assert ram > 0, "RAM detection failed — returned 0"


def test_model_recommendations_sorted_by_ram() -> None:
    """Models should be listed from highest to lowest RAM requirement."""
    prev_ram = float("inf")
    for min_ram, _model_id, name, _note in MODEL_RECOMMENDATIONS:
        assert min_ram <= prev_ram, f"{name} breaks RAM sort order"
        prev_ram = min_ram


def test_model_recommendations_have_required_fields() -> None:
    for entry in MODEL_RECOMMENDATIONS:
        assert len(entry) == 4, f"Entry should have 4 fields: {entry}"
        min_ram, model_id, _name, _note = entry
        assert isinstance(min_ram, int)
        assert isinstance(model_id, str)
        assert len(model_id) > 0


def test_ollama_check_does_not_crash() -> None:
    """Ollama check should return bool, not crash, even if not installed."""
    result = _check_ollama_running()
    assert isinstance(result, bool)
