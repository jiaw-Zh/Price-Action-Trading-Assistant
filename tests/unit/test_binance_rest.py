"""Tests for :mod:`pa_assistant.ingestion.binance`.

All HTTP traffic is intercepted via :class:`httpx.MockTransport` — no real
network calls are made.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from pa_assistant.ingestion.binance import (
    INTERVAL_MS,
    BinanceRestClient,
    interval_to_ms,
    klines_to_polars,
)

# Realistic Binance kline payload (one 1-minute bar @ 2020-01-01 00:00 UTC).
SAMPLE_KLINE: list[object] = [
    1577836800000,  # open_time (ms)
    "7195.24",  # open
    "7196.25",  # high
    "7178.66",  # low
    "7180.00",  # close
    "1234.5",  # volume (base)
    1577836859999,  # close_time (ms)
    "8876543.21",  # quote_volume
    1500,  # trade_count
    "600.0",  # taker_buy_base
    "4321098.76",  # taker_buy_quote
    "0",  # ignored
]


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    retry_attempts: int = 5,
) -> BinanceRestClient:
    """Build a BinanceRestClient backed by a MockTransport."""
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://fapi.binance.com",
        transport=transport,
    )
    return BinanceRestClient(
        client=http_client,
        retry_attempts=retry_attempts,
        retry_min_wait=0.0,
        retry_max_wait=0.01,
    )


# ---------------------------------------------------------------------------
# interval helpers
# ---------------------------------------------------------------------------


def test_interval_to_ms_supported() -> None:
    assert interval_to_ms("1m") == 60_000
    assert interval_to_ms("5m") == 300_000
    assert interval_to_ms("1h") == 3_600_000
    assert interval_to_ms("1d") == 86_400_000


def test_interval_to_ms_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        interval_to_ms("2m")


def test_interval_map_keys_match_architecture() -> None:
    # All timeframes named in the architecture doc must be representable.
    expected = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}
    assert expected.issubset(INTERVAL_MS.keys())


# ---------------------------------------------------------------------------
# get_klines
# ---------------------------------------------------------------------------


async def test_get_klines_returns_payload() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["symbol"] = request.url.params["symbol"]
        captured["interval"] = request.url.params["interval"]
        captured["limit"] = request.url.params["limit"]
        return httpx.Response(200, json=[SAMPLE_KLINE])

    async with _make_client(handler) as client:
        rows = await client.get_klines("btcusdt", "1m", limit=500)

    assert captured == {
        "path": "/fapi/v1/klines",
        "symbol": "BTCUSDT",
        "interval": "1m",
        "limit": "500",
    }
    assert rows == [SAMPLE_KLINE]


async def test_get_klines_limit_clamped() -> None:
    """A limit above 1500 should be clamped, not rejected."""
    seen_limit: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_limit["v"] = request.url.params["limit"]
        return httpx.Response(200, json=[])

    async with _make_client(handler) as client:
        await client.get_klines("BTCUSDT", "1m", limit=99_999)
    assert seen_limit["v"] == "1500"


async def test_get_klines_unknown_interval_raises() -> None:
    async with _make_client(lambda r: httpx.Response(200, json=[])) as client:
        with pytest.raises(ValueError, match="unsupported"):
            await client.get_klines("BTCUSDT", "2m")


# ---------------------------------------------------------------------------
# iter_klines pagination
# ---------------------------------------------------------------------------


async def test_iter_klines_paginates_until_exhausted() -> None:
    """The paginator should walk forward by ``interval_ms`` past the last bar."""
    pages = [
        # First page: 2 bars at 00:00 and 00:01
        [
            [1577836800000, "1", "1", "1", "1", "1", 1577836859999, "1", 1, "1", "1", "0"],
            [1577836860000, "1", "1", "1", "1", "1", 1577836919999, "1", 1, "1", "1", "0"],
        ],
        # Second page: empty -> stop
        [],
    ]
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        page = pages[call_count] if call_count < len(pages) else []
        call_count += 1
        return httpx.Response(200, json=page)

    collected: list[list[list[object]]] = []
    async with _make_client(handler) as client:
        async for page in client.iter_klines(
            "BTCUSDT",
            "1m",
            start_ms=1577836800000,
            end_ms=1577836800000 + 10 * 60_000,  # 10-minute window
        ):
            collected.append(page)

    assert len(collected) == 1
    assert len(collected[0]) == 2
    # Two HTTP calls: one with data, one empty (terminator)
    assert call_count == 2


async def test_iter_klines_empty_range_yields_nothing() -> None:
    async with _make_client(lambda r: httpx.Response(500)) as client:
        out: list[object] = []
        async for page in client.iter_klines("BTCUSDT", "1m", start_ms=2000, end_ms=1000):
            out.append(page)
        assert out == []


# ---------------------------------------------------------------------------
# get_open_interest
# ---------------------------------------------------------------------------


async def test_get_open_interest_returns_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fapi/v1/openInterest"
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(
            200,
            json={
                "symbol": "BTCUSDT",
                "openInterest": "123456.78",
                "time": 1577836800000,
            },
        )

    async with _make_client(handler) as client:
        payload = await client.get_open_interest("btcusdt")

    assert payload["openInterest"] == "123456.78"
    assert payload["symbol"] == "BTCUSDT"


# ---------------------------------------------------------------------------
# Retry behavior
# ---------------------------------------------------------------------------


async def test_retries_on_5xx_then_succeeds() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=[SAMPLE_KLINE])

    async with _make_client(handler) as client:
        rows = await client.get_klines("BTCUSDT", "1m")

    assert len(rows) == 1
    assert call_count == 3


async def test_retries_on_429_rate_limit() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429)
        return httpx.Response(200, json=[])

    async with _make_client(handler) as client:
        rows = await client.get_klines("BTCUSDT", "1m")

    assert rows == []
    assert call_count == 2


async def test_does_not_retry_on_4xx_client_error() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})

    async with _make_client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_klines("ZZZZZZ", "1m")
    assert call_count == 1  # no retries


async def test_retry_exhausted_propagates() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(503)

    async with _make_client(handler, retry_attempts=3) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_klines("BTCUSDT", "1m")
    assert call_count == 3


# ---------------------------------------------------------------------------
# Polars conversion
# ---------------------------------------------------------------------------


def test_klines_to_polars_schema_and_values() -> None:
    df = klines_to_polars([SAMPLE_KLINE], "btcusdt")

    assert df.shape == (1, 13)
    assert df["symbol"][0] == "BTCUSDT"
    assert df["open"][0] == 7195.24
    assert df["close"][0] == 7180.0
    assert df["volume"][0] == 1234.5
    assert df["trade_count"][0] == 1500
    assert df["taker_buy_base"][0] == 600.0
    assert df["is_closed"][0] is True
    # open_time should be a naive UTC datetime equivalent to 2020-01-01 00:00
    assert df["open_time"][0].isoformat() == "2020-01-01T00:00:00"


def test_klines_to_polars_empty_input() -> None:
    df = klines_to_polars([], "BTCUSDT")
    assert df.is_empty()
    assert df.columns == [
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
    ]


# ---------------------------------------------------------------------------
# from_settings
# ---------------------------------------------------------------------------


def test_from_settings_attaches_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from pa_assistant.config import Settings

    monkeypatch.setenv("BINANCE_API_KEY", "test-key-123")
    settings = Settings()
    client = BinanceRestClient.from_settings(settings)
    assert client.api_key == "test-key-123"


def test_from_settings_without_api_key() -> None:
    from pa_assistant.config import Settings

    settings = Settings()
    client = BinanceRestClient.from_settings(settings)
    assert client.api_key is None
