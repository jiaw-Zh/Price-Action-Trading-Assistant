"""OI-weighted funding rate — provider abstraction + implementations.

The :class:`FundingProvider` Protocol decouples *how* the rate is sourced from
*where* it is consumed. Two implementations ship here:

* :class:`SelfAggregatedFundingProvider` — pulls funding rate + OI from
  Binance / OKX / Bybit and computes the OI-weighted average ourselves.
  Uses only public endpoints — no API keys, no payment.
* :class:`CoinglassFundingProvider` — *stub*. Will eventually wrap the
  Coinglass paid REST API; until then it raises :class:`NotImplementedError`.

Math
----

Given per-exchange snapshots ``(rate_i, oi_i)`` in the same base asset:

.. code-block:: text

    weighted = sum(rate_i * oi_i) / sum(oi_i)

If a sub-set of exchanges fails the request, we still compute a weighted
average over the responders (logging the failures). If *all* fail we raise,
so the caller can decide how to proceed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Protocol

from pa_assistant.config import Settings
from pa_assistant.ingestion.binance import BinanceRestClient
from pa_assistant.ingestion.bitget import BitgetRestClient
from pa_assistant.ingestion.bybit import BybitRestClient
from pa_assistant.ingestion.gateio import GateioRestClient
from pa_assistant.ingestion.okx import OkxRestClient
from pa_assistant.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExchangeFundingSnapshot:
    """One exchange's contribution to a weighted funding-rate calculation."""

    exchange: str
    funding_rate: float  # decimal, e.g. -0.00012 = -0.012 %
    open_interest_base: float  # in base asset (e.g. BTC)
    snapshot_time: datetime  # naive UTC


@dataclass(frozen=True, slots=True)
class WeightedFundingRate:
    """The output of a funding provider."""

    symbol: str
    timestamp: datetime  # naive UTC, "as of" the aggregation
    weighted_rate: float
    source: str  # 'self_aggregated' | 'coinglass'
    sample_count: int  # exchanges actually aggregated
    components: tuple[ExchangeFundingSnapshot, ...]


# ---------------------------------------------------------------------------
# Provider Protocol
# ---------------------------------------------------------------------------


class FundingProvider(Protocol):
    """Anything capable of returning an OI-weighted funding rate."""

    name: str

    async def get_weighted_funding(self, symbol: str) -> WeightedFundingRate: ...

    async def aclose(self) -> None: ...


def _ms_to_naive_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------

# Symbol mapping (canonical → per-exchange instrument id)
# ---------------------------------------------------------------------------

# Canonical symbol → (binance, okx, bybit) instrument identifiers.
SYMBOL_MAP: Final[dict[str, dict[str, str]]] = {
    "BTCUSDT": {
        "binance": "BTCUSDT",
        "okx": "BTC-USDT-SWAP",
        "bybit": "BTCUSDT",
        "bitget": "BTCUSDT",
        "gateio": "BTC_USDT",
    },
    "ETHUSDT": {
        "binance": "ETHUSDT",
        "okx": "ETH-USDT-SWAP",
        "bybit": "ETHUSDT",
        "bitget": "ETHUSDT",
        "gateio": "ETH_USDT",
    },
}


def _resolve_symbols(symbol: str) -> dict[str, str]:
    sym = symbol.upper()
    mapping = SYMBOL_MAP.get(sym)
    if mapping is None:
        raise ValueError(
            f"no per-exchange mapping for symbol {sym!r}; "
            f"add it to SYMBOL_MAP in pa_assistant.ingestion.funding"
        )
    return mapping



# ---------------------------------------------------------------------------
# Self-aggregated implementation
# ---------------------------------------------------------------------------


