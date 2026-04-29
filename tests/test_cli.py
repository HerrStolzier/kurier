"""CLI regression tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arkiv import cli as cli_module
from arkiv.cli import app
from arkiv.db.store import FTS_SCHEMA, TABLE_SCHEMA, Store

runner = CliRunner()


def _write_test_config(
    config_path: Path,
    *,
    inbox_dir: Path,
    review_dir: Path,
    route_dir: Path,
    db_path: Path,
) -> None:
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


def _create_broken_fts_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(TABLE_SCHEMA)
        conn.executescript(FTS_SCHEMA)
        conn.execute(
            """INSERT INTO items (
                original_path, destination, category, confidence, summary, tags,
                language, route_name, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "/tmp/rechnung.txt",
                "/tmp/archiv/rechnung.txt",
                "rechnung",
                0.9,
                "Testrechnung",
                "[]",
                "de",
                "archiv",
                "routed",
            ),
        )
        conn.execute("INSERT INTO items_fts(items_fts) VALUES('rebuild')")
        conn.execute("DROP TABLE items_fts_data")
        conn.commit()
    finally:
        conn.close()


def test_doctor_fix_creates_missing_directories(tmp_path: Path) -> None:
    """`kurier doctor --fix` should create directories declared in config."""
    inbox_dir = tmp_path / "Kurier" / "Eingang"
    review_dir = tmp_path / "Kurier" / "Pruefen"
    route_dir = tmp_path / "Kurier" / "Archiv"
    db_path = tmp_path / ".local" / "share" / "kurier" / "kurier.db"
    config_path = tmp_path / "config.toml"

    _write_test_config(
        config_path,
        inbox_dir=inbox_dir,
        review_dir=review_dir,
        route_dir=route_dir,
        db_path=db_path,
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

    _write_test_config(
        config_path,
        inbox_dir=inbox_dir,
        review_dir=review_dir,
        route_dir=route_dir,
        db_path=db_path,
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
    assert "Hintergrunddienst ist aus" in result.output
    assert "1 Datei" in result.output


def test_init_quick_creates_notizen_route_and_next_step(tmp_path: Path, monkeypatch) -> None:
    """Quick init should explain the next step and include the default notes route."""
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(cli_module, "DEFAULT_CONFIG_DIR", tmp_path / ".config" / "kurier")
    monkeypatch.setattr(cli_module.Path, "home", lambda: tmp_path)

    result = runner.invoke(app, ["init", "--quick", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "kurier service on" in result.output
    assert "Auto-Sortierung ist noch aus" in result.output
    config_text = config_path.read_text(encoding="utf-8")
    assert "[routes.notizen]" in config_text
    assert 'categories = ["notiz"]' in config_text


def test_doctor_repair_db_rebuilds_broken_fts_index(tmp_path: Path, monkeypatch) -> None:
    """`kurier doctor --repair-db` should rebuild derived FTS data without deleting items."""
    inbox_dir = tmp_path / "Kurier" / "Eingang"
    review_dir = tmp_path / "Kurier" / "Pruefen"
    route_dir = tmp_path / "Kurier" / "Archiv"
    db_path = tmp_path / ".local" / "share" / "kurier" / "kurier.db"
    config_path = tmp_path / "config.toml"

    inbox_dir.mkdir(parents=True)
    review_dir.mkdir(parents=True)
    route_dir.mkdir(parents=True)
    _write_test_config(
        config_path,
        inbox_dir=inbox_dir,
        review_dir=review_dir,
        route_dir=route_dir,
        db_path=db_path,
    )
    _create_broken_fts_db(db_path)
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

    with pytest.raises(sqlite3.DatabaseError):
        Store(db_path)

    result = runner.invoke(app, ["doctor", "--config", str(config_path), "--repair-db"])

    assert result.exit_code == 0
    assert "Datenbank-Reparatur" in result.output
    assert "FTS neu aufgebaut" in result.output
    assert list(db_path.parent.glob("kurier.db.*.bak"))
    recent = Store(db_path).recent(limit=1)
    assert recent[0]["display_title"] == "rechnung.txt"


def test_doctor_repair_db_refuses_when_service_runs(tmp_path: Path, monkeypatch) -> None:
    """Repair should fail clearly while the background service is running."""
    inbox_dir = tmp_path / "Kurier" / "Eingang"
    review_dir = tmp_path / "Kurier" / "Pruefen"
    route_dir = tmp_path / "Kurier" / "Archiv"
    db_path = tmp_path / ".local" / "share" / "kurier" / "kurier.db"
    config_path = tmp_path / "config.toml"

    inbox_dir.mkdir(parents=True)
    review_dir.mkdir(parents=True)
    route_dir.mkdir(parents=True)
    _write_test_config(
        config_path,
        inbox_dir=inbox_dir,
        review_dir=review_dir,
        route_dir=route_dir,
        db_path=db_path,
    )
    Store(db_path)
    monkeypatch.setattr(
        "arkiv.service.status",
        lambda: {
            "installed": True,
            "running": True,
            "pid": 123,
            "log_path": None,
            "recent_logs": [],
        },
    )

    result = runner.invoke(app, ["doctor", "--config", str(config_path), "--repair-db"])

    assert result.exit_code == 1
    assert "Hintergrunddienst läuft" in result.output
    assert "kurier service off" in result.output


def test_doctor_repair_db_failure_uses_error_exit(tmp_path: Path, monkeypatch) -> None:
    """Repair exceptions should not look successful to scripts."""
    inbox_dir = tmp_path / "Kurier" / "Eingang"
    review_dir = tmp_path / "Kurier" / "Pruefen"
    route_dir = tmp_path / "Kurier" / "Archiv"
    db_path = tmp_path / ".local" / "share" / "kurier" / "kurier.db"
    config_path = tmp_path / "config.toml"

    inbox_dir.mkdir(parents=True)
    review_dir.mkdir(parents=True)
    route_dir.mkdir(parents=True)
    _write_test_config(
        config_path,
        inbox_dir=inbox_dir,
        review_dir=review_dir,
        route_dir=route_dir,
        db_path=db_path,
    )
    Store(db_path)
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
    monkeypatch.setattr(
        Store,
        "repair_derived_indexes",
        staticmethod(lambda _path: (_ for _ in ()).throw(sqlite3.DatabaseError("boom"))),
    )

    result = runner.invoke(app, ["doctor", "--config", str(config_path), "--repair-db"])

    assert result.exit_code == 1
    assert "Datenbank-Reparatur" in result.output
    assert "boom" in result.output


def test_status_explains_database_repair_command(tmp_path: Path) -> None:
    """Status should explain the repair path instead of showing a raw traceback."""
    inbox_dir = tmp_path / "Kurier" / "Eingang"
    review_dir = tmp_path / "Kurier" / "Pruefen"
    route_dir = tmp_path / "Kurier" / "Archiv"
    db_path = tmp_path / ".local" / "share" / "kurier" / "kurier.db"
    config_path = tmp_path / "config.toml"

    inbox_dir.mkdir(parents=True)
    review_dir.mkdir(parents=True)
    route_dir.mkdir(parents=True)
    _write_test_config(
        config_path,
        inbox_dir=inbox_dir,
        review_dir=review_dir,
        route_dir=route_dir,
        db_path=db_path,
    )
    _create_broken_fts_db(db_path)

    result = runner.invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Datenbank konnte nicht geöffnet werden" in result.output
    assert "--repair-db" in result.output
