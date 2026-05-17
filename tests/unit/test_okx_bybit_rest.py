"""Tests for OKX and Bybit REST clients (mocked transports)."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from pa_assistant.ingestion.bybit import BybitRestClient
from pa_assistant.ingestion.okx import OkxRestClient


def _okx(handler: Callable[[httpx.Request], httpx.Response]) -> OkxRestClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://www.okx.com", transport=transport)
    return OkxRestClient(
        client=http_client,
        retry_attempts=3,
        retry_min_wait=0.0,
        retry_max_wait=0.01,
    )


def _bybit(handler: Callable[[httpx.Request], httpx.Response]) -> BybitRestClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://api.bybit.com", transport=transport)
    return BybitRestClient(
        client=http_client,
        retry_attempts=3,
        retry_min_wait=0.0,
        retry_max_wait=0.01,
    )


# ---------------------------------------------------------------------------
# OKX
# ---------------------------------------------------------------------------


async def test_okx_funding_rate_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v5/public/funding-rate"
        assert request.url.params["instId"] == "BTC-USDT-SWAP"
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "",
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "fundingRate": "0.00012345",
                        "nextFundingTime": "1700000000000",
                    }
                ],
            },
        )

    async with _okx(handler) as c:
        rate = await c.get_funding_rate("BTC-USDT-SWAP")
    assert rate["fundingRate"] == "0.00012345"


async def test_okx_open_interest_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["instType"] == "SWAP"
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "",
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "instType": "SWAP",
                        "oi": "1234567",
                        "oiCcy": "12345.6",
                        "ts": "1700000000000",
                    }
                ],
            },
        )

    async with _okx(handler) as c:
        oi = await c.get_open_interest("BTC-USDT-SWAP")
    assert oi["oiCcy"] == "12345.6"


async def test_okx_envelope_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": "50001", "msg": "service unavailable", "data": []},
        )

    async with _okx(handler) as c:
        with pytest.raises(RuntimeError, match="OKX error 50001"):
            await c.get_funding_rate("BTC-USDT-SWAP")


async def test_okx_empty_data_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with _okx(handler) as c:
        with pytest.raises(RuntimeError, match="empty"):
            await c.get_funding_rate("BTC-USDT-SWAP")


# ---------------------------------------------------------------------------
# Bybit
# ---------------------------------------------------------------------------


async def test_bybit_funding_history_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/market/funding/history"
        assert request.url.params["category"] == "linear"
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "fundingRate": "0.0001",
                            "fundingRateTimestamp": "1700000000000",
                        }
                    ],
                },
            },
        )

    async with _bybit(handler) as c:
        rate = await c.get_funding_rate("BTCUSDT")
    assert rate["fundingRate"] == "0.0001"


async def test_bybit_open_interest_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/market/open-interest"
        assert request.url.params["intervalTime"] == "5min"
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "category": "linear",
                    "symbol": "BTCUSDT",
                    "list": [
                        {
                            "openInterest": "55555.5",
                            "timestamp": "1700000000000",
                        }
                    ],
                },
            },
        )

    async with _bybit(handler) as c:
        oi = await c.get_open_interest("BTCUSDT")
    assert oi["openInterest"] == "55555.5"


async def test_bybit_error_envelope_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"retCode": 10001, "retMsg": "param error", "result": {}},
        )

    async with _bybit(handler) as c:
        with pytest.raises(RuntimeError, match="Bybit error 10001"):
            await c.get_funding_rate("BTCUSDT")


async def test_bybit_retries_on_5xx() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {"list": [{"fundingRate": "0", "symbol": "BTCUSDT"}]},
            },
        )

    async with _bybit(handler) as c:
        await c.get_funding_rate("BTCUSDT")
    assert call_count == 3