class SelfAggregatedFundingProvider:
    """Computes OI-weighted funding rate from Binance + OKX + Bybit + Bitget + Gate.io.

    The exchange clients are injected so tests can swap in mocks.
    Closing the provider closes all owned underlying clients.
    """

    name = "self_aggregated"

    def __init__(
        self,
        *,
        binance: BinanceRestClient,
        okx: OkxRestClient,
        bybit: BybitRestClient,
        bitget: BitgetRestClient,
        gateio: GateioRestClient,
        duckdb_path: Path | None = None,
    ) -> None:
        self.binance = binance
        self.okx = okx
        self.bybit = bybit
        self.bitget = bitget
        self.gateio = gateio
        self.duckdb_path = duckdb_path

    @classmethod
    def from_settings(cls, settings: Settings) -> SelfAggregatedFundingProvider:
        proxy = settings.http_proxy_url
        return cls(
            binance=BinanceRestClient.from_settings(settings),
            okx=OkxRestClient(proxy=proxy),
            bybit=BybitRestClient(proxy=proxy),
            bitget=BitgetRestClient(proxy=proxy),
            gateio=GateioRestClient(proxy=proxy),
            duckdb_path=settings.duckdb_path,
        )

    async def aclose(self) -> None:
        await asyncio.gather(
            self.binance.aclose(),
            self.okx.aclose(),
            self.bybit.aclose(),
            self.bitget.aclose(),
            self.gateio.aclose(),
            return_exceptions=True,
        )

    # ----- Per-exchange snapshot fetchers -----

    async def _fetch_binance(self, sym: str) -> ExchangeFundingSnapshot:
        # Fetch funding rate (premiumIndex)
        try:
            funding = await self.binance.get_funding_rate(sym)
            funding_rate = float(funding["lastFundingRate"])
        except Exception as e:
            # If funding rate fails, raise to try fallback or drop
            raise e

        # Local-First approach for Binance OI: Try DB first to completely avoid duplicate network requests
        oi_val = None
        snapshot_time = None
        if self.duckdb_path:
            try:
                import duckdb
                conn = duckdb.connect(str(self.duckdb_path), read_only=True)
                try:
                    row = conn.execute(
                        "SELECT open_interest, timestamp FROM oi_1m WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
                        [sym.upper()],
                    ).fetchone()
                    if row:
                        oi_val = float(row[0])
                        snapshot_time = row[1]
                        log.info("binance_oi_funding_db_first_success", oi=oi_val, time=snapshot_time)
                finally:
                    conn.close()
            except Exception as db_err:
                log.error("binance_oi_funding_db_first_failed", error=str(db_err))

        # Only call the network if it's not found in the DB (e.g., in some test environments)
        if oi_val is None:
            log.warning("binance_oi_not_found_in_db_calling_network_fallback")
            try:
                oi = await self.binance.get_open_interest(sym)
                oi_val = float(oi["openInterest"])
                snapshot_time = _ms_to_naive_utc(int(oi["time"]))
            except Exception as oi_err:
                log.warning("binance_network_oi_failed_trying_bybit_oi_fallback", error=str(oi_err))
                try:
                    bybit_oi = await self.bybit.get_open_interest(sym)
                    oi_val = float(bybit_oi["openInterest"])
                    snapshot_time = _ms_to_naive_utc(int(bybit_oi["timestamp"]))
                    log.info("binance_oi_funding_bybit_oi_fallback_success", oi=oi_val)
                except Exception as bybit_err:
                    log.error("binance_oi_funding_bybit_oi_fallback_failed", error=str(bybit_err))
                    raise oi_err  # Re-raise original error if all fallbacks fail

        if snapshot_time is None:
            snapshot_time = datetime.now(UTC).replace(tzinfo=None)

        return ExchangeFundingSnapshot(
            exchange="binance",
            funding_rate=funding_rate,
            open_interest_base=oi_val,
            snapshot_time=snapshot_time,
        )


    async def _fetch_okx(self, inst_id: str) -> ExchangeFundingSnapshot:
        funding, oi = await asyncio.gather(
            self.okx.get_funding_rate(inst_id),
            self.okx.get_open_interest(inst_id),
        )
        # OKX OI: ``oi`` (contracts) and ``oiCcy`` (in base currency); use oiCcy.
        oi_value = oi.get("oiCcy") or oi.get("oi")
        if oi_value is None:
            raise RuntimeError("OKX OI payload missing both 'oiCcy' and 'oi'")
        return ExchangeFundingSnapshot(
            exchange="okx",
            funding_rate=float(funding["fundingRate"]),
            open_interest_base=float(oi_value),
            snapshot_time=_ms_to_naive_utc(int(oi["ts"])),
        )

    async def _fetch_bybit(self, symbol: str) -> ExchangeFundingSnapshot:
        funding, oi = await asyncio.gather(
            self.bybit.get_funding_rate(symbol),
            self.bybit.get_open_interest(symbol),
        )
        return ExchangeFundingSnapshot(
            exchange="bybit",
            funding_rate=float(funding["fundingRate"]),
            # Bybit linear BTCUSDT: 1 contract = 1 BTC, so this is base.
            open_interest_base=float(oi["openInterest"]),
            snapshot_time=_ms_to_naive_utc(int(oi["timestamp"])),
        )

    async def _fetch_bitget(self, symbol: str) -> ExchangeFundingSnapshot:
        funding, oi = await asyncio.gather(
            self.bitget.get_funding_rate(symbol),
            self.bitget.get_open_interest(symbol),
        )
        return ExchangeFundingSnapshot(
            exchange="bitget",
            funding_rate=float(funding["fundingRate"]),
            open_interest_base=float(oi["size"]),
            snapshot_time=datetime.now(UTC).replace(tzinfo=None),
        )

    async def _fetch_gateio(self, contract: str) -> ExchangeFundingSnapshot:
        info = await self.gateio.get_contract(contract)
        # OI in base = position_size (contracts) * quanto_multiplier (BTC/contract)
        position_size = float(info["position_size"])
        multiplier = float(info["quanto_multiplier"])
        return ExchangeFundingSnapshot(
            exchange="gateio",
            funding_rate=float(info["funding_rate"]),
            open_interest_base=position_size * multiplier,
            snapshot_time=datetime.now(UTC).replace(tzinfo=None),
        )

    # ----- Aggregation -----

    async def get_weighted_funding(self, symbol: str) -> WeightedFundingRate:
        ids = _resolve_symbols(symbol)
        fetchers: list[tuple[str, Callable[[], Awaitable[ExchangeFundingSnapshot]]]] = [
            ("binance", lambda: self._fetch_binance(ids["binance"])),
            ("okx", lambda: self._fetch_okx(ids["okx"])),
            ("bybit", lambda: self._fetch_bybit(ids["bybit"])),
            ("bitget", lambda: self._fetch_bitget(ids["bitget"])),
            ("gateio", lambda: self._fetch_gateio(ids["gateio"])),
        ]

        results = await asyncio.gather(
            *(coro() for _, coro in fetchers),
            return_exceptions=True,
        )

        snapshots: list[ExchangeFundingSnapshot] = []
        for (exchange_name, _), result in zip(fetchers, results, strict=True):
            if isinstance(result, BaseException):
                log.warning(
                    "exchange_fetch_failed",
                    exchange=exchange_name,
                    error=type(result).__name__,
                    message=str(result),
                )
                continue
            snapshots.append(result)

        if not snapshots:
            raise RuntimeError("self-aggregation failed: all exchanges returned errors")

        total_oi = sum(s.open_interest_base for s in snapshots)
        if total_oi <= 0:
            raise RuntimeError(f"self-aggregation failed: total OI is non-positive ({total_oi})")

        weighted = sum(s.funding_rate * s.open_interest_base for s in snapshots) / total_oi

        weighted_result = WeightedFundingRate(
            symbol=symbol.upper(),
            timestamp=datetime.now(UTC).replace(tzinfo=None),
            weighted_rate=weighted,
            source=self.name,
            sample_count=len(snapshots),
            components=tuple(snapshots),
        )

        log.info(
            "funding_aggregated",
            symbol=weighted_result.symbol,
            weighted_rate=weighted,
            sample_count=len(snapshots),
            sources=[s.exchange for s in snapshots],
        )
        return weighted_result


