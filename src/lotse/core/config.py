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
    model: str = "qwen2.5:7b"
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
    store_content: bool = True  # Store document text in DB (disable for max privacy)


class RouteConfig(BaseModel):
    """A single route definition."""

    type: str = "folder"
    path: str | None = None
    url: str | None = None
    categories: list[str] = Field(default_factory=list)
    confidence_threshold: float = 0.7


class AuditConfig(BaseModel):
    """Audit system configuration."""

    similarity_threshold: float = 0.92  # Embedding similarity for duplicate detection
    confidence_threshold: float = 0.6  # Warn below this confidence
    reclassify_sample: int = 10  # How many items to re-classify per audit


class LotseConfig(BaseSettings):
    """Root configuration for Lotse."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    routes: dict[str, RouteConfig] = Field(default_factory=dict)
    inbox_dir: Path = DEFAULT_DATA_DIR / "inbox"
    review_dir: Path = DEFAULT_DATA_DIR / "review"
    log_level: str = "INFO"
    categories: dict[str, str] | None = None
    watch_max_concurrent: int = 3
    classifier_retries: int = 3
    classifier_timeout: int = 30

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
        """Create required directories with restrictive permissions."""
        import contextlib
        import os

        for d in (self.database.path.parent, self.inbox_dir, self.review_dir):
            d.mkdir(parents=True, exist_ok=True)
            # Owner-only access for directories holding sensitive data
            with contextlib.suppress(OSError):
                os.chmod(d, 0o700)
