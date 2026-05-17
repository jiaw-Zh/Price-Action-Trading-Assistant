"""Data ingestion: Binance + Coinglass connectors.

Submodules:

* :mod:`pa_assistant.ingestion.binance` — Binance USDT-M Futures REST client
  (historical klines, open interest). WebSocket streams will land in a sibling
  module ``binance_ws``.
* :mod:`pa_assistant.ingestion.coinglass` — *(planned)* Coinglass REST client
  for OI-weighted funding rate, with a multi-exchange self-aggregation
  fallback in ``funding_aggregator``.
"""

from pa_assistant.ingestion.binance import (
    INTERVAL_MS,
    BinanceRestClient,
    interval_to_ms,
    klines_to_polars,
)

__all__ = [
    "INTERVAL_MS",
    "BinanceRestClient",
    "interval_to_ms",
    "klines_to_polars",
]
