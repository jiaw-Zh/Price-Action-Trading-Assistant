"""Market analysis engine.

This package contains pure, deterministic analysis primitives that operate
on Polars DataFrames. Anything network-bound or DuckDB-bound stays in
:mod:`pa_assistant.ingestion` / :mod:`pa_assistant.storage`.

Submodules:

* :mod:`pa_assistant.analysis.resample`  — 1m OHLCV → higher timeframe
* :mod:`pa_assistant.analysis.structure` — swing detection + BOS / CHoCH
* :mod:`pa_assistant.analysis.volume`    — delta, CVD, VWAP + bands
* :mod:`pa_assistant.analysis.profile`   — Volume Profile (POC / VAH / VAL)
"""

from pa_assistant.analysis.profile import VolumeProfile, compute_volume_profile
from pa_assistant.analysis.resample import resample_ohlcv
from pa_assistant.analysis.structure import (
    StructureEvent,
    detect_structure_events,
    detect_swings,
)
from pa_assistant.analysis.volume import compute_delta, compute_vwap

__all__ = [
    "StructureEvent",
    "VolumeProfile",
    "compute_delta",
    "compute_volume_profile",
    "compute_vwap",
    "detect_structure_events",
    "detect_swings",
    "resample_ohlcv",
]
