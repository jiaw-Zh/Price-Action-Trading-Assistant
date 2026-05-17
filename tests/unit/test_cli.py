"""Tests for the :mod:`pa_assistant.cli` Typer app."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from pa_assistant.cli import app

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "pa-assistant" in result.stdout


def test_init_db_creates_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "out.duckdb"
    monkeypatch.setenv("DUCKDB_PATH", str(db_path))

    result = runner.invoke(app, ["init-db"])
    assert result.exit_code == 0, result.stdout
    assert db_path.exists()
    assert "Schema initialized" in result.stdout


def test_show_config_masks_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINANCE_API_KEY", "super-secret-key")
    result = runner.invoke(app, ["show-config"])
    assert result.exit_code == 0
    assert "super-secret-key" not in result.stdout
