"""Market analysis engine.

This package contains pure, deterministic analysis primitives that operate
on Polars DataFrames. Anything network-bound or DuckDB-bound stays in
:mod:`pa_assistant.ingestion` / :mod:`pa_assistant.storage`.

Submodules:

* :mod:`pa_assistant.analysis.resample`  — 1m OHLCV → higher timeframe
* :mod:`pa_assistant.analysis.structure` — swing detection + BOS / CHoCH
"""

from pa_assistant.analysis.resample import resample_ohlcv
from pa_assistant.analysis.structure import (
    StructureEvent,
    detect_structure_events,
    detect_swings,
)

__all__ = [
    "StructureEvent",
    "detect_structure_events",
    "detect_swings",
    "resample_ohlcv",
]
