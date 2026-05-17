"""Bybit V5 public REST client (linear perpetual).

Endpoints used:

* ``GET /v5/market/funding/history`` — funding rate history (we take latest)
* ``GET /v5/market/open-interest``   — open-interest series

Bybit wraps every response in ``{"retCode": 0, "retMsg": "OK",
"result": {"list": [...]}}``. We unwrap that here.
"""

from __future__ import annotations

from typing import Any, Final

from pa_assistant.ingestion._http import AsyncRestClient
from pa_assistant.logging import get_logger

log = get_logger(__name__)

BYBIT_REST_BASE: Final[str] = "https://api.bybit.com"


class BybitRestClient(AsyncRestClient):
    """Async client for Bybit V5 public market endpoints (linear category)."""

    def __init__(self, *, base_url: str = BYBIT_REST_BASE, **kwargs: Any) -> None:
        super().__init__(base_url=base_url, **kwargs)

    async def _get_unwrapped(self, path: str, **params: Any) -> dict[str, Any]:
        """Issue a GET, validate the Bybit envelope, return the ``result`` object."""
        result = await self._get(path, **params)
        if not isinstance(result, dict):
            raise RuntimeError(f"Bybit: unexpected payload shape: {result!r}")
        ret_code = result.get("retCode")
        if ret_code != 0:
            raise RuntimeError(f"Bybit error {ret_code}: {result.get('retMsg', '<no msg>')}")
        inner = result.get("result")
        if not isinstance(inner, dict):
            raise RuntimeError(f"Bybit: missing 'result' object: {result!r}")
        return inner

    async def get_funding_rate(self, symbol: str) -> dict[str, Any]:
        """Latest funding rate snapshot for ``symbol`` (linear perpetual)."""
        result = await self._get_unwrapped(
            "/v5/market/funding/history",
            category="linear",
            symbol=symbol.upper(),
            limit=1,
        )
        items = result.get("list") or []
        if not items:
            raise RuntimeError(f"Bybit: empty funding history for {symbol!r}")
        first = items[0]
        if not isinstance(first, dict):
            raise RuntimeError(f"Bybit: malformed funding entry: {first!r}")
        return first

    async def get_open_interest(
        self,
        symbol: str,
        *,
        interval_time: str = "5min",
    ) -> dict[str, Any]:
        """Latest OI snapshot for ``symbol`` at ``interval_time`` granularity."""
        result = await self._get_unwrapped(
            "/v5/market/open-interest",
            category="linear",
            symbol=symbol.upper(),
            intervalTime=interval_time,
            limit=1,
        )
        items = result.get("list") or []
        if not items:
            raise RuntimeError(f"Bybit: empty open-interest list for {symbol!r}")
        first = items[0]
        if not isinstance(first, dict):
            raise RuntimeError(f"Bybit: malformed OI entry: {first!r}")
        return first
