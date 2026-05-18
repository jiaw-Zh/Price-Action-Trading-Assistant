"""Stop hunt / liquidity sweep detection.

A **stop hunt** is the most actionable pattern in liquidity-driven markets:

1. Smart money drives price up (or down) just enough to pierce a known
   liquidity pool (Equal Highs / Equal Lows = retail stop concentration).
2. Stops trigger, providing the liquidity the smart money needs to fill
   the OPPOSITE direction.
3. Price immediately reverses; the bar that did the sweeping leaves a
   long rejection wick.

This is distinct from a *clean break*, where price closes beyond the
level and the trend continues. The two look identical in real time on
just the wick — only the close (and the next few bars) tell you which
one happened.

We classify a sweep as a stop hunt iff:

* The sweep bar's wick crossed the pool price ``L``
* The sweep bar's **close** returned back inside (``<= L`` for high
  sweeps, ``>= L`` for low sweeps)
* The rejection wick dominates the bar (``wick_ratio >= min_wick_ratio``)

We additionally tag each hunt with two strength signals:

* ``volume_ratio`` — the sweep bar's volume divided by the trailing
  ``volume_window`` average. Values > 1 indicate elevated participation.
* ``confirmed`` — whether the next ``confirmation_bars`` bars all
  refused to close beyond ``L`` again. A confirmed hunt is much less
  likely to be a fakeout-of-fakeout.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import polars as pl

from pa_assistant.analysis.liquidity import LiquidityLevel

HuntSide = Literal["high", "low"]


@dataclass(frozen=True, slots=True)
class StopHunt:
    """A confirmed stop-hunt event on a single bar.

    Attributes
    ----------
    timestamp:
        ``open_time`` of the sweep bar.
    side:
        ``"high"`` — sell-side stops above ``pool_price`` were hunted; the
        immediate bias is bearish reversal.
        ``"low"``  — buy-side stops below; bias is bullish reversal.
    pool_price:
        Price of the liquidity pool that was breached.
    pool_touches:
        How many swings reinforced the pool before it was hunted (more =
        higher-quality target).
    extreme:
        The bar's wick extreme — ``high`` for ``side="high"``, ``low`` for
        ``side="low"``. Distance from ``pool_price`` is the overshoot.
    close:
        The sweep bar's close.
    wick_ratio:
        Fraction of total bar range made up by the rejection wick:
        ``upper_wick / (high - low)`` for high sweeps,
        ``lower_wick / (high - low)`` for low sweeps. Range ``[0, 1]``.
        Higher = more pin-bar-like = stronger rejection.
    volume_ratio:
        ``bar_volume / mean(prior N bars' volume)``. ``1.0`` is average;
        ``> 1.5`` indicates institutional-style participation.
    confirmed:
        ``True`` iff every one of the next ``confirmation_bars`` bars
        closed back inside the pool (i.e. the reversal stuck). ``False``
        if any bar closed beyond, or if there weren't enough bars to
        check.
    """

    timestamp: datetime
    side: HuntSide
    pool_price: float
    pool_touches: int
    extreme: float
    close: float
    wick_ratio: float
    volume_ratio: float
    confirmed: bool


def detect_stop_hunts(
    df: pl.DataFrame,
    levels: list[LiquidityLevel],
    *,
    min_wick_ratio: float = 0.5,
    confirmation_bars: int = 3,
    volume_window: int = 20,
) -> list[StopHunt]:
    """Identify stop-hunt events given a set of liquidity pools.

    Parameters
    ----------
    df:
        Must contain ``open_time``, ``open``, ``high``, ``low``,
        ``close``, ``volume``. Sorted ascending by ``open_time``.
    levels:
        Pre-computed liquidity pools (typically from
        :func:`detect_liquidity_levels`). Only levels with non-``None``
        ``swept_at`` are considered.
    min_wick_ratio:
        Minimum rejection wick fraction (``0..1``) to qualify as a hunt.
        Default ``0.5`` — wick must be at least half the total bar range.
    confirmation_bars:
        Number of bars after the sweep to verify the reversal sticks.
        Default ``3``.
    volume_window:
        Trailing window for the average volume baseline. Default ``20``.

    Returns
    -------
    A list of :class:`StopHunt` ordered by ``timestamp``. A single bar
    that hunts multiple stacked pools produces multiple events with the
    same ``timestamp`` but different ``pool_price``.
    """
    if min_wick_ratio < 0 or min_wick_ratio > 1:
        raise ValueError(
            f"min_wick_ratio must be in [0, 1], got {min_wick_ratio}"
        )
    if confirmation_bars < 0:
        raise ValueError(
            f"confirmation_bars must be >= 0, got {confirmation_bars}"
        )
    if volume_window < 1:
        raise ValueError(f"volume_window must be >= 1, got {volume_window}")

    required = {"open_time", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"detect_stop_hunts: missing columns {missing}")

    if df.is_empty() or not levels:
        return []

    rows: list[tuple[datetime, float, float, float, float, float]] = list(
        df.select(["open_time", "open", "high", "low", "close", "volume"])
        .iter_rows()
    )
    time_to_idx: dict[datetime, int] = {r[0]: i for i, r in enumerate(rows)}

    hunts: list[StopHunt] = []

    for level in levels:
        if level.swept_at is None:
            continue
        idx = time_to_idx.get(level.swept_at)
        if idx is None:
            continue

        _ts, o, h, low, close, vol = rows[idx]

        # Reject clean breaks: close must be back inside the pool.
        if level.side == "high" and close > level.price:
            continue
        if level.side == "low" and close < level.price:
            continue

        bar_range = h - low
        if bar_range <= 0:
            continue  # doji or zero-range bar — can't compute ratio

        if level.side == "high":
            rejection_wick = h - max(o, close)
            extreme = h
        else:
            rejection_wick = min(o, close) - low
            extreme = low

        wick_ratio = rejection_wick / bar_range
        if wick_ratio < min_wick_ratio:
            continue

        # Volume ratio against trailing window (clamped to available bars).
        window_start = max(0, idx - volume_window)
        prior_vols = [r[5] for r in rows[window_start:idx]]
        if prior_vols:
            avg_vol = sum(prior_vols) / len(prior_vols)
            volume_ratio = vol / avg_vol if avg_vol > 0 else 1.0
        else:
            volume_ratio = 1.0

        # Confirmation: next N bars must not close beyond the pool again.
        confirmed: bool
        end_check = idx + 1 + confirmation_bars
        if end_check > len(rows):
            confirmed = False
        else:
            next_closes = [r[4] for r in rows[idx + 1 : end_check]]
            if level.side == "high":
                confirmed = all(c <= level.price for c in next_closes)
            else:
                confirmed = all(c >= level.price for c in next_closes)

        hunts.append(
            StopHunt(
                timestamp=level.swept_at,
                side=level.side,
                pool_price=level.price,
                pool_touches=len(level.touches),
                extreme=extreme,
                close=close,
                wick_ratio=wick_ratio,
                volume_ratio=volume_ratio,
                confirmed=confirmed,
            )
        )

    hunts.sort(key=lambda h: h.timestamp)
    return hunts
