"""Binance USDT-M Futures REST client.

Endpoints used:

* ``GET /fapi/v1/klines``           — historical OHLCV (max 1500 per call)
* ``GET /fapi/v1/openInterest``     — current open interest snapshot
* ``GET /fapi/v1/openInterestHist`` — historical OI (≤ 30 days, period-bucketed)

Authentication is not required for these public endpoints, but if an API key
is configured we attach it via the ``X-MBX-APIKEY`` header — some IP-rate-limit
tiers prefer it.

Design choices:

* All times exchanged with the API are **milliseconds since epoch**. Datetimes
  in the public Python interface are timezone-aware UTC where applicable.
* Transient HTTP errors (5xx, 429, network) trigger exponential-backoff
  retries via :mod:`tenacity`. Retry parameters are constructor-injectable so
  unit tests can run without real waits.
* The client owns its :class:`httpx.AsyncClient` by default but accepts an
  external one (used by tests via ``MockTransport``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Final

import httpx
import polars as pl
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from pa_assistant.config import Settings
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


# Exception classes considered transient on REST calls.
_TRANSIENT_HTTPX_ERRORS: Final[tuple[type[BaseException], ...]] = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.PoolTimeout,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


def _is_transient(exc: BaseException) -> bool:
    """Predicate for tenacity: is *exc* worth retrying?"""
    if isinstance(exc, _TRANSIENT_HTTPX_ERRORS):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status >= 500 or status == 429
    return False


class BinanceRestClient:
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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._timeout = timeout
        self._retry_attempts = retry_attempts
        self._retry_min_wait = retry_min_wait
        self._retry_max_wait = retry_max_wait

        # External clients (e.g. test transports) are not owned/closed by us.
        self._client = client
        self._owned_client = client is None

    # ----- Construction helpers -----

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
        }
        kwargs.update(overrides)
        return cls(**kwargs)

    # ----- Lifecycle -----

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"User-Agent": "pa-assistant/0.1"}
            if self.api_key:
                headers["X-MBX-APIKEY"] = self.api_key
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self._timeout,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owned_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> BinanceRestClient:
        self._get_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # ----- Internal HTTP plumbing -----

    def _make_retrying(self) -> AsyncRetrying:
        return AsyncRetrying(
            stop=stop_after_attempt(self._retry_attempts),
            wait=wait_exponential(
                multiplier=1,
                min=self._retry_min_wait,
                max=self._retry_max_wait,
            ),
            retry=retry_if_exception(_is_transient),
            reraise=True,
        )

    async def _get(self, path: str, **params: Any) -> Any:
        """Issue a GET request with retry and JSON decoding."""
        async for attempt in self._make_retrying():
            with attempt:
                client = self._get_client()
                response = await client.get(path, params=params)
                response.raise_for_status()
                return response.json()
        # AsyncRetrying with reraise=True either returns above or raises.
        raise AssertionError("unreachable: retry loop exited without result")

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
        """Fetch one page of klines.

        Returns the raw 12-element rows as documented by Binance.
        """
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
        """Yield successive pages of klines covering ``[start_ms, end_ms)``.

        The cursor advances past the last returned bar by one ``interval``,
        guaranteeing forward progress and de-duplicating page boundaries.
        """
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


# ---------------------------------------------------------------------------
# Polars conversion helpers
# ---------------------------------------------------------------------------

# Kline column order matches the kline_1m table schema. Keep them in sync.
_KLINE_COLUMNS: Final[tuple[str, ...]] = (
    "open_time",
    "close_time",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trade_count",
    "taker_buy_base",
    "taker_buy_quote",
    "is_closed",
)


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
    to ``True``. WebSocket streams should override this for the live bar.
    """
    if not rows:
        return _empty_klines_df(symbol)

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


def _empty_klines_df(symbol: str) -> pl.DataFrame:
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
