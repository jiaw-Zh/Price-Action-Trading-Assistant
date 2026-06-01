"""CoinGecko API client for public derivatives tickers.

Used as a robust, WAF-proof and geo-compliant fallback to retrieve
Binance Futures funding rates and open interest when direct Binance calls fail.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from pa_assistant.ingestion._http import AsyncRestClient
from pa_assistant.logging import get_logger

log = get_logger(__name__)

COINGECKO_PUBLIC_BASE: Final[str] = "https://api.coingecko.com/api/v3/"


class CoinGeckoRestClient(AsyncRestClient):
    """Async client for CoinGecko API (public / pro plans)."""

    def __init__(
        self,
        *,
        base_url: str = COINGECKO_PUBLIC_BASE,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        # Ensure base_url has a trailing slash for correct relative resolution
        if not base_url.endswith("/"):
            base_url += "/"

        headers = {}
        if api_key:
            # Auto-detect Pro plan vs Demo plan based on base URL
            key_header = (
                "x-cg-pro-api-key" if "pro-api" in base_url else "x-cg-demo-api-key"
            )
            headers[key_header] = api_key

        client = kwargs.get("client")
        if client is not None and headers:
            client.headers.update(headers)

        super().__init__(base_url=base_url, headers=headers, **kwargs)


    async def get_derivatives_tickers(self) -> list[dict[str, Any]]:
        """Fetch all derivatives tickers from CoinGecko.

        Endpoint: ``GET derivatives`` (resolves to ``<base_url>/derivatives``).
        """
        # We request without leading slash to preserve base_url sub-path
        result = await self._get("derivatives")
        if not isinstance(result, list):
            log.warning("coingecko_unexpected_payload_shape", payload=result)
            return []
        return result

    async def get_binance_futures_ticker(
        self, symbol: str = "BTCUSDT"
    ) -> dict[str, Any] | None:
        """Get the specific Binance Futures ticker and normalize it.

        Returns normalized dictionary with ``funding_rate``, ``open_interest_base``,
        and ``timestamp``, or ``None`` if not found.
        """
        tickers = await self.get_derivatives_tickers()
        target_sym = symbol.upper()

        for t in tickers:
            market = t.get("market")
            sym = t.get("symbol")
            if not market or not sym:
                continue

            # CoinGecko represents Binance Futures as "Binance (Futures)" or similar
            if "Binance" in market and "Futures" in market:
                # CoinGecko symbol could be "BTCUSDT" or "BTC" depending on mapping
                # Standard derivatives ticker symbol matches "BTCUSDT" or "BTC" (with perpetual contract type)
                if sym.upper() == target_sym or target_sym.startswith(sym.upper()):
                    log.info(
                        "coingecko_binance_ticker_found",
                        market=market,
                        symbol=sym,
                        funding_rate=t.get("funding_rate"),
                    )
                    return t

        log.warning(
            "coingecko_binance_ticker_not_found",
            symbol=target_sym,
            ticker_count=len(tickers),
        )
        return None


def parse_coingecko_binance_ticker(
    ticker: dict[str, Any]
) -> dict[str, Any]:
    """Parse raw CoinGecko ticker into normalized futures metrics.

    Converts Open Interest from USD notional to base asset units.
    """
    funding_rate = float(ticker.get("funding_rate") or 0.0)
    
    # CoinGecko open_interest is in USD notional, we divide by price to get base asset units
    oi_usd = float(ticker.get("open_interest") or 0.0)
    price = float(ticker.get("price") or ticker.get("index") or 1.0)
    oi_base = oi_usd / price if price > 0 else 0.0

    # CoinGecko last_traded_at is Unix timestamp in seconds
    last_trade_sec = int(ticker.get("last_traded_at") or datetime.now(UTC).timestamp())
    timestamp = datetime.fromtimestamp(last_trade_sec, tz=UTC).replace(tzinfo=None)

    return {
        "funding_rate": funding_rate,
        "open_interest_base": oi_base,
        "timestamp": timestamp,
    }
