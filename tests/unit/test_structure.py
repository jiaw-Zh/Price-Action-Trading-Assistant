"""Tests for swing detection + BOS / CHoCH events."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

import polars as pl
import pytest

from pa_assistant.analysis.structure import (
    detect_structure_events,
    detect_swings,
)


def _bars(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float] | None = None,
) -> pl.DataFrame:
    """Build a DataFrame with explicit highs/lows; closes default to (h+l)/2."""
    n = len(highs)
    assert len(lows) == n
    if closes is None:
        closes = [(highs[i] + lows[i]) / 2 for i in range(n)]
    base = datetime(2025, 1, 1)
    return pl.DataFrame(
        {
            "open_time": [base + timedelta(minutes=i) for i in range(n)],
            "open": [float(c) for c in closes],
            "high": [float(h) for h in highs],
            "low": [float(low) for low in lows],
            "close": [float(c) for c in closes],
        }
    )


# ---------------------------------------------------------------------------
# detect_swings
# ---------------------------------------------------------------------------


def test_swings_isolated_peak_and_trough_with_lookback_2() -> None:
    """A clean peak at index 4 and trough at index 9 should be detected."""
    #               idx:    0    1    2    3    4    5    6    7    8    9   10   11   12
    highs: list[float] = [10, 11, 12, 13, 20, 13, 12, 11, 10,  9, 10, 11, 12]
    lows: list[float] =  [ 8,  9, 10, 11, 18, 11, 10,  9,  8,  3,  8,  9, 10]
    df = _bars(highs, lows)
    out = detect_swings(df, lookback=2)

    sh = out.get_column("swing_high").to_list()
    sl = out.get_column("swing_low").to_list()

    # Only index 4 should be a swing high (high=20, with 2 lower bars on each side)
    assert sh[4] == 20.0
    assert all(v is None for i, v in enumerate(sh) if i != 4)
    # Only index 9 should be a swing low (low=3)
    assert sl[9] == 3.0
    assert all(v is None for i, v in enumerate(sl) if i != 9)


def test_swings_last_n_bars_are_unconfirmed() -> None:
    """Bars within `lookback` of either edge cannot be confirmed."""
    # Peak of 20 sits at idx 3 — has 3 bars to left, 3 bars to right (lookback=2 OK).
    # Idx 1 has only 1 bar to left → not confirmable even though it's a local max.
    highs = [10, 13, 11, 20, 11, 9, 8]
    lows = [5, 9, 4, 16, 7, 3, 2]
    df = _bars(highs, lows)
    out = detect_swings(df, lookback=2)
    sh = out.get_column("swing_high").to_list()
    sl = out.get_column("swing_low").to_list()
    # idx 3 IS confirmable (lookback=2 bars on each side)
    assert sh[3] == 20.0
    # idx 0, 1: insufficient left — must be null
    assert sh[0] is None
    assert sh[1] is None
    # idx 5, 6: insufficient right — must be null
    assert sl[5] is None  # low=3 looks small but only 1 right bar
    assert sl[6] is None


def test_swings_lookback_1_is_william_3bar() -> None:
    highs = [10, 12, 11]
    lows = [8, 7, 9]
    df = _bars(highs, lows)
    out = detect_swings(df, lookback=1)
    assert out.get_column("swing_high").to_list() == [None, 12.0, None]
    assert out.get_column("swing_low").to_list() == [None, 7.0, None]


def test_swings_invalid_lookback_raises() -> None:
    df = _bars([10, 11, 10], [9, 8, 9])
    with pytest.raises(ValueError, match="lookback must be"):
        detect_swings(df, lookback=0)


def test_swings_empty_df() -> None:
    df = pl.DataFrame(
        schema={
            "open_time": pl.Datetime,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
        }
    )
    out = detect_swings(df, lookback=2)
    assert out.height == 0
    assert "swing_high" in out.columns
    assert "swing_low" in out.columns


def test_swings_plateau_produces_no_swing() -> None:
    """Equal highs in a row → strict-on-both-sides rejects all of them."""
    highs = [10, 11, 12, 12, 11, 10, 9]
    lows = [8, 9, 10, 10, 9, 8, 7]
    df = _bars(highs, lows)
    out = detect_swings(df, lookback=2)
    sh = out.get_column("swing_high").to_list()
    # Both 12's: neither strictly > its neighbour — neither marked.
    assert sh[2] is None
    assert sh[3] is None


# ---------------------------------------------------------------------------
# detect_structure_events
# ---------------------------------------------------------------------------


def test_first_break_emits_choch_not_bos() -> None:
    """Initial trend is 'none' so the very first break is always a CHoCH."""
    # Manually construct: a swing high at idx 2, then a close above it later
    df = _bars(
        highs=[10, 12, 15, 13, 11, 10, 11, 16],
        lows=[8, 10, 13, 11, 9, 8, 9, 14],
        closes=[9, 11, 14, 12, 10, 9, 10, 16],  # close[7] = 16 > swing_high 15
    )
    out = detect_swings(df, lookback=2)
    events = detect_structure_events(out)

    assert len(events) == 1
    assert events[0].event_type == "CHoCH_up"
    assert events[0].level == 15.0
    assert events[0].trend_before == "none"
    assert events[0].trend_after == "up"


def test_continuation_emits_bos() -> None:
    """After establishing an uptrend, a second upward break is BOS, not CHoCH."""
    # Two swing highs (15 then 17), each broken by a higher close.
    # idx:       0   1   2   3   4   5   6   7   8   9  10  11  12
    highs   = [10, 12, 15, 13, 11, 12, 17, 15, 14, 13, 16, 18, 19]
    lows    = [ 8, 10, 13, 11,  9, 10, 15, 13, 12, 11, 14, 16, 17]
    closes  = [ 9, 11, 14, 12, 10, 11, 16, 14, 13, 12, 15, 18, 19]
    # 1st swing_high is idx 2 (=15). At idx 6 close=16 > 15 → CHoCH_up
    # 2nd swing_high is idx 6 (=17). At idx 11 close=18 > 17 → BOS_up
    df = _bars(highs, lows, closes)
    out = detect_swings(df, lookback=2)
    events = detect_structure_events(out)

    types = [e.event_type for e in events]
    assert "CHoCH_up" in types
    assert "BOS_up" in types
    # Order: CHoCH first, BOS second
    assert types.index("CHoCH_up") < types.index("BOS_up")


def test_reversal_uptrend_to_downtrend_emits_choch_down() -> None:
    """Up → break swing low → CHoCH_down (since trend was up)."""
    # Sequence built so that:
    #   idx 2: swing_high=15  → close at idx 5 breaks 15 → CHoCH_up (trend now up)
    #   idx 5: swing_high=16  → never broken
    #   idx 8: swing_low=7    → close at idx 11 breaks 7 → CHoCH_down
    highs = [10, 11, 15, 13, 12, 16, 14, 13, 12, 11, 10, 8, 7]
    lows = [8, 9, 13, 11, 10, 14, 10, 9, 7, 8, 9, 5, 4]
    closes = [9, 10, 14, 12, 11, 16, 12, 10, 8, 8, 9, 6, 5]
    df = _bars(highs, lows, closes)
    out = detect_swings(df, lookback=2)
    events = detect_structure_events(out)

    types = [(e.event_type, e.level) for e in events]
    assert ("CHoCH_up", 15.0) in types
    assert ("CHoCH_down", 7.0) in types
    # CHoCH_up should fire before CHoCH_down chronologically
    chronology = [e.event_type for e in events]
    assert chronology.index("CHoCH_up") < chronology.index("CHoCH_down")


def test_no_swings_no_events() -> None:
    """Monotone price → no swings → no events."""
    df = _bars(
        highs=[10, 11, 12, 13, 14, 15, 16, 17],
        lows=[8, 9, 10, 11, 12, 13, 14, 15],
    )
    out = detect_swings(df, lookback=2)
    events = detect_structure_events(out)
    assert events == []


def test_break_consumed_only_once() -> None:
    """The same swing level cannot trigger two events in a row."""
    df = _bars(
        # swing_high at idx 2 = 15, broken at idx 5
        highs=[10, 12, 15, 13, 11, 16, 17, 18, 19],
        lows=[8, 10, 13, 11, 9, 14, 15, 16, 17],
        closes=[9, 11, 14, 12, 10, 16, 17, 18, 19],
    )
    out = detect_swings(df, lookback=2)
    events = detect_structure_events(out)
    # Even though closes 16, 17, 18, 19 are all > 15, only one break event
    # for level 15. (Subsequent bars need a NEW swing high to break.)
    breaks_of_15 = [e for e in events if e.level == 15.0]
    assert len(breaks_of_15) == 1


def test_missing_columns_raises() -> None:
    df = pl.DataFrame({"open_time": [datetime(2025, 1, 1)], "high": [1.0]})
    with pytest.raises(ValueError, match="missing columns"):
        detect_structure_events(df)


def test_empty_df_returns_empty_event_list() -> None:
    df = pl.DataFrame(
        schema={
            "open_time": pl.Datetime,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "swing_high": pl.Float64,
            "swing_low": pl.Float64,
        }
    )
    assert detect_structure_events(df) == []
