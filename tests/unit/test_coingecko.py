"""Tests for CoinGecko REST client and normalizer."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import httpx
import pytest

from pa_assistant.ingestion.coingecko import (
    CoinGeckoRestClient,
    parse_coingecko_binance_ticker,
)


def _coingecko(
    handler: Callable[[httpx.Request], httpx.Response],
    api_key: str | None = "fake-key",
) -> CoinGeckoRestClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://api.coingecko.com/api/v3/", transport=transport
    )
    return CoinGeckoRestClient(
        client=http_client,
        api_key=api_key,
        retry_attempts=3,
        retry_min_wait=0.0,
        retry_max_wait=0.01,
    )


async def test_coingecko_get_derivatives_tickers_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/derivatives"
        assert request.headers.get("x-cg-demo-api-key") == "fake-key"
        return httpx.Response(
            200,
            json=[
                {
                    "market": "Binance (Futures)",
                    "symbol": "BTCUSDT",
                    "price": "60000.0",
                    "funding_rate": "0.0001",
                    "open_interest": "600000000.0",
                    "last_traded_at": 1700000000,
                },
                {
                    "market": "Bybit",
                    "symbol": "ETHUSDT",
                    "price": "3000.0",
                    "funding_rate": "0.0002",
                    "open_interest": "3000000.0",
                    "last_traded_at": 1700000000,
                },
            ],
        )

    async with _coingecko(handler) as c:
        tickers = await c.get_derivatives_tickers()
    assert len(tickers) == 2
    assert tickers[0]["market"] == "Binance (Futures)"


async def test_coingecko_get_binance_futures_ticker_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "market": "Binance (Futures)",
                    "symbol": "BTCUSDT",
                    "price": "60000.0",
                    "funding_rate": "0.0001",
                    "open_interest": "600000000.0",
                    "last_traded_at": 1700000000,
                }
            ],
        )

    async with _coingecko(handler) as c:
        ticker = await c.get_binance_futures_ticker("BTCUSDT")
    assert ticker is not None
    assert ticker["symbol"] == "BTCUSDT"
    assert ticker["market"] == "Binance (Futures)"


async def test_coingecko_get_binance_futures_ticker_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "market": "Bybit",
                    "symbol": "BTCUSDT",
                    "price": "60000.0",
                }
            ],
        )

    async with _coingecko(handler) as c:
        ticker = await c.get_binance_futures_ticker("BTCUSDT")
    assert ticker is None


def test_parse_coingecko_binance_ticker_ok() -> None:
    raw_ticker = {
        "market": "Binance (Futures)",
        "symbol": "BTCUSDT",
        "price": "50000.0",
        "funding_rate": "0.00015",
        "open_interest": "50000000.0",  # 50M USD
        "last_traded_at": 1700000000,
    }

    parsed = parse_coingecko_binance_ticker(raw_ticker)
    assert parsed["funding_rate"] == 0.00015
    # 50,000,000 USD / 50,000 Price = 1000 BTC open interest base
    assert parsed["open_interest_base"] == 1000.0
    assert isinstance(parsed["timestamp"], datetime)
    assert parsed["timestamp"].year == 2023  # 1700000000 is Nov 14, 2023
