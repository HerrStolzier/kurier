"""Configuration management for Lotse."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# XDG-style defaults
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "lotse"
DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "lotse"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.toml"


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: str = "ollama"
    model: str = "mistral"
    base_url: str | None = "http://localhost:11434"
    api_key: str | None = None
    temperature: float = 0.1
    max_tokens: int = 1024


class EmbeddingConfig(BaseModel):
    """Embedding model configuration."""

    model: str = "BAAI/bge-small-en-v1.5"
    cache_dir: Path | None = None


class DatabaseConfig(BaseModel):
    """Database configuration."""

    path: Path = DEFAULT_DATA_DIR / "lotse.db"


class RouteConfig(BaseModel):
    """A single route definition."""

    type: str = "folder"
    path: str | None = None
    url: str | None = None
    categories: list[str] = Field(default_factory=list)
    confidence_threshold: float = 0.7


class LotseConfig(BaseSettings):
    """Root configuration for Lotse."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    routes: dict[str, RouteConfig] = Field(default_factory=dict)
    inbox_dir: Path = DEFAULT_DATA_DIR / "inbox"
    review_dir: Path = DEFAULT_DATA_DIR / "review"
    log_level: str = "INFO"

    @classmethod
    def load(cls, config_path: Path | None = None) -> LotseConfig:
        """Load configuration from TOML file, falling back to defaults."""
        path = config_path or DEFAULT_CONFIG_FILE

        if path.exists():
            with open(path, "rb") as f:
                data: dict[str, Any] = tomllib.load(f)
            return cls(**data)

        return cls()

    def ensure_dirs(self) -> None:
        """Create required directories if they don't exist."""
        self.database.path.parent.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.review_dir.mkdir(parents=True, exist_ok=True)
