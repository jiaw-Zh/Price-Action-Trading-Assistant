"""Multi-indicator divergence detection.

A *divergence* is when price makes a new extreme (HH or LL) but a
confirmation indicator fails to follow. It signals that the move lacks
underlying participation and is at risk of reversing.

This module is **indicator-agnostic** — the analysis is identical
regardless of which series we compare against price. Pass in a DataFrame
that contains whichever indicator columns you want to evaluate; we
detect divergences for each of them independently.

Supported indicators (any subset, depending on which columns are
present in the input):

* **CVD** (``cvd``) — cumulative net taker-buy volume. Bearish
  divergence at a HH means cumulative buying *failed* to make a new
  high; sellers are absorbing.
* **Volume** (``volume``) — VSA "no-demand / no-supply" reading.
  Bearish divergence at a HH means the new high was reached on weaker
  volume → exhaustion.
* **OI** (``oi``) — open interest. The cleanest signal in derivatives
  markets:

  * Price ↑ + OI ↑ → new longs entering (healthy trend)
  * Price ↑ + OI ↓ → short covering driving the move (likely fakeout)
  * Price ↓ + OI ↑ → new shorts entering (sustained downtrend)
  * Price ↓ + OI ↓ → longs unwinding (capitulation, near bottom)

  We mark the OI ↓ branches at swing highs and lows as divergences.

Method
------

For each pair of *adjacent same-type swings* (e.g. consecutive swing
highs) separated by at least ``min_separation_bars``:

* Bearish divergence (at swing highs):
    - price: ``swing_N.high > swing_{N-1}.high``
    - indicator: ``ind_N < ind_{N-1}``
* Bullish divergence (at swing lows):
    - price: ``swing_N.low < swing_{N-1}.low``
    - indicator: ``ind_N > ind_{N-1}``

The "adjacent" choice (vs sliding window over multiple prior swings)
keeps the detector unambiguous and matches how traders actually read
divergences off charts. Strength is a 0..1 normalized magnitude of the
indicator's relative move.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Literal

import polars as pl

from pa_assistant.analysis.structure import detect_swings

DivergenceSide = Literal["bearish", "bullish"]
Indicator = Literal["cvd", "volume", "oi"]

INDICATOR_COLUMNS: dict[Indicator, str] = {
    "cvd": "cvd",
    "volume": "volume",
    "oi": "oi",
}


@dataclass(frozen=True, slots=True)
class DivergenceEvent:
    """A divergence between price and a confirmation indicator at swing N
    vs swing N-1.

    Attributes
    ----------
    timestamp:
        ``open_time`` of the *later* (divergent) swing.
    side:
        ``"bearish"`` — at a higher swing high; expect downside reversal.
        ``"bullish"`` — at a lower swing low; expect upside reversal.
    indicator:
        Which series was compared: ``"cvd"`` / ``"volume"`` / ``"oi"``.
    swing_price:
        Price at the later swing (high for bearish, low for bullish).
    prior_swing_price:
        Price at the prior same-type swing.
    prior_swing_time:
        Timestamp of the prior swing.
    indicator_value:
        Indicator value at the later swing.
    prior_indicator_value:
        Indicator value at the prior swing.
    strength:
        Normalized divergence magnitude in ``[0, 1]``: the absolute
        relative change of the indicator,
        ``|ind_N - ind_{N-1}| / max(|ind_N|, |ind_{N-1}|)``. Higher =
        stronger divergence. ``0`` if both indicator values are zero.
    """

    timestamp: datetime
    side: DivergenceSide
    indicator: Indicator
    swing_price: float
    prior_swing_price: float
    prior_swing_time: datetime
    indicator_value: float
    prior_indicator_value: float
    strength: float


def detect_divergences(
    df: pl.DataFrame,
    *,
    indicators: list[Indicator] | None = None,
    lookback: int = 2,
    min_separation_bars: int = 3,
) -> list[DivergenceEvent]:
    """Find divergences for each requested indicator.

    Parameters
    ----------
    df:
        Must contain ``open_time``, ``high``, ``low``. For each indicator
        listed in ``indicators``, the corresponding column must also be
        present (``cvd`` / ``volume`` / ``oi``).
    indicators:
        Subset of ``["cvd", "volume", "oi"]``. Default: all three (any
        whose column is missing is silently skipped — useful when OI is
        unavailable).
    lookback:
        Forwarded to :func:`detect_swings`. Default 2.
    min_separation_bars:
        Minimum number of bars between two consecutive same-type swings
        for the comparison to count. Adjacent fractals can be 1-2 bars
        apart and produce noise; raise this to filter low-quality pairs.

    Returns
    -------
    A list of :class:`DivergenceEvent` ordered by ``timestamp``.
    """
    if lookback < 1:
        raise ValueError(f"lookback must be >= 1, got {lookback}")
    if min_separation_bars < 0:
        raise ValueError(
            f"min_separation_bars must be >= 0, got {min_separation_bars}"
        )

    required = {"open_time", "high", "low"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"detect_divergences: missing columns {missing}")

    requested: list[Indicator] = (
        indicators if indicators is not None else ["cvd", "volume", "oi"]
    )
    # Filter to indicators whose column is actually present.
    active: list[Indicator] = [
        ind for ind in requested if INDICATOR_COLUMNS[ind] in df.columns
    ]
    if not active:
        return []

    if df.is_empty():
        return []

    annotated = detect_swings(df, lookback=lookback)

    # Index swings by row position so we can compute bar separation.
    annotated_with_idx = annotated.with_row_index("_idx")
    high_pts: list[tuple[int, datetime, float]] = list(
        annotated_with_idx.filter(pl.col("swing_high").is_not_null())
        .select(["_idx", "open_time", "swing_high"])
        .iter_rows()
    )
    low_pts: list[tuple[int, datetime, float]] = list(
        annotated_with_idx.filter(pl.col("swing_low").is_not_null())
        .select(["_idx", "open_time", "swing_low"])
        .iter_rows()
    )

    # Snapshot indicator columns by row position for fast lookup.
    indicator_values: dict[Indicator, list[float]] = {}
    for ind in active:
        col = INDICATOR_COLUMNS[ind]
        indicator_values[ind] = df.get_column(col).to_list()

    events: list[DivergenceEvent] = []

    # Bearish: consecutive swing highs where price rises but indicator falls.
    for prev, curr in _consecutive_pairs(high_pts):
        prev_idx, prev_ts, prev_price = prev
        curr_idx, curr_ts, curr_price = curr
        if curr_idx - prev_idx < min_separation_bars:
            continue
        if curr_price <= prev_price:
            continue  # not a HH; no bearish-divergence opportunity
        for ind in active:
            values = indicator_values[ind]
            ind_prev = float(values[prev_idx])
            ind_curr = float(values[curr_idx])
            if ind_curr >= ind_prev:
                continue  # indicator confirmed price → no divergence
            events.append(
                DivergenceEvent(
                    timestamp=curr_ts,
                    side="bearish",
                    indicator=ind,
                    swing_price=curr_price,
                    prior_swing_price=prev_price,
                    prior_swing_time=prev_ts,
                    indicator_value=ind_curr,
                    prior_indicator_value=ind_prev,
                    strength=_strength(ind_prev, ind_curr),
                )
            )

    # Bullish: consecutive swing lows where price falls but indicator rises.
    for prev, curr in _consecutive_pairs(low_pts):
        prev_idx, prev_ts, prev_price = prev
        curr_idx, curr_ts, curr_price = curr
        if curr_idx - prev_idx < min_separation_bars:
            continue
        if curr_price >= prev_price:
            continue  # not a LL
        for ind in active:
            values = indicator_values[ind]
            ind_prev = float(values[prev_idx])
            ind_curr = float(values[curr_idx])
            if ind_curr <= ind_prev:
                continue
            events.append(
                DivergenceEvent(
                    timestamp=curr_ts,
                    side="bullish",
                    indicator=ind,
                    swing_price=curr_price,
                    prior_swing_price=prev_price,
                    prior_swing_time=prev_ts,
                    indicator_value=ind_curr,
                    prior_indicator_value=ind_prev,
                    strength=_strength(ind_prev, ind_curr),
                )
            )

    events.sort(key=lambda e: e.timestamp)
    return events


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _consecutive_pairs(
    items: list[tuple[int, datetime, float]],
) -> list[tuple[tuple[int, datetime, float], tuple[int, datetime, float]]]:
    """Return overlapping pairs ``[(a, b), (b, c), (c, d), ...]``."""
    if len(items) < 2:
        return []
    return list(pairwise(items))


def _strength(prev: float, curr: float) -> float:
    """Normalized divergence magnitude in ``[0, 1]``.

    Defined as ``|curr - prev| / max(|curr|, |prev|)``. Returns 0 if
    both values are zero (avoids division by zero); clamps at 1.0 in
    case of sign flips around zero (when ``|curr - prev| > max(|c|, |p|)``).
    """
    denom = max(abs(prev), abs(curr))
    if denom == 0:
        return 0.0
    return min(abs(curr - prev) / denom, 1.0)
