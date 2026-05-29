"""Tests for web module."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from pa_assistant.config import reset_settings
from pa_assistant.web.app import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Create test client with isolated DB."""
    db_path = tmp_path / "data" / "pa.duckdb"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DUCKDB_PATH", str(db_path))
    reset_settings()

    conn = duckdb.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kline_1m ("
        "  symbol VARCHAR, open_time TIMESTAMP, "
        "  open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, "
        "  volume DOUBLE, quote_volume DOUBLE, taker_buy_base DOUBLE"
        ")"
    )
    conn.execute(
        "INSERT INTO kline_1m VALUES "
        "('BTCUSDT', '2026-01-01 00:00:00', 100, 110, 90, 105, 1000, 50000, 600),"
        "('BTCUSDT', '2026-01-01 01:00:00', 105, 115, 95, 110, 1200, 60000, 700)"
    )
    conn.close()

    yield TestClient(app)

    reset_settings()


def test_health_endpoint(client: TestClient) -> None:
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_page(client: TestClient) -> None:
    """Test dashboard page loads."""
    response = client.get("/")
    assert response.status_code == 200
    assert "PA Assistant" in response.text


def test_liquidity_page(client: TestClient) -> None:
    """Test liquidity page loads."""
    response = client.get("/liquidity")
    assert response.status_code == 200
    assert "流动性" in response.text


def test_backtest_page(client: TestClient) -> None:
    """Test backtest page loads."""
    response = client.get("/backtest")
    assert response.status_code == 200
    assert "回放" in response.text


def test_klines_api(client: TestClient) -> None:
    """Test klines API endpoint."""
    response = client.get("/api/klines?symbol=BTCUSDT&timeframe=1h&limit=10")
    assert response.status_code == 200
    data = response.json()
    assert "bars" in data
    assert "total" in data
