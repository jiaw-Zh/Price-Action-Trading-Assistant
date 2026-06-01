"""OKX V5 public REST client (perpetual swap).

Endpoints used:

* ``GET /api/v5/public/funding-rate``  — current funding rate
* ``GET /api/v5/public/open-interest`` — current open interest
* ``GET /api/v5/market/candles``       — historical OHLCV klines

OKX wraps every successful response in ``{"code": "0", "msg": "", "data": [...]}``.
We unwrap that here and surface the inner data list / object directly.

Symbol format: OKX uses ``BTC-USDT-SWAP`` for perpetual swaps. Mapping to/from
the canonical ``BTCUSDT`` form lives in :mod:`pa_assistant.ingestion.funding`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Final

import polars as pl

from pa_assistant.ingestion._http import AsyncRestClient
from pa_assistant.logging import get_logger

log = get_logger(__name__)

OKX_REST_BASE: Final[str] = "https://www.okx.com"

# OKX-supported bar values → milliseconds per bar.
OKX_INTERVAL_MS: Final[dict[str, int]] = {
    "1m": 60_000,
    "3m": 3 * 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1H": 60 * 60_000,
    "2H": 2 * 60 * 60_000,
    "4H": 4 * 60 * 60_000,
    "6H": 6 * 60 * 60_000,
    "12H": 12 * 60 * 60_000,
    "1D": 24 * 60 * 60_000,
    "1W": 7 * 24 * 60 * 60_000,
}

# Canonical interval → OKX bar value mapping.
_INTERVAL_TO_OKX: Final[dict[str, str]] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "4h": "4H",
    "1d": "1D",
}

# OKX max candles per request.
OKX_KLINES_PAGE_LIMIT: Final[int] = 300


def _canonical_to_okx_bar(interval: str) -> str:
    """Map a canonical interval (e.g. ``'1h'``) to OKX bar value (e.g. ``'1H'``)."""
    bar = _INTERVAL_TO_OKX.get(interval)
    if bar is None:
        raise ValueError(f"unsupported OKX kline interval: {interval!r}")
    return bar


def _okx_bar_to_ms(bar: str) -> int:
    """Return the duration of one bar in milliseconds."""
    ms = OKX_INTERVAL_MS.get(bar)
    if ms is None:
        raise ValueError(f"unsupported OKX bar value: {bar!r}")
    return ms


def _ms_to_naive_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).replace(tzinfo=None)


class OkxRestClient(AsyncRestClient):
    """Async client for OKX V5 public market endpoints."""

    def __init__(self, *, base_url: str = OKX_REST_BASE, **kwargs: Any) -> None:
        super().__init__(base_url=base_url, **kwargs)

    async def _get_unwrapped(self, path: str, **params: Any) -> list[dict[str, Any]]:
        """Issue a GET, validate the OKX envelope, return the ``data`` list."""
        result = await self._get(path, **params)
        if not isinstance(result, dict):
            raise RuntimeError(f"OKX: unexpected payload shape: {result!r}")
        code = str(result.get("code", ""))
        if code != "0":
            raise RuntimeError(f"OKX error {code}: {result.get('msg', '<no msg>')}")
        data = result.get("data")
        if not isinstance(data, list):
            raise RuntimeError(f"OKX: missing 'data' list in response: {result!r}")
        return data

    async def get_funding_rate(self, inst_id: str) -> dict[str, Any]:
        """Current funding rate for ``instId`` (e.g. ``'BTC-USDT-SWAP'``)."""
        data = await self._get_unwrapped("/api/v5/public/funding-rate", instId=inst_id)
        if not data:
            raise RuntimeError(f"OKX: empty funding-rate response for {inst_id!r}")
        return data[0]

    async def get_open_interest(self, inst_id: str) -> dict[str, Any]:
        """Current open interest for ``instId``."""
        data = await self._get_unwrapped(
            "/api/v5/public/open-interest", instType="SWAP", instId=inst_id
        )
        if not data:
            raise RuntimeError(f"OKX: empty open-interest response for {inst_id!r}")
        return data[0]

    # ----- Public API: klines -----

    async def get_klines(
        self,
        inst_id: str,
        bar: str = "1m",
        *,
        after: str | None = None,
        before: str | None = None,
        limit: int = OKX_KLINES_PAGE_LIMIT,
    ) -> list[list[str]]:
        """Fetch one page of klines (raw OKX candle rows).

        OKX candle format (9 fields):
        ``[ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]``

        Data is returned in **descending** order (newest first).
        Use ``after`` / ``before`` for pagination (pass the ``ts`` of the
        last received candle).
        """
        params: dict[str, Any] = {
            "instId": inst_id,
            "bar": bar,
            "limit": str(min(max(limit, 1), OKX_KLINES_PAGE_LIMIT)),
        }
        if after is not None:
            params["after"] = after
        if before is not None:
            params["before"] = before

        data = await self._get_unwrapped("/api/v5/market/candles", **params)
        # Each item is a list of strings: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
        return [list(item) if isinstance(item, list) else [] for item in data]

    async def iter_klines(
        self,
        inst_id: str,
        interval: str,
        *,
        start_ms: int,
        end_ms: int,
    ) -> AsyncIterator[list[list[str]]]:
        """Yield successive pages of klines covering ``[start_ms, end_ms)``.

        OKX returns data newest-first and paginates via ``after`` (return
        records older than this timestamp). We page backwards until we reach
        ``start_ms``.
        """
        if start_ms >= end_ms:
            return

        bar = _canonical_to_okx_bar(interval)
        after: str | None = None

        while True:
            page = await self.get_klines(inst_id, bar, after=after, limit=OKX_KLINES_PAGE_LIMIT)
            if not page:
                break

            # OKX returns newest first; reverse to chronological order
            page.reverse()

            for candle in page:
                if len(candle) < 9:
                    continue
                ts = int(candle[0])
                if ts < start_ms:
                    return
                if ts < end_ms:
                    yield [candle]

            # Move pagination cursor: oldest candle in this page - 1ms
            oldest_ts = int(page[0][0])
            next_after = str(oldest_ts - 1)
            if next_after == after:
                break  # safety: avoid infinite loop
            after = next_after

            # If oldest candle is already before start_ms, we're done
            if oldest_ts <= start_ms:
                break


def okx_symbol(symbol: str) -> str:
    """Convert canonical ``BTCUSDT`` to OKX ``BTC-USDT-SWAP``."""
    sym = symbol.upper()
    if sym.endswith("USDT"):
        base = sym[:-4]
        return f"{base}-USDT-SWAP"
    return sym


def okx_klines_to_polars(rows: list[list[str]], symbol: str) -> pl.DataFrame:
    """Convert OKX raw klines to Polars DataFrame matching canonical kline_1m.

    OKX candle format (9 fields):
    ``[ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]``

    * ``vol`` is in contracts (1 contract = 1 BTC for BTC-USDT-SWAP)
    * ``volCcy`` is in base currency (BTC)
    * ``volCcyQuote`` is in quote currency (USDT)
    * ``confirm`` is ``"0"`` (uncomplete) or ``"1"`` (complete)
    """
    if not rows:
        return _empty_klines_df()

    sym = symbol.upper()

    # Filter out malformed rows
    valid = [r for r in rows if len(r) >= 9]
    if not valid:
        return _empty_klines_df()

    open_times = [_ms_to_naive_utc(int(r[0])) for r in valid]
    # OKX doesn't provide close_time directly; infer as open_time + bar_duration - 1ms
    # For simplicity, use open_time + 59999ms for 1m (matches bybit convention)
    close_times = [_ms_to_naive_utc(int(r[0]) + 59999) for r in valid]

    return pl.DataFrame(
        {
            "open_time": open_times,
            "close_time": close_times,
            "symbol": [sym] * len(valid),
            "open": [float(r[1]) for r in valid],
            "high": [float(r[2]) for r in valid],
            "low": [float(r[3]) for r in valid],
            "close": [float(r[4]) for r in valid],
            "volume": [float(r[5]) for r in valid],  # vol in contracts
            "quote_volume": [float(r[7]) for r in valid],  # volCcyQuote in USDT
            "trade_count": [0] * len(valid),  # OKX doesn't provide trade count
            "taker_buy_base": [float(r[6]) / 2.0 for r in valid],  # volCcy/2 as neutral default
            "taker_buy_quote": [float(r[7]) / 2.0 for r in valid],  # volCcyQuote/2
            "is_closed": [r[8] == "1" for r in valid],  # confirm field
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
