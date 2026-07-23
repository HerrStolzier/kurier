"""Characterization tests for the Kurier CLI surface."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from arkiv.cli import app
from arkiv.db.store import Store

runner = CliRunner()


def _write_config(config_path: Path, db_path: Path, inbox_dir: Path, review_dir: Path) -> None:
    config_path.write_text(
        "\n".join(
            [
                "[llm]",
                'provider = "ollama"',
                'model = "qwen2.5:7b"',
                'base_url = "http://localhost:11434"',
                "",
                "[database]",
                f'path = "{db_path}"',
                "",
                f'inbox_dir = "{inbox_dir}"',
                f'review_dir = "{review_dir}"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_help_lists_expected_primary_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command_name in (
        "add",
        "watch",
        "import-email",
        "fetch-email",
        "search",
        "status",
        "init",
        "doctor",
        "audit",
        "beta",
        "eval",
        "serve",
        "plugins",
        "undo",
        "export",
        "tui",
        "service",
    ):
        assert command_name in result.stdout


def test_version_flag_prints_version_and_exits_cleanly() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "kurier" in result.stdout.lower()


def test_status_reports_empty_database_without_crashing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    db_path = tmp_path / "state" / "kurier.db"
    inbox_dir = tmp_path / "inbox"
    review_dir = tmp_path / "review"
    _write_config(config_path, db_path, inbox_dir, review_dir)

    result = runner.invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Kurier" in result.stdout
    assert "Noch keine Dokumente verarbeitet." in result.stdout
    assert "Ablage-Daten:" in result.stdout
    assert db_path.name in result.stdout


def test_status_explains_next_steps_and_integrations(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    db_path = tmp_path / "state" / "kurier.db"
    inbox_dir = tmp_path / "inbox"
    review_dir = tmp_path / "review"
    _write_config(config_path, db_path, inbox_dir, review_dir)
    store = Store(db_path)
    store.record_item(
        original_path="/tmp/note.txt",
        destination="/archive/note.txt",
        category="notiz",
        confidence=0.9,
        summary="Notiz",
        tags=[],
        language="de",
        route_name="archiv",
    )

    result = runner.invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Integrationen:" in result.stdout
    assert "keine offenen Webhooks" in result.stdout
    assert "Nächster Schritt:" in result.stdout
    assert "Zuletzt erledigt:" in result.stdout
    assert "note.txt" in result.stdout


def test_doctor_reports_missing_config_file(tmp_path: Path) -> None:
    missing_config = tmp_path / "missing.toml"

    result = runner.invoke(app, ["doctor", "--config", str(missing_config)])

    assert result.exit_code == 0
    assert "Kurier Gesundheitscheck" in result.stdout
    assert "Einstellungen" in result.stdout
    assert "Nicht gefunden" in result.stdout


def test_plugins_reports_empty_state_without_error(monkeypatch) -> None:
    class DummyPluginManager:
        def list_plugins(self) -> list[str]:
            return []

    monkeypatch.setattr("arkiv.plugins.manager.PluginManager", DummyPluginManager)

    result = runner.invoke(app, ["plugins"])

    assert result.exit_code == 0
    assert "Keine Erweiterungen installiert." in result.stdout


def test_search_help_uses_user_facing_language() -> None:
    result = runner.invoke(app, ["search", "--help"])

    assert result.exit_code == 0
    assert "Suchfrage in normaler Sprache" in result.stdout
    assert "Lasse Kurier unklare Suchfragen lokal verbessern" in result.stdout


def test_beta_report_empty_state(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    db_path = tmp_path / "state" / "kurier.db"
    inbox_dir = tmp_path / "inbox"
    review_dir = tmp_path / "review"
    _write_config(config_path, db_path, inbox_dir, review_dir)

    result = runner.invoke(app, ["beta", "report", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Kurier Beta-Bericht" in result.stdout
    assert "Noch keine Stolperer notiert." in result.stdout
    assert "5-Tage-Alltagstest" in result.stdout


def test_beta_report_prioritizes_next_product_actions(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    db_path = tmp_path / "state" / "kurier.db"
    inbox_dir = tmp_path / "inbox"
    review_dir = tmp_path / "review"
    _write_config(config_path, db_path, inbox_dir, review_dir)
    store = Store(db_path)
    store.record_beta_event("search_no_results", "Suche ohne Treffer", severity="warn")
    store.record_beta_event("upload_failed", "Upload fehlgeschlagen", severity="error")

    result = runner.invoke(app, ["beta", "report", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Was sollte als Nächstes verbessert werden?" in result.stdout
    assert "Import-Vertrauen prüfen" in result.stdout
    assert "Suche verständlicher machen" in result.stdout


def test_serve_requires_api_key_for_non_loopback_even_with_force(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    db_path = tmp_path / "state" / "kurier.db"
    inbox_dir = tmp_path / "inbox"
    review_dir = tmp_path / "review"
    _write_config(config_path, db_path, inbox_dir, review_dir)

    result = runner.invoke(
        app,
        ["serve", "--host", "0.0.0.0", "--force", "--config", str(config_path)],
    )

    assert result.exit_code == 1
    assert "--api-key" in result.stdout
    assert "--force deaktiviert" in result.stdout


def test_serve_allows_non_loopback_with_api_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    db_path = tmp_path / "state" / "kurier.db"
    inbox_dir = tmp_path / "inbox"
    review_dir = tmp_path / "review"
    _write_config(config_path, db_path, inbox_dir, review_dir)

    with patch("uvicorn.run") as run:
        result = runner.invoke(
            app,
            [
                "serve",
                "--host",
                "0.0.0.0",
                "--api-key",
                "test-secret",
                "--config",
                str(config_path),
            ],
        )

    assert result.exit_code == 0
    run.assert_called_once()


def test_init_quick_with_config_in_new_directory(tmp_path):
    """`kurier init --config` in ein noch nicht existierendes Verzeichnis."""
    from typer.testing import CliRunner

    from arkiv.cli import app

    config_path = tmp_path / "neu" / "tiefer" / "config.toml"
    result = CliRunner().invoke(app, ["init", "--quick", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    assert config_path.exists()
