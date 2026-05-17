"""Binance USDT-M Futures REST client.

Endpoints used:

* ``GET /fapi/v1/klines``           — historical OHLCV (max 1500 per call)
* ``GET /fapi/v1/openInterest``     — current open interest snapshot
* ``GET /fapi/v1/openInterestHist`` — historical OI (≤ 30 days, period-bucketed)
* ``GET /fapi/v1/premiumIndex``     — mark price + last funding rate

Authentication is not required for these public endpoints, but if an API key
is configured we attach it via the ``X-MBX-APIKEY`` header.

Design choices:

* All times exchanged with the API are **milliseconds since epoch**. Datetimes
  in the public Python interface are naive UTC by convention.
* HTTP retries / lifecycle are inherited from
  :class:`pa_assistant.ingestion._http.AsyncRestClient`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Final

import httpx
import polars as pl

from pa_assistant.config import Settings
from pa_assistant.ingestion._http import AsyncRestClient
from pa_assistant.logging import get_logger

log = get_logger(__name__)

BINANCE_FUTURES_BASE: Final[str] = "https://fapi.binance.com"

# Maximum kline rows the REST endpoint will return per call.
KLINES_PAGE_LIMIT: Final[int] = 1500

# Binance-supported intervals we care about → milliseconds per bar.
INTERVAL_MS: Final[dict[str, int]] = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


def interval_to_ms(interval: str) -> int:
    """Return the duration of one bar at ``interval`` in milliseconds."""
    try:
        return INTERVAL_MS[interval]
    except KeyError as exc:
        raise ValueError(f"unsupported kline interval: {interval!r}") from exc


class BinanceRestClient(AsyncRestClient):
    """Async client for Binance USDT-M Futures public REST endpoints."""

    def __init__(
        self,
        *,
        base_url: str = BINANCE_FUTURES_BASE,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
        retry_attempts: int = 5,
        retry_min_wait: float = 1.0,
        retry_max_wait: float = 30.0,
        proxy: str | None = None,
    ) -> None:
        headers: dict[str, str] = {}
        if api_key:
            headers["X-MBX-APIKEY"] = api_key
        super().__init__(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            retry_attempts=retry_attempts,
            retry_min_wait=retry_min_wait,
            retry_max_wait=retry_max_wait,
            client=client,
            proxy=proxy,
        )
        self.api_key = api_key

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        **overrides: Any,
    ) -> BinanceRestClient:
        """Build a client using API keys from :class:`Settings`."""
        api_key = (
            settings.binance_api_key.get_secret_value()
            if settings.binance_api_key is not None
            else None
        )
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": settings.binance_rest_base_url,
            "proxy": settings.http_proxy_url,
        }
        kwargs.update(overrides)
        return cls(**kwargs)

    # ----- Public API: klines -----

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = KLINES_PAGE_LIMIT,
    ) -> list[list[Any]]:
        """Fetch one page of klines (raw 12-element rows from Binance)."""
        if interval not in INTERVAL_MS:
            raise ValueError(f"unsupported kline interval: {interval!r}")

        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(max(limit, 1), KLINES_PAGE_LIMIT),
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms

        result = await self._get("/fapi/v1/klines", **params)
        if not isinstance(result, list):
            raise RuntimeError(f"unexpected klines payload: {result!r}")
        return result

    async def iter_klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_ms: int,
        end_ms: int,
        page_limit: int = KLINES_PAGE_LIMIT,
    ) -> AsyncIterator[list[list[Any]]]:
        """Yield successive pages of klines covering ``[start_ms, end_ms)``."""
        if start_ms >= end_ms:
            return
        bar_ms = interval_to_ms(interval)
        cursor = start_ms
        while cursor < end_ms:
            page = await self.get_klines(
                symbol,
                interval,
                start_ms=cursor,
                end_ms=end_ms,
                limit=page_limit,
            )
            if not page:
                return
            yield page

            last_open_ms = int(page[-1][0])
            next_cursor = last_open_ms + bar_ms
            if next_cursor <= cursor:  # safety: avoid infinite loop
                return
            cursor = next_cursor

    # ----- Public API: open interest -----

    async def get_open_interest(self, symbol: str) -> dict[str, Any]:
        """Current open-interest snapshot for *symbol*."""
        result = await self._get("/fapi/v1/openInterest", symbol=symbol.upper())
        if not isinstance(result, dict):
            raise RuntimeError(f"unexpected openInterest payload: {result!r}")
        return result

    async def get_open_interest_hist(
        self,
        symbol: str,
        period: str = "5m",
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Historical OI series (max 30 days, ``period``-bucketed)."""
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "period": period,
            "limit": min(max(limit, 1), 500),
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        result = await self._get("/futures/data/openInterestHist", **params)
        if not isinstance(result, list):
            raise RuntimeError(f"unexpected openInterestHist payload: {result!r}")
        return result

    # ----- Public API: funding rate -----

    async def get_funding_rate(self, symbol: str) -> dict[str, Any]:
        """Current premium index — includes ``lastFundingRate``."""
        result = await self._get("/fapi/v1/premiumIndex", symbol=symbol.upper())
        if not isinstance(result, dict):
            raise RuntimeError(f"unexpected premiumIndex payload: {result!r}")
        return result


# ---------------------------------------------------------------------------
# Polars conversion helpers (unchanged from previous revision).
# ---------------------------------------------------------------------------


def _ms_to_naive_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).replace(tzinfo=None)


def klines_to_polars(rows: list[list[Any]], symbol: str) -> pl.DataFrame:
    """Convert Binance kline rows to a Polars DataFrame matching ``kline_1m``.

    Binance kline format (12 fields):

    .. code-block:: text

        [open_time_ms, open, high, low, close, volume,
         close_time_ms, quote_volume, trade_count,
         taker_buy_base, taker_buy_quote, ignored]

    Historical klines from REST are always finalized, so ``is_closed`` is set
    to ``True``.
    """
    if not rows:
        return _empty_klines_df()

    sym = symbol.upper()
    return pl.DataFrame(
        {
            "open_time": [_ms_to_naive_utc(int(r[0])) for r in rows],
            "close_time": [_ms_to_naive_utc(int(r[6])) for r in rows],
            "symbol": [sym] * len(rows),
            "open": [float(r[1]) for r in rows],
            "high": [float(r[2]) for r in rows],
            "low": [float(r[3]) for r in rows],
            "close": [float(r[4]) for r in rows],
            "volume": [float(r[5]) for r in rows],
            "quote_volume": [float(r[7]) for r in rows],
            "trade_count": [int(r[8]) for r in rows],
            "taker_buy_base": [float(r[9]) for r in rows],
            "taker_buy_quote": [float(r[10]) for r in rows],
            "is_closed": [True] * len(rows),
        },
        schema={
            "open_time": pl.Datetime("us"),
            "close_time": pl.Datetime("us"),
            "symbol": pl.Utf8,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "quote_volume": pl.Float64,
            "trade_count": pl.Int64,
            "taker_buy_base": pl.Float64,
            "taker_buy_quote": pl.Float64,
            "is_closed": pl.Boolean,
        },
    )


def _empty_klines_df() -> pl.DataFrame:
    """Empty DataFrame with the canonical kline schema."""
    return pl.DataFrame(
        schema={
            "open_time": pl.Datetime("us"),
            "close_time": pl.Datetime("us"),
            "symbol": pl.Utf8,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "quote_volume": pl.Float64,
            "trade_count": pl.Int64,
            "taker_buy_base": pl.Float64,
            "taker_buy_quote": pl.Float64,
            "is_closed": pl.Boolean,
        }
    )
