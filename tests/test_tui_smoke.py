"""Characterization tests for the Kurier Textual app."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import ListView, Static

from arkiv.core.config import ArkivConfig
from arkiv.tui.app import MENU_ITEMS, ArkivApp, HomeScreen, SetupWizardScreen


@pytest.fixture
def config(tmp_path: Path) -> ArkivConfig:
    return ArkivConfig(
        database={"path": tmp_path / "kurier.db"},
        inbox_dir=tmp_path / "inbox",
        review_dir=tmp_path / "review",
    )


def test_arkiv_app_alias_points_to_home_screen() -> None:
    assert ArkivApp is HomeScreen


def test_tui_menu_uses_user_facing_healthcheck_label() -> None:
    labels = [label for _key, label in MENU_ITEMS]

    assert "Gesundheitscheck" in labels
    assert "Eingang überwachen" in labels
    assert not any("Doctor" in label for label in labels)
    assert not any("Inbox" in label for label in labels)


def test_tui_first_run_enables_local_contract_explanations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr("arkiv.core.config.DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr("arkiv.core.config.DEFAULT_CONFIG_FILE", config_file)
    monkeypatch.setattr("arkiv.tui.app.Path.home", lambda: tmp_path)
    wizard = SetupWizardScreen()
    wizard._inbox_path = tmp_path / "Kurier" / "Eingang"

    wizard._do_write_config()

    loaded = ArkivConfig.load(config_file)
    assert loaded.explanation.enabled is True
    assert loaded.explanation.model == "qwen3.5:9b"


@pytest.mark.asyncio
async def test_tui_boots_into_home_screen_with_menu_and_empty_db_hint(
    config: ArkivConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # HomeScreen.on_mount pushes the setup wizard (leaving the stats bar at
    # "Lade Statistiken...") when the default config file is absent. Point it
    # at an existing file so load_stats() runs and the test stays hermetic —
    # otherwise it silently depends on the developer's real
    # ~/.config/kurier/config.toml and fails on clean CI runners.
    existing_config = tmp_path / "config.toml"
    existing_config.write_text("")
    monkeypatch.setattr("arkiv.core.config.DEFAULT_CONFIG_FILE", existing_config)

    app = ArkivApp(config)

    async with app.run_test() as _pilot:
        menu = app.query_one("#menu-list", ListView)
        stats_bar = app.query_one("#stats-bar", Static)

        assert len(menu.children) == 7
        assert "Noch keine Einträge" in str(stats_bar.render())
