"""Market analysis engine.

This package contains pure, deterministic analysis primitives that operate
on Polars DataFrames. Anything network-bound or DuckDB-bound stays in
:mod:`pa_assistant.ingestion` / :mod:`pa_assistant.storage`.

Submodules:

* :mod:`pa_assistant.analysis.resample`   — 1m OHLCV → higher timeframe
* :mod:`pa_assistant.analysis.structure`  — swing detection + BOS / CHoCH
* :mod:`pa_assistant.analysis.volume`     — delta, CVD, VWAP + bands
* :mod:`pa_assistant.analysis.profile`    — Volume Profile (POC / VAH / VAL)
* :mod:`pa_assistant.analysis.zones`      — Order Blocks + Fair Value Gaps
* :mod:`pa_assistant.analysis.liquidity`  — Equal-Highs / Equal-Lows pools
* :mod:`pa_assistant.analysis.stop_hunt`  — Stop hunt / liquidity sweep events
"""

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
from pa_assistant.analysis.zones import (
    FairValueGap,
    OrderBlock,
    detect_fvgs,
    detect_order_blocks,
)

__all__ = [
    "FairValueGap",
    "LiquidityLevel",
    "OrderBlock",
    "StopHunt",
    "StructureEvent",
    "VolumeProfile",
    "compute_delta",
    "compute_volume_profile",
    "compute_vwap",
    "detect_fvgs",
    "detect_liquidity_levels",
    "detect_order_blocks",
    "detect_stop_hunts",
    "detect_structure_events",
    "detect_swings",
    "resample_ohlcv",
]
