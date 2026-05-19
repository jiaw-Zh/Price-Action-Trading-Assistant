"""Market analysis engine.

This package contains pure, deterministic analysis primitives that operate
on Polars DataFrames. Anything network-bound or DuckDB-bound stays in
:mod:`pa_assistant.ingestion` / :mod:`pa_assistant.storage`.

Submodules:

* :mod:`pa_assistant.analysis.resample`    — 1m OHLCV → higher timeframe
* :mod:`pa_assistant.analysis.structure`   — swing detection + BOS / CHoCH
* :mod:`pa_assistant.analysis.volume`      — delta, CVD, VWAP + bands
* :mod:`pa_assistant.analysis.profile`     — Volume Profile (POC / VAH / VAL)
* :mod:`pa_assistant.analysis.zones`       — Order Blocks + Fair Value Gaps
* :mod:`pa_assistant.analysis.liquidity`   — Equal-Highs / Equal-Lows pools
* :mod:`pa_assistant.analysis.stop_hunt`   — Stop hunt / liquidity sweep events
* :mod:`pa_assistant.analysis.divergence`  — Multi-indicator divergences (CVD/Volume/OI)
* :mod:`pa_assistant.analysis.wyckoff`     — Wyckoff phase state machine
"""

from pa_assistant.analysis.context import (
    ContextReport,
    FlowContext,
    FundingContext,
    LiquidityMap,
    Scorecard,
    StopHuntContext,
    TrendContext,
    WyckoffContext,
    ZoneContext,
    build_context_report,
    build_flow_context,
    build_funding_context,
    build_liquidity_map,
    build_stop_hunt_context,
    build_trend_context,
    build_wyckoff_context,
    build_zone_context,
    render_text,
)
from pa_assistant.analysis.divergence import (
    DivergenceEvent,
    detect_divergences,
)
from pa_assistant.analysis.liquidity import (
    LiquidityLevel,
    detect_liquidity_levels,
)
from pa_assistant.analysis.profile import VolumeProfile, compute_volume_profile
from pa_assistant.analysis.resample import resample_ohlcv
from pa_assistant.analysis.stop_hunt import StopHunt, detect_stop_hunts
from pa_assistant.analysis.structure import (
    StructureEvent,
    detect_structure_events,
    detect_swings,
)
from pa_assistant.analysis.volume import compute_delta, compute_vwap
from pa_assistant.analysis.wyckoff import (
    WyckoffEvent,
    WyckoffEventType,
    WyckoffPhase,
    WyckoffSnapshot,
    analyze_wyckoff,
    detect_wyckoff_events,
    evolve,
)
from pa_assistant.analysis.zones import (
    FairValueGap,
    OrderBlock,
    detect_fvgs,
    detect_order_blocks,
)

__all__ = [
    "ContextReport",
    "DivergenceEvent",
    "FairValueGap",
    "FlowContext",
    "FundingContext",
    "LiquidityLevel",
    "LiquidityMap",
    "OrderBlock",
    "Scorecard",
    "StopHunt",
    "StopHuntContext",
    "StructureEvent",
    "TrendContext",
    "VolumeProfile",
    "WyckoffContext",
    "WyckoffEvent",
    "WyckoffEventType",
    "WyckoffPhase",
    "WyckoffSnapshot",
    "ZoneContext",
    "analyze_wyckoff",
    "build_context_report",
    "build_flow_context",
    "build_funding_context",
    "build_liquidity_map",
    "build_stop_hunt_context",
    "build_trend_context",
    "build_wyckoff_context",
    "build_zone_context",
    "compute_delta",
    "compute_volume_profile",
    "compute_vwap",
    "detect_divergences",
    "detect_fvgs",
    "detect_liquidity_levels",
    "detect_order_blocks",
    "detect_stop_hunts",
    "detect_structure_events",
    "detect_swings",
    "detect_wyckoff_events",
    "evolve",
    "render_text",
    "resample_ohlcv",
]
