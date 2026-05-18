"""Gate.io Futures V4 public REST client (USDT-settled).

Endpoints:
* ``GET /api/v4/futures/usdt/contracts/{contract}`` — contract info (includes
  funding_rate, position_size, quanto_multiplier)

Gate.io does NOT wrap responses in an envelope — the JSON IS the data.
OI in base asset = position_size * quanto_multiplier.
"""

from __future__ import annotations

from typing import Any, Final

from pa_assistant.ingestion._http import AsyncRestClient

GATEIO_REST_BASE: Final[str] = "https://api.gateio.ws"


class GateioRestClient(AsyncRestClient):
    """Async client for Gate.io Futures V4 public endpoints."""

    def __init__(self, *, base_url: str = GATEIO_REST_BASE, **kwargs: Any) -> None:
        super().__init__(base_url=base_url, **kwargs)

    async def get_contract(self, contract: str) -> dict[str, Any]:
        """Full contract info including funding_rate and position_size."""
        result = await self._get(f"/api/v4/futures/usdt/contracts/{contract}")
        if not isinstance(result, dict):
            raise RuntimeError(f"Gate.io: unexpected payload: {result!r}")
        return result
