"""CLI regression tests."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from arkiv.cli import app
from arkiv.commands import setup as setup_commands

runner = CliRunner()


def test_doctor_fix_creates_missing_directories(tmp_path: Path) -> None:
    """`kurier doctor --fix` should create directories declared in config."""
    inbox_dir = tmp_path / "Kurier" / "Eingang"
    review_dir = tmp_path / "Kurier" / "Pruefen"
    route_dir = tmp_path / "Kurier" / "Archiv"
    db_path = tmp_path / ".local" / "share" / "kurier" / "kurier.db"
    config_path = tmp_path / "config.toml"

    config_path.write_text(
        "\n".join(
            [
                f'inbox_dir = "{inbox_dir}"',
                f'review_dir = "{review_dir}"',
                "",
                "[llm]",
                'provider = "openai"',
                'model = "gpt-4o-mini"',
                "",
                "[database]",
                f'path = "{db_path}"',
                "",
                "[routes.archiv]",
                'type = "folder"',
                f'path = "{route_dir}"',
                'categories = ["rechnung"]',
                "confidence_threshold = 0.7",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["doctor", "--config", str(config_path), "--fix"])

    assert result.exit_code == 0
    assert inbox_dir.exists()
    assert review_dir.exists()
    assert route_dir.exists()
    assert db_path.parent.exists()
    assert "Angelegt:" in result.output


def test_doctor_warns_when_auto_sort_is_off_and_files_wait(tmp_path: Path, monkeypatch) -> None:
    """Doctor should explain when auto-sorting is off but inbox files are waiting."""
    inbox_dir = tmp_path / "Kurier" / "Eingang"
    review_dir = tmp_path / "Kurier" / "Pruefen"
    route_dir = tmp_path / "Kurier" / "Archiv"
    db_path = tmp_path / ".local" / "share" / "kurier" / "kurier.db"
    config_path = tmp_path / "config.toml"

    inbox_dir.mkdir(parents=True)
    review_dir.mkdir(parents=True)
    route_dir.mkdir(parents=True)
    (inbox_dir / "wartet.pdf").write_text("test", encoding="utf-8")

    config_path.write_text(
        "\n".join(
            [
                f'inbox_dir = "{inbox_dir}"',
                f'review_dir = "{review_dir}"',
                "",
                "[llm]",
                'provider = "openai"',
                'model = "gpt-4o-mini"',
                "",
                "[database]",
                f'path = "{db_path}"',
                "",
                "[routes.archiv]",
                'type = "folder"',
                f'path = "{route_dir}"',
                'categories = ["rechnung"]',
                "confidence_threshold = 0.7",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "arkiv.service.status",
        lambda: {
            "installed": False,
            "running": False,
            "pid": None,
            "log_path": None,
            "recent_logs": [],
        },
    )

    result = runner.invoke(app, ["doctor", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Auto-Sortierung" in result.output
    assert "Automatische Sortierung ist aus" in result.output
    assert "1 Datei" in result.output


def test_init_quick_creates_notizen_route_and_next_step(tmp_path: Path, monkeypatch) -> None:
    """Quick init should explain the next step and include the default notes route."""
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(setup_commands.Path, "home", lambda: tmp_path)

    result = runner.invoke(app, ["init", "--quick", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "kurier service on" in result.output
    assert "Auto-Sortierung ist noch aus" in result.output
    config_text = config_path.read_text(encoding="utf-8")
    assert "[routes.notizen]" in config_text
    assert 'categories = ["notiz"]' in config_text
    assert '[explanation]\nenabled = true\nmodel = "qwen3.5:9b"' in config_text
    config = setup_commands.ArkivConfig.load(config_path)
    assert config.inbox_dir == tmp_path / "Documents" / "Kurier" / "Eingang"
    assert config.review_dir == tmp_path / "Documents" / "Kurier" / "Prüfen"


def test_service_status_uses_human_readable_labels(tmp_path: Path, monkeypatch) -> None:
    """Service status should avoid internal labels like PID, Log, or technical inbox wording."""
    config_path = tmp_path / "config.toml"
    inbox_dir = tmp_path / "Kurier" / "Eingang"
    review_dir = tmp_path / "Kurier" / "Pruefen"
    db_path = tmp_path / "kurier.db"
    inbox_dir.mkdir(parents=True)
    review_dir.mkdir(parents=True)

    config_path.write_text(
        "\n".join(
            [
                f'inbox_dir = "{inbox_dir}"',
                f'review_dir = "{review_dir}"',
                "",
                "[llm]",
                'provider = "openai"',
                'model = "gpt-4o-mini"',
                "",
                "[database]",
                f'path = "{db_path}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "arkiv.service.status",
        lambda: {
            "installed": True,
            "running": True,
            "pid": 1234,
            "log_path": "",
            "recent_logs": [],
        },
    )

    result = runner.invoke(app, ["service", "status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Kurier Auto-Sortierung" in result.output
    assert "Zustand" in result.output
    assert "Eingang" in result.output
    assert "Prozess 1234" in result.output
