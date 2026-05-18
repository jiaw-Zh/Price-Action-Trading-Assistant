"""Data ingestion: exchange + funding-rate connectors.

Submodules:

* :mod:`pa_assistant.ingestion._http`  — common async HTTP base class
* :mod:`pa_assistant.ingestion.binance` — Binance USDT-M Futures REST
* :mod:`pa_assistant.ingestion.okx`     — OKX V5 public REST
* :mod:`pa_assistant.ingestion.bybit`   — Bybit V5 public REST
* :mod:`pa_assistant.ingestion.funding` — funding-rate Provider abstraction
  with self-aggregated implementation (Coinglass stubbed for now)
"""

from pa_assistant.ingestion.binance import (
    INTERVAL_MS,
    BinanceRestClient,
    interval_to_ms,
    klines_to_polars,
    oi_hist_to_polars,
)
from pa_assistant.ingestion.bitget import BitgetRestClient
from pa_assistant.ingestion.bybit import BybitRestClient
from pa_assistant.ingestion.funding import (
    CoinglassFundingProvider,
    ExchangeFundingSnapshot,
    FundingProvider,
    SelfAggregatedFundingProvider,
    WeightedFundingRate,
    make_funding_provider,
)
from pa_assistant.ingestion.gateio import GateioRestClient
from pa_assistant.ingestion.okx import OkxRestClient

__all__ = [
    "INTERVAL_MS",
    "BinanceRestClient",
    "BitgetRestClient",
    "BybitRestClient",
    "CoinglassFundingProvider",
    "ExchangeFundingSnapshot",
    "FundingProvider",
    "GateioRestClient",
    "OkxRestClient",
    "SelfAggregatedFundingProvider",
    "WeightedFundingRate",
    "interval_to_ms",
    "klines_to_polars",
    "make_funding_provider",
    "oi_hist_to_polars",
]
