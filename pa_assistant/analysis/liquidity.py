"""Liquidity pool detection.

In leveraged markets, retail stop-loss orders cluster at obvious chart
levels:

* **Equal Highs** — multiple swing highs at (nearly) the same price. Sell
  stops sit just above. A break here triggers a cascade.
* **Equal Lows** — multiple swing lows at the same price. Buy stops sit
  just below.

Smart money hunts these zones to source liquidity for their entries. This
module identifies those clusters, tracks how many swings reinforce each
level ("touches"), and records when (if ever) price wicked through the
level — i.e. the **sweep**.

A liquidity level survives until the first wick takes out its price; we
record the timestamp of that first sweep. Untouched levels remain
candidates for future stop hunts.

Future work (not in this slice):

* Round-number / psychological levels (e.g. \\$80,000 for BTC) treated
  as implicit liquidity even without prior swings touching them.
* ATR-relative tolerance (current implementation uses a fixed bps band).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import polars as pl

from pa_assistant.analysis.structure import detect_swings

LiquiditySide = Literal["high", "low"]


@dataclass(frozen=True, slots=True)
class LiquidityLevel:
    """A cluster of equal swings = stop-loss concentration zone.

    Attributes
    ----------
    price:
        Representative price of the cluster (mean of member swings).
    side:
        ``"high"`` — sell stops sit ABOVE this level (Equal Highs).
        ``"low"``  — buy stops sit BELOW this level (Equal Lows).
    touches:
        Sorted timestamps of every swing in the cluster.
    first_seen / last_seen:
        Earliest / latest swing timestamp in ``touches``.
    spread_bps:
        Width of the cluster in basis points
        ``(max_price - min_price) / mean_price * 10_000``. A tighter
        cluster (smaller spread) is a higher-quality liquidity pool.
    swept_at:
        Timestamp of the first bar AFTER ``last_seen`` whose wick crossed
        the cluster's max (for highs) or min (for lows). ``None`` means
        the level remains untouched.
    """

    price: float
    side: LiquiditySide
    touches: list[datetime] = field(hash=False, compare=False)
    first_seen: datetime
    last_seen: datetime
    spread_bps: float
    swept_at: datetime | None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_liquidity_levels(
    df: pl.DataFrame,
    *,
    lookback: int = 2,
    tolerance_bps: float = 5.0,
    min_touches: int = 2,
) -> list[LiquidityLevel]:
    """Find Equal-Highs / Equal-Lows liquidity pools and their sweep status.

    Pipeline:

    1. Run :func:`detect_swings` to get individual swing points.
    2. Greedy-cluster swing highs by proximity (running mean within
       ``tolerance_bps``); same for lows.
    3. Drop clusters with fewer than ``min_touches`` swings.
    4. For each surviving cluster, walk forward from ``last_seen + 1``
       to find the first wick that crosses the cluster boundary.

    Parameters
    ----------
    df:
        Must contain ``open_time``, ``high``, ``low`` and be sorted
        ascending by ``open_time``.
    lookback:
        Forwarded to :func:`detect_swings`. Default 2 (5-bar fractal).
    tolerance_bps:
        Maximum distance from the running cluster mean to absorb a new
        swing into the cluster, in basis points. Default 5 bps (= 0.05%);
        for BTC at \\$77k that's ≈\\$38, sensible on 1h.
    min_touches:
        Minimum number of swings required for a cluster to be reported.
        Default 2; raise to 3 for higher-confidence pools.

    Returns
    -------
    A list of :class:`LiquidityLevel`, ordered by ``last_seen`` ascending.
    Highs and lows are interleaved by time, not separated.
    """
    if lookback < 1:
        raise ValueError(f"lookback must be >= 1, got {lookback}")
    if tolerance_bps <= 0:
        raise ValueError(f"tolerance_bps must be > 0, got {tolerance_bps}")
    if min_touches < 2:
        raise ValueError(f"min_touches must be >= 2, got {min_touches}")

    required = {"open_time", "high", "low"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"detect_liquidity_levels: missing columns {missing}")

    if df.is_empty():
        return []

    annotated = detect_swings(df, lookback=lookback)

    high_pts: list[tuple[datetime, float]] = list(
        annotated.filter(pl.col("swing_high").is_not_null())
        .select(["open_time", "swing_high"])
        .iter_rows()
    )
    low_pts: list[tuple[datetime, float]] = list(
        annotated.filter(pl.col("swing_low").is_not_null())
        .select(["open_time", "swing_low"])
        .iter_rows()
    )

    # Sweep detection needs the full bar series (high/low columns).
    bars: list[tuple[datetime, float, float]] = list(
        df.select(["open_time", "high", "low"]).iter_rows()
    )

    high_clusters = _cluster_by_price(high_pts, tolerance_bps)
    low_clusters = _cluster_by_price(low_pts, tolerance_bps)

    levels: list[LiquidityLevel] = []
    for cluster in high_clusters:
        if len(cluster) < min_touches:
            continue
        levels.append(_build_level(cluster, "high", bars))
    for cluster in low_clusters:
        if len(cluster) < min_touches:
            continue
        levels.append(_build_level(cluster, "low", bars))

    levels.sort(key=lambda lv: lv.last_seen)
    return levels


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _cluster_by_price(
    points: list[tuple[datetime, float]],
    tolerance_bps: float,
) -> list[list[tuple[datetime, float]]]:
    """Greedy 1-D clustering by price.

    Sort by price, then absorb each next point into the current cluster
    if it lies within ``tolerance_bps`` of the cluster's running mean.
    Otherwise start a new cluster.
    """
    if not points:
        return []

    sorted_pts = sorted(points, key=lambda p: p[1])
    clusters: list[list[tuple[datetime, float]]] = [[sorted_pts[0]]]

    for ts, price in sorted_pts[1:]:
        current = clusters[-1]
        running_mean = sum(p[1] for p in current) / len(current)
        if running_mean == 0:
            continue
        diff_bps = abs(price - running_mean) / running_mean * 10_000
        if diff_bps <= tolerance_bps:
            current.append((ts, price))
        else:
            clusters.append([(ts, price)])

    return clusters


def _build_level(
    cluster: list[tuple[datetime, float]],
    side: LiquiditySide,
    bars: list[tuple[datetime, float, float]],
) -> LiquidityLevel:
    """Compute aggregate stats + sweep timestamp for a cluster."""
    prices = [p for _, p in cluster]
    timestamps = sorted(ts for ts, _ in cluster)
    mean_price = sum(prices) / len(prices)
    min_p = min(prices)
    max_p = max(prices)
    spread_bps = (max_p - min_p) / mean_price * 10_000 if mean_price > 0 else 0.0

    last_seen = timestamps[-1]

    # Sweep boundary: highest price for high cluster, lowest for low cluster.
    boundary = max_p if side == "high" else min_p

    swept_at: datetime | None = None
    for ts, h, low in bars:
        if ts <= last_seen:
            continue
        if side == "high" and h > boundary:
            swept_at = ts
            break
        if side == "low" and low < boundary:
            swept_at = ts
            break

    return LiquidityLevel(
        price=mean_price,
        side=side,
        touches=timestamps,
        first_seen=timestamps[0],
        last_seen=last_seen,
        spread_bps=spread_bps,
        swept_at=swept_at,
    )
