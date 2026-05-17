"""Tests for :mod:`pa_assistant.config`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pa_assistant.config import Settings, get_settings


def test_defaults_match_architecture_doc() -> None:
    s = Settings()
    assert s.app_env == "development"
    assert s.log_level == "INFO"
    assert s.symbol == "BTCUSDT"
    assert s.timeframe_list == ["1m", "5m", "15m", "1h", "4h", "1d"]
    assert s.api_host == "127.0.0.1"
    assert s.api_port == 8000
    assert s.coinglass_poll_interval_sec == 10
    assert s.oi_poll_interval_sec == 60
    assert s.is_production is False


def test_timeframes_csv_is_split(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIMEFRAMES", "1m, 5m,1h ")
    s = Settings()
    assert s.timeframe_list == ["1m", "5m", "1h"]


def test_app_env_production_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    s = Settings()
    assert s.is_production is True


def test_secrets_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINANCE_API_KEY", "super-secret-key")
    s = Settings()
    assert s.binance_api_key is not None

    # repr/str must never leak the secret
    assert "super-secret-key" not in repr(s)
    assert "super-secret-key" not in str(s.binance_api_key)
    # but the underlying value must still be reachable for actual API calls
    assert s.binance_api_key.get_secret_value() == "super-secret-key"


def test_invalid_log_level_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "TRACE")
    with pytest.raises(ValidationError):
        Settings()


def test_port_out_of_range_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_PORT", "70000")
    with pytest.raises(ValidationError):
        Settings()


def test_get_settings_is_cached() -> None:
    a = get_settings()
    b = get_settings()
    assert a is b


def test_unknown_env_vars_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PA_SOMETHING_RANDOM", "value")
    # Should not raise
    Settings()
