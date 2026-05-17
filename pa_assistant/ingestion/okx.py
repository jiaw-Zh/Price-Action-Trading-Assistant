"""OKX V5 public REST client (perpetual swap).

Endpoints used:

* ``GET /api/v5/public/funding-rate``  — current funding rate
* ``GET /api/v5/public/open-interest`` — current open interest

OKX wraps every successful response in ``{"code": "0", "msg": "", "data": [...]}``.
We unwrap that here and surface the inner data list / object directly.

Symbol format: OKX uses ``BTC-USDT-SWAP`` for perpetual swaps. Mapping to/from
the canonical ``BTCUSDT`` form lives in :mod:`pa_assistant.ingestion.funding`.
"""

from __future__ import annotations

from typing import Any, Final

from pa_assistant.ingestion._http import AsyncRestClient
from pa_assistant.logging import get_logger

log = get_logger(__name__)

OKX_REST_BASE: Final[str] = "https://www.okx.com"


class OkxRestClient(AsyncRestClient):
    """Async client for OKX V5 public market endpoints."""

    def __init__(self, *, base_url: str = OKX_REST_BASE, **kwargs: Any) -> None:
        super().__init__(base_url=base_url, **kwargs)

    async def _get_unwrapped(self, path: str, **params: Any) -> list[dict[str, Any]]:
        """Issue a GET, validate the OKX envelope, return the ``data`` list."""
        result = await self._get(path, **params)
        if not isinstance(result, dict):
            raise RuntimeError(f"OKX: unexpected payload shape: {result!r}")
        code = str(result.get("code", ""))
        if code != "0":
            raise RuntimeError(f"OKX error {code}: {result.get('msg', '<no msg>')}")
        data = result.get("data")
        if not isinstance(data, list):
            raise RuntimeError(f"OKX: missing 'data' list in response: {result!r}")
        return data

    async def get_funding_rate(self, inst_id: str) -> dict[str, Any]:
        """Current funding rate for ``instId`` (e.g. ``'BTC-USDT-SWAP'``)."""
        data = await self._get_unwrapped("/api/v5/public/funding-rate", instId=inst_id)
        if not data:
            raise RuntimeError(f"OKX: empty funding-rate response for {inst_id!r}")
        return data[0]

    async def get_open_interest(self, inst_id: str) -> dict[str, Any]:
        """Current open interest for ``instId``."""
        data = await self._get_unwrapped(
            "/api/v5/public/open-interest", instType="SWAP", instId=inst_id
        )
        if not data:
            raise RuntimeError(f"OKX: empty open-interest response for {inst_id!r}")
        return data[0]
