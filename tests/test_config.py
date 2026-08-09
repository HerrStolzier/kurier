"""Tests for configuration loading."""

from pathlib import Path

from arkiv.core.config import ArkivConfig


def test_default_config() -> None:
    config = ArkivConfig()
    assert config.llm.provider == "ollama"
    assert config.llm.model == "qwen2.5:7b"
    assert config.log_level == "INFO"


def test_load_from_toml(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text("""\
[llm]
provider = "openai"
model = "gpt-4o-mini"
api_key = "test-key"

[routes.docs]
type = "folder"
path = "~/Documents"
categories = ["document", "letter"]
confidence_threshold = 0.8
""")

    config = ArkivConfig.load(config_file)
    assert config.llm.provider == "openai"
    assert config.llm.model == "gpt-4o-mini"
    assert "docs" in config.routes
    assert config.routes["docs"].confidence_threshold == 0.8


def test_load_nonexistent_falls_back_to_defaults(tmp_path: Path) -> None:
    config = ArkivConfig.load(tmp_path / "nonexistent.toml")
    assert config.llm.provider == "ollama"


def test_misplaced_settings_findet_verrutschte_grundeinstellungen(tmp_path: Path) -> None:
    """TOML ordnet jede Zeile der zuletzt geoeffneten Tabelle zu. inbox_dir unter
    [database] wird damit zu database.inbox_dir und wirkt nie."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[database]\npath = "/tmp/x.db"\n'
        'inbox_dir = "/tmp/eingang"\n'
        'disabled_categories = ["rechnung"]\n'
    )

    found = ArkivConfig.misplaced_settings(cfg_file)

    assert ("inbox_dir", "database") in found
    assert ("disabled_categories", "database") in found


def test_misplaced_settings_meldet_korrekte_datei_nicht(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        'inbox_dir = "/tmp/eingang"\n'
        'disabled_categories = ["rechnung"]\n\n'
        '[database]\npath = "/tmp/x.db"\n'
    )

    assert ArkivConfig.misplaced_settings(cfg_file) == []


def test_misplaced_settings_ignoriert_routen_und_fehlende_datei(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[routes.archiv]\ntype = "folder"\npath = "/tmp/a"\n')

    assert ArkivConfig.misplaced_settings(cfg_file) == []
    assert ArkivConfig.misplaced_settings(tmp_path / "gibtsnicht.toml") == []


def test_misplaced_settings_findet_verrutschte_werte_in_routen(tmp_path: Path) -> None:
    """Auch eine Route-Tabelle verschluckt Grundeinstellungen."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[routes.archiv]\ntype = "folder"\npath = "/tmp/a"\ninbox_dir = "/tmp/eingang"\n'
    )

    assert ("inbox_dir", "routes.archiv") in ArkivConfig.misplaced_settings(cfg_file)


def test_misplaced_settings_haelt_routen_namen_fuer_gueltig(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[routes.notifications]\ntype = "folder"\npath = "/tmp/a"\n')

    assert ArkivConfig.misplaced_settings(cfg_file) == []
