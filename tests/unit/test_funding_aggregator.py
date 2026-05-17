"""Tests for the funding-rate provider abstraction.

The :class:`SelfAggregatedFundingProvider` is exercised by injecting fake
exchange clients (subclasses of the real client classes that bypass HTTP).
This keeps the test focused on the aggregation math and partial-failure
handling rather than HTTP details (those are covered in test_*_rest.py).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pa_assistant.config import Settings
from pa_assistant.ingestion.binance import BinanceRestClient
from pa_assistant.ingestion.bybit import BybitRestClient
from pa_assistant.ingestion.funding import (
    CoinglassFundingProvider,
    SelfAggregatedFundingProvider,
    make_funding_provider,
)
from pa_assistant.ingestion.okx import OkxRestClient

# ---------------------------------------------------------------------------
# Helpers — fake clients that return canned snapshots
# ---------------------------------------------------------------------------


class _FakeBinance(BinanceRestClient):
    def __init__(self, funding: dict[str, Any] | None, oi: dict[str, Any] | None) -> None:
        # Bypass the real super().__init__ — we never make HTTP calls.
        self._funding = funding
        self._oi = oi

    async def get_funding_rate(self, symbol: str) -> dict[str, Any]:
        if self._funding is None:
            raise RuntimeError("binance funding fetch failed (simulated)")
        return self._funding

    async def get_open_interest(self, symbol: str) -> dict[str, Any]:
        if self._oi is None:
            raise RuntimeError("binance OI fetch failed (simulated)")
        return self._oi

    async def aclose(self) -> None:
        return None


class _FakeOkx(OkxRestClient):
    def __init__(self, funding: dict[str, Any] | None, oi: dict[str, Any] | None) -> None:
        self._funding = funding
        self._oi = oi

    async def get_funding_rate(self, inst_id: str) -> dict[str, Any]:
        if self._funding is None:
            raise RuntimeError("okx funding fetch failed (simulated)")
        return self._funding

    async def get_open_interest(self, inst_id: str) -> dict[str, Any]:
        if self._oi is None:
            raise RuntimeError("okx OI fetch failed (simulated)")
        return self._oi

    async def aclose(self) -> None:
        return None


class _FakeBybit(BybitRestClient):
    def __init__(self, funding: dict[str, Any] | None, oi: dict[str, Any] | None) -> None:
        self._funding = funding
        self._oi = oi

    async def get_funding_rate(self, symbol: str) -> dict[str, Any]:
        if self._funding is None:
            raise RuntimeError("bybit funding fetch failed (simulated)")
        return self._funding

    async def get_open_interest(
        self, symbol: str, *, interval_time: str = "5min"
    ) -> dict[str, Any]:
        if self._oi is None:
            raise RuntimeError("bybit OI fetch failed (simulated)")
        return self._oi

    async def aclose(self) -> None:
        return None


# Canonical sample payloads (per-exchange shapes) used across tests.
BINANCE_FUNDING = {"symbol": "BTCUSDT", "lastFundingRate": "0.0001"}
BINANCE_OI = {"symbol": "BTCUSDT", "openInterest": "100000", "time": 1700000000000}

OKX_FUNDING = {"instId": "BTC-USDT-SWAP", "fundingRate": "0.0002"}
OKX_OI = {"instId": "BTC-USDT-SWAP", "oiCcy": "50000", "ts": "1700000000000"}

BYBIT_FUNDING = {"symbol": "BTCUSDT", "fundingRate": "-0.0001"}
BYBIT_OI = {"symbol": "BTCUSDT", "openInterest": "30000", "timestamp": "1700000000000"}


# ---------------------------------------------------------------------------
# Aggregation math
# ---------------------------------------------------------------------------


async def test_aggregator_all_three_succeed() -> None:
    """Verify the OI-weighted average when every exchange returns data."""
    provider = SelfAggregatedFundingProvider(
        binance=_FakeBinance(BINANCE_FUNDING, BINANCE_OI),
        okx=_FakeOkx(OKX_FUNDING, OKX_OI),
        bybit=_FakeBybit(BYBIT_FUNDING, BYBIT_OI),
    )

    result = await provider.get_weighted_funding("BTCUSDT")

    # Manual math:
    # rates: binance=+0.0001 (OI 100000), okx=+0.0002 (OI 50000), bybit=-0.0001 (OI 30000)
    # numerator   = 0.0001*100000 + 0.0002*50000 + (-0.0001)*30000 = 10 + 10 - 3 = 17
    # denominator = 100000 + 50000 + 30000 = 180000
    # weighted    = 17 / 180000 ≈ 9.444e-5
    expected = 17.0 / 180000.0
    assert result.weighted_rate == pytest.approx(expected, rel=1e-9)
    assert result.sample_count == 3
    assert result.symbol == "BTCUSDT"
    assert result.source == "self_aggregated"
    assert {c.exchange for c in result.components} == {"binance", "okx", "bybit"}

    await provider.aclose()


async def test_aggregator_one_exchange_fails() -> None:
    """If OKX fails, weighted average comes from Binance + Bybit only."""
    provider = SelfAggregatedFundingProvider(
        binance=_FakeBinance(BINANCE_FUNDING, BINANCE_OI),
        okx=_FakeOkx(None, None),  # all OKX calls fail
        bybit=_FakeBybit(BYBIT_FUNDING, BYBIT_OI),
    )

    result = await provider.get_weighted_funding("BTCUSDT")

    # rates: binance=+0.0001 (OI 100000), bybit=-0.0001 (OI 30000)
    # numerator   = 10 - 3 = 7
    # denominator = 130000
    expected = 7.0 / 130000.0
    assert result.weighted_rate == pytest.approx(expected, rel=1e-9)
    assert result.sample_count == 2
    assert {c.exchange for c in result.components} == {"binance", "bybit"}


async def test_aggregator_two_exchanges_fail() -> None:
    """Falls back to the single survivor; weighted = its own rate."""
    provider = SelfAggregatedFundingProvider(
        binance=_FakeBinance(BINANCE_FUNDING, BINANCE_OI),
        okx=_FakeOkx(None, None),
        bybit=_FakeBybit(None, None),
    )

    result = await provider.get_weighted_funding("BTCUSDT")
    assert result.sample_count == 1
    assert result.weighted_rate == pytest.approx(0.0001)


async def test_aggregator_all_fail_raises() -> None:
    provider = SelfAggregatedFundingProvider(
        binance=_FakeBinance(None, None),
        okx=_FakeOkx(None, None),
        bybit=_FakeBybit(None, None),
    )

    with pytest.raises(RuntimeError, match="all exchanges returned errors"):
        await provider.get_weighted_funding("BTCUSDT")


async def test_aggregator_unknown_symbol_raises() -> None:
    provider = SelfAggregatedFundingProvider(
        binance=_FakeBinance(BINANCE_FUNDING, BINANCE_OI),
        okx=_FakeOkx(OKX_FUNDING, OKX_OI),
        bybit=_FakeBybit(BYBIT_FUNDING, BYBIT_OI),
    )

    with pytest.raises(ValueError, match="no per-exchange mapping"):
        await provider.get_weighted_funding("DOGEUSDT")


async def test_aggregator_zero_total_oi_raises() -> None:
    provider = SelfAggregatedFundingProvider(
        binance=_FakeBinance(BINANCE_FUNDING, {**BINANCE_OI, "openInterest": "0"}),
        okx=_FakeOkx(OKX_FUNDING, {**OKX_OI, "oiCcy": "0"}),
        bybit=_FakeBybit(BYBIT_FUNDING, {**BYBIT_OI, "openInterest": "0"}),
    )

    with pytest.raises(RuntimeError, match="total OI is non-positive"):
        await provider.get_weighted_funding("BTCUSDT")


# ---------------------------------------------------------------------------
# Coinglass stub
# ---------------------------------------------------------------------------


async def test_coinglass_stub_raises_not_implemented() -> None:
    provider = CoinglassFundingProvider(api_key="fake")
    with pytest.raises(NotImplementedError):
        await provider.get_weighted_funding("BTCUSDT")
    await provider.aclose()  # must not raise


def test_coinglass_empty_key_rejected() -> None:
    with pytest.raises(ValueError, match="API key is required"):
        CoinglassFundingProvider(api_key="")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_make_funding_provider_defaults_to_self_aggregated() -> None:
    settings = Settings()
    assert settings.coinglass_api_key is None
    provider = make_funding_provider(settings)
    assert isinstance(provider, SelfAggregatedFundingProvider)


def test_make_funding_provider_picks_coinglass_when_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COINGLASS_API_KEY", "real-key")
    settings = Settings()
    provider = make_funding_provider(settings)
    assert isinstance(provider, CoinglassFundingProvider)


def test_make_funding_provider_treats_blank_coinglass_key_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COINGLASS_API_KEY", "   ")
    settings = Settings()
    provider = make_funding_provider(settings)
    assert isinstance(provider, SelfAggregatedFundingProvider)


# ---------------------------------------------------------------------------
# Component snapshot fields are populated correctly
# ---------------------------------------------------------------------------


async def test_components_carry_correct_per_exchange_data() -> None:
    provider = SelfAggregatedFundingProvider(
        binance=_FakeBinance(BINANCE_FUNDING, BINANCE_OI),
        okx=_FakeOkx(OKX_FUNDING, OKX_OI),
        bybit=_FakeBybit(BYBIT_FUNDING, BYBIT_OI),
    )

    result = await provider.get_weighted_funding("BTCUSDT")
    by_exchange = {c.exchange: c for c in result.components}

    assert by_exchange["binance"].funding_rate == pytest.approx(0.0001)
    assert by_exchange["binance"].open_interest_base == pytest.approx(100_000.0)
    assert by_exchange["okx"].funding_rate == pytest.approx(0.0002)
    assert by_exchange["okx"].open_interest_base == pytest.approx(50_000.0)
    assert by_exchange["bybit"].funding_rate == pytest.approx(-0.0001)
    assert by_exchange["bybit"].open_interest_base == pytest.approx(30_000.0)


# ---------------------------------------------------------------------------
# Sanity: ensure the real Binance client signature still accepts mocks
# (regression guard if anyone refactors the constructor again)
# ---------------------------------------------------------------------------


def test_real_binance_client_accepts_mock_transport() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    http_client = httpx.AsyncClient(base_url="https://example.com", transport=transport)
    client = BinanceRestClient(client=http_client)
    assert client.api_key is None
