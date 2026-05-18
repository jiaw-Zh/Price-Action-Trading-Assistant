"""Bitget V2 Mix public REST client (USDT-M futures).

Endpoints:
* ``GET /api/v2/mix/market/current-fund-rate`` — current funding rate
* ``GET /api/v2/mix/market/open-interest``      — open interest

Bitget envelope: ``{"code": "00000", "msg": "success", "data": ...}``.
"""

from __future__ import annotations

from typing import Any, Final

from pa_assistant.ingestion._http import AsyncRestClient

BITGET_REST_BASE: Final[str] = "https://api.bitget.com"


class BitgetRestClient(AsyncRestClient):
    """Async client for Bitget V2 Mix public endpoints."""

    def __init__(self, *, base_url: str = BITGET_REST_BASE, **kwargs: Any) -> None:
        super().__init__(base_url=base_url, **kwargs)

    async def _get_unwrapped(self, path: str, **params: Any) -> Any:
        result = await self._get(path, **params)
        if not isinstance(result, dict):
            raise RuntimeError(f"Bitget: unexpected payload: {result!r}")
        code = str(result.get("code", ""))
        if code != "00000":
            raise RuntimeError(f"Bitget error {code}: {result.get('msg', '')}")
        return result["data"]

    async def get_funding_rate(self, symbol: str) -> dict[str, Any]:
        data = await self._get_unwrapped(
            "/api/v2/mix/market/current-fund-rate",
            symbol=symbol.upper(),
            productType="USDT-FUTURES",
        )
        if isinstance(data, list):
            if not data:
                raise RuntimeError(f"Bitget: empty funding rate for {symbol!r}")
            return data[0]  # type: ignore[no-any-return]
        raise RuntimeError(f"Bitget: unexpected data shape: {data!r}")

    async def get_open_interest(self, symbol: str) -> dict[str, Any]:
        data = await self._get_unwrapped(
            "/api/v2/mix/market/open-interest",
            symbol=symbol.upper(),
            productType="USDT-FUTURES",
        )
        if not isinstance(data, dict):
            raise RuntimeError(f"Bitget: unexpected OI shape: {data!r}")
        items = data.get("openInterestList") or []
        if not items:
            raise RuntimeError(f"Bitget: empty OI for {symbol!r}")
        first = items[0]
        if not isinstance(first, dict):
            raise RuntimeError(f"Bitget: malformed OI entry: {first!r}")
        return first
