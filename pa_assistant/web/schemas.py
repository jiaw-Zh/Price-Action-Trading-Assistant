"""Pydantic models for API requests and responses."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    """Analysis API request."""

    symbol: str = "BTCUSDT"
    timeframe: str = "1h"
    htf: str | None = None
    swing_lookback: int = Field(default=3, ge=1, le=5)
    eq_tolerance_bps: float = Field(default=10.0, ge=0.1)
    volume_climax_z: float = Field(default=2.0, ge=0.5)
    modules: list[str] = Field(
        default_factory=lambda: [
            "structure",
            "zones",
            "liquidity",
            "wyckoff",
            "divergence",
        ]
    )


class OHLCVBar(BaseModel):
    """Single OHLCV bar."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class WyckoffState(BaseModel):
    """Wyckoff analysis state."""

    phase: str
    confidence: float
    range_low: float | None = None
    range_high: float | None = None
    next_watch: str = ""


class TrendState(BaseModel):
    """Trend analysis state."""

    working: Literal["up", "down", "none"]
    htf: Literal["up", "down", "none"] = "none"
    alignment: str = ""


class LiquidityLevel(BaseModel):
    """Liquidity pool."""

    price: float
    side: Literal["high", "low"]
    touches: int
    spread_bps: float
    distance: float
    distance_pct: float
    status: Literal["active", "swept"]


class StructureEvent(BaseModel):
    """Structure event (BOS/CHoCH)."""

    timestamp: datetime
    event_type: str
    level: float
    trend_before: str
    trend_after: str


class OrderBlock(BaseModel):
    """Order block."""

    timestamp: datetime
    direction: Literal["bullish", "bearish"]
    top: float
    bottom: float
    status: Literal["active", "mitigated"]


class FairValueGap(BaseModel):
    """Fair value gap."""

    timestamp: datetime
    direction: Literal["bullish", "bearish"]
    top: float
    bottom: float
    status: Literal["unfilled", "filled"]


class Divergence(BaseModel):
    """Divergence event."""

    timestamp: datetime
    indicator: str
    side: Literal["bullish", "bearish"]
    strength: float
    swing_price: float
    indicator_value: float


class Scorecard(BaseModel):
    """Analysis scorecard."""

    net_bias: Literal["bullish", "bearish", "neutral"]
    bullish_factors: list[str] = Field(default_factory=list)
    bearish_factors: list[str] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    """Analysis API response."""

    timestamp: datetime
    symbol: str
    timeframe: str
    current_price: float

    wyckoff: WyckoffState | None = None
    trend: TrendState | None = None
    liquidity_levels: list[LiquidityLevel] = Field(default_factory=list)
    order_blocks: list[OrderBlock] = Field(default_factory=list)
    fvgs: list[FairValueGap] = Field(default_factory=list)
    structure_events: list[StructureEvent] = Field(default_factory=list)
    divergences: list[Divergence] = Field(default_factory=list)
    scorecard: Scorecard | None = None


class KlineResponse(BaseModel):
    """K-line data response."""

    bars: list[OHLCVBar]
    total: int
