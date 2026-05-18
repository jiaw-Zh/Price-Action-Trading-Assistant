"""Tests for liquidity-pool detection (Equal Highs / Equal Lows)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from itertools import pairwise

import polars as pl
import pytest

from pa_assistant.analysis.liquidity import (
    LiquidityLevel,
    detect_liquidity_levels,
)


def _bars(
    highs: Sequence[float],
    lows: Sequence[float],
) -> pl.DataFrame:
    """Build a minimal df with highs/lows and synthetic open/close.

    open = (high + low) / 2 keeps it valid OHLC (not directly checked here
    but useful if we ever add open/close requirements).
    """
    n = len(highs)
    base = datetime(2025, 1, 1)
    return pl.DataFrame(
        {
            "open_time": [base + timedelta(minutes=i) for i in range(n)],
            "open": [(h + low) / 2.0 for h, low in zip(highs, lows, strict=True)],
            "high": [float(x) for x in highs],
            "low": [float(x) for x in lows],
            "close": [(h + low) / 2.0 for h, low in zip(highs, lows, strict=True)],
        }
    )


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_lookback_must_be_positive() -> None:
    df = _bars([1.0], [0.5])
    with pytest.raises(ValueError, match="lookback must be"):
        detect_liquidity_levels(df, lookback=0)


def test_tolerance_must_be_positive() -> None:
    df = _bars([1.0], [0.5])
    with pytest.raises(ValueError, match="tolerance_bps must be"):
        detect_liquidity_levels(df, tolerance_bps=0)


def test_min_touches_must_be_at_least_two() -> None:
    df = _bars([1.0], [0.5])
    with pytest.raises(ValueError, match="min_touches must be"):
        detect_liquidity_levels(df, min_touches=1)


def test_missing_columns_raise() -> None:
    df = pl.DataFrame({"open_time": [datetime(2025, 1, 1)], "high": [1.0]})
    with pytest.raises(ValueError, match="missing columns"):
        detect_liquidity_levels(df)


def test_empty_df_returns_empty() -> None:
    df = _bars([], [])
    assert detect_liquidity_levels(df) == []


# ---------------------------------------------------------------------------
# Equal Highs detection
# ---------------------------------------------------------------------------


def test_two_equal_highs_form_a_cluster() -> None:
    """Two swing highs at exactly the same price → 1 high cluster."""
    # Build a sequence with two clear swing highs at price 100.
    # lookback=2 → swing high needs higher than 2 bars on each side.
    highs = [
        90, 92, 94, 96, 100, 96, 94,   # swing high at idx 4
        92, 94, 96, 100, 96, 94, 92,   # swing high at idx 10
    ]
    lows = [h - 5 for h in highs]
    df = _bars(highs, lows)
    levels = detect_liquidity_levels(df, lookback=2, tolerance_bps=5.0)

    high_levels = [lv for lv in levels if lv.side == "high"]
    assert len(high_levels) == 1
    lv = high_levels[0]
    assert lv.price == pytest.approx(100.0)
    assert len(lv.touches) == 2
    assert lv.spread_bps == pytest.approx(0.0)
    assert lv.swept_at is None  # no later wick crosses 100


def test_two_equal_lows_form_a_cluster() -> None:
    highs = [
        110, 108, 106, 104, 100, 104, 106,
        108, 106, 104, 100, 104, 106, 108,
    ]
    # Build symmetric: swing lows at idx 4 and idx 10, both at price 95.
    lows = [
        105, 103, 101,  99,  95, 99, 101,
        103, 101,  99,  95, 99, 101, 103,
    ]
    df = _bars(highs, lows)
    levels = detect_liquidity_levels(df, lookback=2, tolerance_bps=5.0)

    low_levels = [lv for lv in levels if lv.side == "low"]
    assert len(low_levels) == 1
    lv = low_levels[0]
    assert lv.price == pytest.approx(95.0)
    assert len(lv.touches) == 2


def test_separate_high_clusters_when_outside_tolerance() -> None:
    """Highs at 100 and 110 should be two clusters, not one."""
    highs = [
        90, 92, 94, 96, 100, 96, 94,
        92, 94, 96, 100, 96, 94, 92,    # both at 100
        94, 96, 98, 100, 110, 100, 98,  # this one at 110
        96, 98, 100, 110, 100, 98, 96,  # also at 110
    ]
    lows = [h - 5 for h in highs]
    df = _bars(highs, lows)
    levels = detect_liquidity_levels(df, lookback=2, tolerance_bps=5.0)

    high_levels = [lv for lv in levels if lv.side == "high"]
    assert len(high_levels) == 2
    prices = sorted(lv.price for lv in high_levels)
    assert prices[0] == pytest.approx(100.0)
    assert prices[1] == pytest.approx(110.0)


def test_min_touches_filters_singletons() -> None:
    """A swing high alone (only 1 touch) should be filtered out."""
    highs = [90, 92, 94, 96, 100, 96, 94, 92]  # one swing high
    lows = [h - 5 for h in highs]
    df = _bars(highs, lows)
    levels = detect_liquidity_levels(df, lookback=2, min_touches=2)
    assert [lv for lv in levels if lv.side == "high"] == []


def test_tolerance_groups_near_but_not_equal_highs() -> None:
    """Swing highs within tolerance get grouped even if not exactly equal."""
    # 100 and 100.04 — at price ~100, that's ~4 bps apart. tolerance=5 → group.
    highs = [
        90, 92, 94, 96, 100.00, 96, 94,
        92, 94, 96, 100.04, 96, 94, 92,
    ]
    lows = [h - 5 for h in highs]
    df = _bars(highs, lows)
    levels = detect_liquidity_levels(df, lookback=2, tolerance_bps=5.0)
    high_levels = [lv for lv in levels if lv.side == "high"]
    assert len(high_levels) == 1
    assert high_levels[0].spread_bps > 0
    assert high_levels[0].spread_bps < 5


def test_tolerance_too_strict_keeps_them_separate() -> None:
    """Same data with tighter tolerance → two clusters, both filtered out by min_touches."""
    highs = [
        90, 92, 94, 96, 100.00, 96, 94,
        92, 94, 96, 100.04, 96, 94, 92,
    ]
    lows = [h - 5 for h in highs]
    df = _bars(highs, lows)
    levels = detect_liquidity_levels(df, lookback=2, tolerance_bps=1.0)
    # Tolerance too tight → 2 separate singleton clusters → filtered by min_touches=2
    high_levels = [lv for lv in levels if lv.side == "high"]
    assert high_levels == []


# ---------------------------------------------------------------------------
# Sweep detection
# ---------------------------------------------------------------------------


def test_sweep_recorded_when_later_wick_crosses_high_boundary() -> None:
    """Two equal highs at 100, then a later bar wicks to 102 → swept."""
    highs = [
        90, 92, 94, 96, 100, 96, 94,
        92, 94, 96, 100, 96, 94, 92,
        94, 96, 98, 102, 98, 96, 94,  # idx 17 wicks to 102 > 100 → sweep
    ]
    lows = [h - 5 for h in highs]
    df = _bars(highs, lows)
    levels = detect_liquidity_levels(df, lookback=2, tolerance_bps=5.0)
    high_levels = [lv for lv in levels if lv.side == "high"]
    assert len(high_levels) == 1
    lv = high_levels[0]
    assert lv.swept_at == datetime(2025, 1, 1, 0, 17)


def test_sweep_recorded_when_later_wick_crosses_low_boundary() -> None:
    highs = [110, 108, 106, 104, 100, 104, 106, 108, 106, 104, 100, 104, 106, 108]
    lows = [105, 103, 101, 99, 95, 99, 101, 103, 101, 99, 95, 99, 101, 103]
    # Append bars where a later low wicks to 90 (below 95)
    extra_h = [105, 107, 109, 107]
    extra_l = [101, 103, 90, 105]  # idx 16 dips to 90
    full_h = highs + extra_h
    full_l = lows + extra_l
    df_full = _bars(full_h, full_l)
    levels = detect_liquidity_levels(df_full, lookback=2, tolerance_bps=5.0)
    low_levels = [lv for lv in levels if lv.side == "low"]
    assert len(low_levels) == 1
    assert low_levels[0].swept_at == datetime(2025, 1, 1, 0, 16)


def test_sweep_only_counts_bars_after_last_seen() -> None:
    """A wick BETWEEN the two equal highs doesn't count as a sweep; only bars
    after the last touch can sweep."""
    # Equal highs at idx 4 and idx 14. Idx 9 wicks to 105 (between them).
    # That should NOT be considered a sweep because last_seen=14.
    highs = [
        90, 92, 94, 96, 100, 96, 94, 92, 94,
        105,  # mid-range wick
        96, 98, 96, 94, 100, 96, 94,
    ]
    lows = [h - 5 for h in highs]
    df = _bars(highs, lows)
    levels = detect_liquidity_levels(df, lookback=2, tolerance_bps=5.0)
    high_levels = [lv for lv in levels if lv.side == "high"]
    # idx 9 wicks to 105 → it's actually the highest swing high, would form
    # its own cluster if it were a swing; let's verify the structure.
    # Filter to only the "100" cluster.
    cluster_100 = [lv for lv in high_levels if 99 < lv.price < 101]
    assert len(cluster_100) == 1
    # last_seen for the 100-cluster is idx 14. No bars after idx 14 wick to >100.
    assert cluster_100[0].swept_at is None


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------


def test_result_sorted_by_last_seen() -> None:
    """Output ordering: ascending by last_seen, mixing high/low."""
    highs = [
        90, 92, 94, 96, 100, 96, 94,    # high cluster A: idx 4
        92, 94, 96, 100, 96, 94, 92,    # high cluster A: idx 10
        # Now a low cluster B with last_seen later
        94, 96, 98, 96,
        90, 88, 86, 84, 80, 84, 86,     # low at 80, idx 22
        88, 86, 84, 80, 84, 86,         # low at 80, idx 28
    ]
    lows = [
        85, 87, 89, 91, 95, 91, 89,
        87, 89, 91, 95, 91, 89, 87,
        89, 91, 93, 91,
        85, 83, 81, 79, 75, 79, 81,
        83, 81, 79, 75, 79, 81,
    ]
    df = _bars(highs, lows)
    levels = detect_liquidity_levels(df, lookback=2, tolerance_bps=5.0)
    if len(levels) >= 2:
        for a, b in pairwise(levels):
            assert a.last_seen <= b.last_seen


def test_dataclass_immutable() -> None:
    lv = LiquidityLevel(
        price=100.0,
        side="high",
        touches=[datetime(2025, 1, 1)],
        first_seen=datetime(2025, 1, 1),
        last_seen=datetime(2025, 1, 1),
        spread_bps=0.0,
        swept_at=None,
    )
    with pytest.raises(AttributeError):
        lv.price = 200.0  # type: ignore[misc]
