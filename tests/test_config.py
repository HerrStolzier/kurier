"""Tests for configuration loading."""

from pathlib import Path

from lotse.core.config import LotseConfig


def test_default_config() -> None:
    config = LotseConfig()
    assert config.llm.provider == "ollama"
    assert config.llm.model == "qwen3.5:4b"
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

    config = LotseConfig.load(config_file)
    assert config.llm.provider == "openai"
    assert config.llm.model == "gpt-4o-mini"
    assert "docs" in config.routes
    assert config.routes["docs"].confidence_threshold == 0.8


def test_load_nonexistent_falls_back_to_defaults(tmp_path: Path) -> None:
    config = LotseConfig.load(tmp_path / "nonexistent.toml")
    assert config.llm.provider == "ollama"