# ---------------------------------------------------------------------------
# Coinglass — stub
# ---------------------------------------------------------------------------


class CoinglassFundingProvider:
    """Coinglass-backed implementation — *not yet implemented*.

    Reserved for the day a Coinglass API key is configured. Until then we
    raise loudly rather than silently fall back, so the routing stays
    explicit at the configuration boundary (see :func:`make_funding_provider`).
    """

    name = "coinglass"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("Coinglass API key is required")
        self.api_key = api_key

    async def get_weighted_funding(self, symbol: str) -> WeightedFundingRate:
        raise NotImplementedError(
            "CoinglassFundingProvider is not implemented yet — "
            "leave COINGLASS_API_KEY empty to use self-aggregation."
        )

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_funding_provider(settings: Settings) -> FundingProvider:
    """Choose an implementation based on configuration.

    * ``COINGLASS_API_KEY`` set → :class:`CoinglassFundingProvider`
      (currently unimplemented; will raise on use).
    * otherwise                 → :class:`SelfAggregatedFundingProvider`.
    """
    if settings.coinglass_api_key is not None:
        api_key = settings.coinglass_api_key.get_secret_value()
        if api_key.strip():
            log.info("funding_provider_selected", provider="coinglass")
            return CoinglassFundingProvider(api_key=api_key)
    log.info("funding_provider_selected", provider="self_aggregated")
    return SelfAggregatedFundingProvider.from_settings(settings)


__all__ = [
    "SYMBOL_MAP",
    "CoinglassFundingProvider",
    "ExchangeFundingSnapshot",
    "FundingProvider",
    "SelfAggregatedFundingProvider",
    "WeightedFundingRate",
    "make_funding_provider",
]
