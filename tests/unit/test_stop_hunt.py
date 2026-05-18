"""Tests for stop-hunt detection."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

import polars as pl
import pytest

from pa_assistant.analysis.liquidity import LiquidityLevel
from pa_assistant.analysis.stop_hunt import detect_stop_hunts


def _bars(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float] | None = None,
) -> pl.DataFrame:
    n = len(opens)
    base = datetime(2025, 1, 1)
    if volumes is None:
        volumes = [100.0] * n
    return pl.DataFrame(
        {
            "open_time": [base + timedelta(minutes=i) for i in range(n)],
            "open": [float(x) for x in opens],
            "high": [float(x) for x in highs],
            "low": [float(x) for x in lows],
            "close": [float(x) for x in closes],
            "volume": [float(x) for x in volumes],
        }
    )


def _level(
    price: float,
    side: str,
    swept_at_idx: int | None,
    touches: int = 2,
) -> LiquidityLevel:
    base = datetime(2025, 1, 1)
    return LiquidityLevel(
        price=price,
        side=side,  # type: ignore[arg-type]
        touches=[base + timedelta(minutes=i) for i in range(touches)],
        first_seen=base,
        last_seen=base + timedelta(minutes=touches - 1),
        spread_bps=0.0,
        swept_at=(
            base + timedelta(minutes=swept_at_idx)
            if swept_at_idx is not None
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_invalid_min_wick_ratio() -> None:
    df = _bars([1], [1], [1], [1])
    with pytest.raises(ValueError, match="min_wick_ratio must be"):
        detect_stop_hunts(df, [], min_wick_ratio=-0.1)
    with pytest.raises(ValueError, match="min_wick_ratio must be"):
        detect_stop_hunts(df, [], min_wick_ratio=1.5)


def test_invalid_confirmation_bars() -> None:
    df = _bars([1], [1], [1], [1])
    with pytest.raises(ValueError, match="confirmation_bars"):
        detect_stop_hunts(df, [], confirmation_bars=-1)


def test_invalid_volume_window() -> None:
    df = _bars([1], [1], [1], [1])
    with pytest.raises(ValueError, match="volume_window"):
        detect_stop_hunts(df, [], volume_window=0)


def test_missing_columns_raise() -> None:
    df = pl.DataFrame({"open_time": [datetime(2025, 1, 1)], "high": [1.0]})
    with pytest.raises(ValueError, match="missing columns"):
        detect_stop_hunts(df, [])


def test_empty_inputs_return_empty() -> None:
    df = _bars([], [], [], [])
    assert detect_stop_hunts(df, []) == []
    df2 = _bars([1, 2], [1, 2], [1, 2], [1, 2])
    assert detect_stop_hunts(df2, []) == []


# ---------------------------------------------------------------------------
# High-side stop hunts
# ---------------------------------------------------------------------------


def test_high_sweep_with_close_inside_is_stop_hunt() -> None:
    """Bar wicks above L=100 to 105 then closes at 99 → classic pin bar."""
    # idx 0: setup, idx 1: sweep bar, idx 2-4: confirmation
    df = _bars(
        opens=  [99,  98, 96, 95, 94],
        highs=  [99, 105, 97, 96, 95],  # idx 1 wicks to 105
        lows=   [97,  97, 94, 93, 92],
        closes= [98,  99, 95, 94, 93],  # idx 1 closes at 99 (back inside L=100)
    )
    levels = [_level(price=100.0, side="high", swept_at_idx=1)]
    hunts = detect_stop_hunts(df, levels, confirmation_bars=3)

    assert len(hunts) == 1
    h = hunts[0]
    assert h.side == "high"
    assert h.pool_price == 100.0
    assert h.extreme == 105.0
    assert h.close == 99.0
    # bar range = 105 - 97 = 8
    # rejection wick = 105 - max(98, 99) = 105 - 99 = 6
    # wick_ratio = 6/8 = 0.75
    assert h.wick_ratio == pytest.approx(0.75)
    assert h.confirmed is True


def test_high_sweep_with_close_above_is_clean_break() -> None:
    """Wick to 105, but close at 102 (above L=100) → not a stop hunt."""
    df = _bars(
        opens=  [99,  98, 102, 103, 104],
        highs=  [99, 105, 103, 104, 105],
        lows=   [97,  97, 101, 102, 103],
        closes= [98, 102, 103, 104, 105],
    )
    levels = [_level(price=100.0, side="high", swept_at_idx=1)]
    hunts = detect_stop_hunts(df, levels, confirmation_bars=3)
    assert hunts == []


def test_high_sweep_below_min_wick_ratio_is_skipped() -> None:
    """Close just barely back inside, no real rejection wick."""
    # bar range = 100.5 - 97 = 3.5
    # rejection wick = 100.5 - max(99, 99.9) = 0.6
    # wick_ratio = 0.6/3.5 = 0.17 < 0.5
    df = _bars(
        opens=  [99,  99, 100, 99, 98],
        highs=  [99, 100.5, 100, 99, 98],
        lows=   [97,  97, 98, 97, 96],
        closes= [98, 99.9, 99, 98, 97],
    )
    levels = [_level(price=100.0, side="high", swept_at_idx=1)]
    hunts = detect_stop_hunts(df, levels, min_wick_ratio=0.5)
    assert hunts == []


def test_high_sweep_unconfirmed_when_next_bar_closes_above() -> None:
    df = _bars(
        opens=  [99,  98, 99, 100, 101],
        highs=  [99, 105, 102, 103, 104],
        lows=   [97,  97, 98, 99, 100],
        closes= [98,  99, 101, 102, 103],  # idx 2 closes 101 > L=100
    )
    levels = [_level(price=100.0, side="high", swept_at_idx=1)]
    hunts = detect_stop_hunts(df, levels, confirmation_bars=3)
    assert len(hunts) == 1
    assert hunts[0].confirmed is False


def test_confirmation_window_too_short_marks_unconfirmed() -> None:
    """Not enough trailing bars → confirmed = False (conservative)."""
    df = _bars(
        opens=  [99,  98, 96],
        highs=  [99, 105, 97],
        lows=   [97,  97, 94],
        closes= [98,  99, 95],
    )
    levels = [_level(price=100.0, side="high", swept_at_idx=1)]
    hunts = detect_stop_hunts(df, levels, confirmation_bars=3)
    # Only 1 bar after the sweep, but we need 3 for confirmation → False
    assert len(hunts) == 1
    assert hunts[0].confirmed is False


# ---------------------------------------------------------------------------
# Low-side stop hunts
# ---------------------------------------------------------------------------


def test_low_sweep_with_close_inside_is_stop_hunt() -> None:
    df = _bars(
        opens=  [101, 102, 104, 105, 106],
        highs=  [103, 103, 105, 106, 107],
        lows=   [101,  95, 103, 104, 105],  # idx 1 wicks to 95
        closes= [102, 101, 104, 105, 106],  # idx 1 closes at 101 (back above L=100)
    )
    levels = [_level(price=100.0, side="low", swept_at_idx=1)]
    hunts = detect_stop_hunts(df, levels, confirmation_bars=3)

    assert len(hunts) == 1
    h = hunts[0]
    assert h.side == "low"
    assert h.extreme == 95.0
    assert h.close == 101.0
    # bar range = 103 - 95 = 8
    # rejection wick = min(102, 101) - 95 = 6
    # wick_ratio = 6/8 = 0.75
    assert h.wick_ratio == pytest.approx(0.75)
    assert h.confirmed is True


def test_low_sweep_clean_break_is_skipped() -> None:
    df = _bars(
        opens=  [101, 102, 96, 95, 94],
        highs=  [103, 103, 97, 96, 95],
        lows=   [101,  95, 94, 93, 92],
        closes= [102,  96, 95, 94, 93],  # idx 1 closes at 96 < L=100 → clean break
    )
    levels = [_level(price=100.0, side="low", swept_at_idx=1)]
    hunts = detect_stop_hunts(df, levels)
    assert hunts == []


# ---------------------------------------------------------------------------
# Volume ratio
# ---------------------------------------------------------------------------


def test_volume_ratio_baseline_is_average_of_prior_window() -> None:
    """Sweep bar volume = 500 vs prior avg 100 → volume_ratio = 5.0."""
    df = _bars(
        opens=  [99] * 30 + [98, 96, 95, 94],
        highs=  [99] * 30 + [105, 97, 96, 95],
        lows=   [97] * 30 + [97, 94, 93, 92],
        closes= [98] * 30 + [99, 95, 94, 93],
        volumes=[100] * 30 + [500, 100, 100, 100],
    )
    levels = [_level(price=100.0, side="high", swept_at_idx=30)]
    hunts = detect_stop_hunts(df, levels, volume_window=20, confirmation_bars=3)
    assert len(hunts) == 1
    assert hunts[0].volume_ratio == pytest.approx(5.0)


def test_volume_ratio_neutral_when_no_prior_bars() -> None:
    """Sweep bar at index 0 → no prior history → ratio = 1.0."""
    df = _bars(
        opens=  [98, 96, 95, 94],
        highs=  [105, 97, 96, 95],
        lows=   [97, 94, 93, 92],
        closes= [99, 95, 94, 93],
    )
    levels = [_level(price=100.0, side="high", swept_at_idx=0)]
    hunts = detect_stop_hunts(df, levels, volume_window=20, confirmation_bars=3)
    assert len(hunts) == 1
    assert hunts[0].volume_ratio == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Multi-level interactions
# ---------------------------------------------------------------------------


def test_unswept_level_produces_no_hunt() -> None:
    df = _bars([99], [99], [97], [98])
    levels = [_level(price=100.0, side="high", swept_at_idx=None)]
    assert detect_stop_hunts(df, levels) == []


def test_level_swept_at_unknown_timestamp_is_skipped() -> None:
    """Sweep timestamp doesn't match any bar → just skip, don't crash."""
    df = _bars([99, 98], [99, 105], [97, 97], [98, 99])
    bogus_ts = datetime(2099, 1, 1)
    bogus_level = LiquidityLevel(
        price=100.0,
        side="high",
        touches=[datetime(2025, 1, 1)],
        first_seen=datetime(2025, 1, 1),
        last_seen=datetime(2025, 1, 1),
        spread_bps=0.0,
        swept_at=bogus_ts,
    )
    assert detect_stop_hunts(df, [bogus_level]) == []


def test_two_pools_swept_by_same_bar_emit_two_hunts() -> None:
    """One bar wicks past two stacked high pools at 100 and 102."""
    df = _bars(
        opens=  [99,  98, 96, 95, 94],
        highs=  [99, 105, 97, 96, 95],
        lows=   [97,  97, 94, 93, 92],
        closes= [98,  99, 95, 94, 93],  # closes at 99 (below both 100 and 102)
    )
    levels = [
        _level(price=100.0, side="high", swept_at_idx=1),
        _level(price=102.0, side="high", swept_at_idx=1),
    ]
    hunts = detect_stop_hunts(df, levels)
    assert len(hunts) == 2
    prices = sorted(h.pool_price for h in hunts)
    assert prices == [100.0, 102.0]


def test_hunts_sorted_by_timestamp() -> None:
    df = _bars(
        opens=  [99,  98,  96, 95, 100, 105, 100, 99, 98],
        highs=  [99, 105, 97, 96, 100, 110, 100, 99, 98],
        lows=   [97,  97, 94, 93,  98, 102,  98, 97, 96],
        closes= [98,  99, 95, 94,  99, 103, 99, 98, 97],
    )
    levels = [
        _level(price=100.0, side="high", swept_at_idx=1),
        _level(price=108.0, side="high", swept_at_idx=5),
    ]
    hunts = detect_stop_hunts(df, levels, confirmation_bars=2)
    assert len(hunts) == 2
    assert hunts[0].timestamp < hunts[1].timestamp


def test_zero_range_bar_is_skipped() -> None:
    """Doji where high == low → no ratio computable → skipped silently."""
    df = _bars(
        opens=  [99, 100, 96, 95, 94],
        highs=  [99, 100, 97, 96, 95],
        lows=   [97, 100, 94, 93, 92],  # idx 1 has high == low
        closes= [98, 100, 95, 94, 93],
    )
    levels = [_level(price=100.0, side="high", swept_at_idx=1)]
    hunts = detect_stop_hunts(df, levels)
    assert hunts == []
