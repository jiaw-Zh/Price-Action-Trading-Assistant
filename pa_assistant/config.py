"""Application configuration via pydantic-settings.

All configuration is loaded from environment variables and (optionally) a
``.env`` file in the working directory. Secrets are wrapped in ``SecretStr``
so that ``repr()``/``str()`` never leak them into logs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
AppEnv = Literal["development", "production"]


class Settings(BaseSettings):
    """Application-wide settings.

    Field names map case-insensitively to env vars. ``.env`` is loaded if
    present in the current working directory.

    All secrets use :class:`pydantic.SecretStr` — call ``.get_secret_value()``
    to access the underlying string when actually issuing API requests.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- Application ----------
    app_env: AppEnv = "development"
    log_level: LogLevel = "INFO"
    log_json: bool = False
    duckdb_path: Path = Field(default=Path("./data/pa.duckdb"))

    # ---------- Symbol & timeframes ----------
    symbol: str = "BTCUSDT"
    # Stored as CSV to play nicely with .env files. Use ``timeframe_list``.
    timeframes: str = "1m,5m,15m,1h,4h,1d"

    # ---------- Polling intervals (seconds) ----------
    coinglass_poll_interval_sec: int = Field(default=10, ge=1, le=3600)
    oi_poll_interval_sec: int = Field(default=60, ge=5, le=3600)

    # ---------- Web API ----------
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)

    # ---------- HTTP proxy ----------
    # When set, all exchange REST clients route through this HTTP/SOCKS proxy.
    # Typical local clash setup: ``http://127.0.0.1:7890`` (mixed port).
    # Leave empty / unset to make direct connections.
    http_proxy_url: str | None = None

    # ---------- Binance ----------
    binance_api_key: SecretStr | None = None
    binance_api_secret: SecretStr | None = None
    # Override REST base URL — set to ``https://testnet.binancefuture.com`` to
    # use the public testnet (useful when ``fapi.binance.com`` is region-blocked).
    binance_rest_base_url: str = "https://fapi.binance.com"

    # ---------- Coinglass ----------
    coinglass_api_key: SecretStr | None = None

    # ---------- Telegram ----------
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    # ----- Computed helpers -----

    @property
    def timeframe_list(self) -> list[str]:
        """Parsed timeframes (CSV → list, whitespace stripped)."""
        return [tf.strip() for tf in self.timeframes.split(",") if tf.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance.

    The instance is created lazily on first access. Use :func:`reset_settings`
    in tests to force re-loading after mutating environment variables.
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Clear the cached :class:`Settings` (intended for tests)."""
    global _settings
    _settings = None
