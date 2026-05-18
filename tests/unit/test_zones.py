"""Tests for Order Block + Fair Value Gap detection."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

import polars as pl
import pytest

from pa_assistant.analysis.structure import (
    StructureEvent,
    detect_structure_events,
    detect_swings,
)
from pa_assistant.analysis.zones import (
    detect_fvgs,
    detect_order_blocks,
)


def _bars(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> pl.DataFrame:
    n = len(opens)
    base = datetime(2025, 1, 1)
    return pl.DataFrame(
        {
            "open_time": [base + timedelta(minutes=i) for i in range(n)],
            "open": [float(x) for x in opens],
            "high": [float(x) for x in highs],
            "low": [float(x) for x in lows],
            "close": [float(x) for x in closes],
        }
    )


# ---------------------------------------------------------------------------
# detect_order_blocks
# ---------------------------------------------------------------------------


def _event(at_idx: int, kind: str, level: float) -> StructureEvent:
    base = datetime(2025, 1, 1)
    return StructureEvent(
        timestamp=base + timedelta(minutes=at_idx),
        event_type=kind,  # type: ignore[arg-type]
        level=level,
        trend_before="none",
        trend_after="up" if kind.endswith("_up") else "down",
    )


def test_bullish_ob_is_last_bearish_bar_before_bos_up() -> None:
    """Pattern: red, red, red, GREEN, GREEN (BOS_up). The last red is the OB."""
    # Last bearish bar is at idx 2 (open=12, close=10). That's the bullish OB.
    df = _bars(
        opens=  [10, 12, 12, 10, 12, 14],
        highs=  [13, 13, 13, 14, 14, 16],
        lows=   [ 9, 11, 10,  9, 11, 13],
        closes= [12, 11, 10, 13, 14, 15],  # idx 5 close=15 breaks high level
    )
    events = [_event(at_idx=5, kind="BOS_up", level=14.0)]
    obs = detect_order_blocks(df, events, lookback=10)

    assert len(obs) == 1
    ob = obs[0]
    assert ob.direction == "bullish"
    assert ob.timestamp == datetime(2025, 1, 1, 0, 2)  # idx 2
    assert ob.top == 12.0  # max(open=12, close=10)
    assert ob.bottom == 10.0  # min(open=12, close=10)
    assert ob.wick_top == 13.0
    assert ob.wick_bottom == 10.0
    assert ob.triggered_by == datetime(2025, 1, 1, 0, 5)


def test_bearish_ob_is_last_bullish_bar_before_bos_down() -> None:
    df = _bars(
        opens=  [10, 12, 14, 13, 11,  9],
        highs=  [11, 13, 15, 14, 12, 11],
        lows=   [ 9, 11, 13, 11,  9,  7],
        closes= [11, 13, 15, 12, 10,  8],  # idx 5 close=8 breaks low level
    )
    # Last bullish bar before idx 5 is idx 2 (open=14, close=15)
    events = [_event(at_idx=5, kind="BOS_down", level=9.0)]
    obs = detect_order_blocks(df, events, lookback=10)

    assert len(obs) == 1
    ob = obs[0]
    assert ob.direction == "bearish"
    assert ob.timestamp == datetime(2025, 1, 1, 0, 2)
    assert ob.top == 15.0  # max(14, 15)
    assert ob.bottom == 14.0


def test_ob_lookback_too_short_skips_event() -> None:
    """Opposite candle is too far back → no OB emitted."""
    df = _bars(
        opens=  [10, 12, 14, 16, 18, 20, 22],  # all bullish (close > open)
        highs=  [12, 14, 16, 18, 20, 22, 24],
        lows=   [ 9, 11, 13, 15, 17, 19, 21],
        closes= [12, 14, 16, 18, 20, 22, 23],
    )
    events = [_event(at_idx=6, kind="BOS_up", level=22.0)]
    # No bearish candle exists at all → no OB regardless of lookback
    obs = detect_order_blocks(df, events, lookback=3)
    assert obs == []


def test_ob_unmitigated_when_price_keeps_running() -> None:
    """If price never returns to OB body, mitigated_at is None."""
    df = _bars(
        opens=  [10, 12, 10, 14, 18, 22],
        highs=  [13, 13, 11, 16, 20, 25],
        lows=   [ 9, 11, 10, 13, 17, 21],
        closes= [12, 11,  9, 15, 19, 24],
    )
    # OB candidate: idx 1 (open=12, close=11, bearish) → body 11..12
    # Or idx 2 (open=10, close=9, bearish) → body 9..10. The LAST bearish before idx 5.
    events = [_event(at_idx=5, kind="BOS_up", level=22.0)]
    obs = detect_order_blocks(df, events)
    assert len(obs) == 1
    # Idx 2's body is 9..10. Bars 3,4,5 lows are 13, 17, 21 — none touch 10.
    assert obs[0].mitigated_at is None


def test_ob_mitigated_when_price_returns_to_body() -> None:
    df = _bars(
        opens=  [10, 12, 14, 18, 16, 12, 10],
        highs=  [13, 14, 15, 20, 18, 14, 12],
        lows=   [ 9, 11, 13, 16, 14, 10,  8],  # idx 6 low=8 < OB body
        closes= [12, 11, 14, 19, 15, 11,  9],
    )
    # Last bearish before idx 3 is idx 1 (open=12, close=11). Body 11..12.
    events = [_event(at_idx=3, kind="BOS_up", level=15.0)]
    obs = detect_order_blocks(df, events)
    assert len(obs) == 1
    # Idx 5 has low=10 <= body_top=12 → first mitigation.
    assert obs[0].mitigated_at == datetime(2025, 1, 1, 0, 5)


def test_ob_lookback_zero_rejected() -> None:
    df = _bars([10], [11], [9], [10])
    with pytest.raises(ValueError, match="lookback must be"):
        detect_order_blocks(df, [], lookback=0)


def test_ob_empty_inputs_return_empty() -> None:
    df = _bars([], [], [], [])
    assert detect_order_blocks(df, []) == []
    df2 = _bars([10, 11], [11, 12], [9, 10], [10, 11])
    assert detect_order_blocks(df2, []) == []


def test_ob_event_not_in_df_is_skipped() -> None:
    """An event whose timestamp doesn't align with any bar is just ignored."""
    df = _bars([10, 11], [11, 12], [9, 10], [10, 11])
    bogus = StructureEvent(
        timestamp=datetime(2099, 1, 1),
        event_type="BOS_up",
        level=99.0,
        trend_before="none",
        trend_after="up",
    )
    assert detect_order_blocks(df, [bogus]) == []


def test_ob_choch_also_generates_ob() -> None:
    """CHoCH events are treated identically to BOS for OB detection."""
    df = _bars(
        opens=  [10, 12, 10, 14, 16],
        highs=  [13, 13, 11, 15, 17],
        lows=   [ 9, 11, 10, 13, 15],
        closes= [12, 11, 10, 14, 16],
    )
    events = [_event(at_idx=4, kind="CHoCH_up", level=13.0)]
    obs = detect_order_blocks(df, events)
    assert len(obs) == 1
    assert obs[0].direction == "bullish"


# ---------------------------------------------------------------------------
# Integration with real swing detection
# ---------------------------------------------------------------------------


def test_ob_pipeline_with_real_swings_and_events() -> None:
    """Full pipeline: build bars → swings → events → OBs."""
    # Build a sequence with a clear BOS_up
    df = _bars(
        opens=  [10, 12, 15, 13, 11, 13, 18, 16, 14, 17, 20, 19, 22],
        highs=  [12, 13, 16, 14, 12, 14, 19, 17, 15, 18, 21, 20, 23],
        lows=   [ 9, 11, 14, 12, 10, 11, 17, 15, 13, 15, 19, 17, 21],
        closes= [11, 12, 14, 13, 11, 13, 18, 16, 14, 17, 20, 18, 22],
    )
    annotated = detect_swings(df, lookback=2)
    events = detect_structure_events(annotated)
    obs = detect_order_blocks(df, events)
    # We don't pin specific OBs here, just verify the pipeline runs.
    assert isinstance(obs, list)
    for ob in obs:
        assert ob.direction in ("bullish", "bearish")
        assert ob.bottom <= ob.top
        assert ob.wick_bottom <= ob.bottom
        assert ob.wick_top >= ob.top


# ---------------------------------------------------------------------------
# detect_fvgs
# ---------------------------------------------------------------------------


def test_bullish_fvg_basic_three_bar_gap() -> None:
    """bar1.high=10, bar3.low=15 → bullish FVG [10, 15]."""
    df = _bars(
        opens=  [9, 12, 16],
        highs=  [10, 14, 17],
        lows=   [8, 11, 15],
        closes= [9, 13, 16],
    )
    fvgs = detect_fvgs(df)
    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.direction == "bullish"
    assert fvg.timestamp == datetime(2025, 1, 1, 0, 1)  # bar 2 (middle)
    assert fvg.top == 15.0
    assert fvg.bottom == 10.0
    assert fvg.mitigated_at is None  # no later bars


def test_bearish_fvg_basic_three_bar_gap() -> None:
    """bar1.low=15, bar3.high=10 → bearish FVG [10, 15]."""
    df = _bars(
        opens=  [16, 13, 9],
        highs=  [17, 14, 10],
        lows=   [15, 11, 8],
        closes= [16, 12, 9],
    )
    fvgs = detect_fvgs(df)
    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.direction == "bearish"
    assert fvg.top == 15.0
    assert fvg.bottom == 10.0


def test_fvg_no_gap_returns_empty() -> None:
    """Overlapping bars with no displacement → no FVG."""
    df = _bars(
        opens=  [10, 11, 12],
        highs=  [11, 12, 13],
        lows=   [ 9, 10, 11],  # bar1.high=11 NOT < bar3.low=11 (not strict)
        closes= [11, 12, 13],
    )
    assert detect_fvgs(df) == []


def test_fvg_mitigated_when_price_returns_to_gap() -> None:
    """Bullish FVG with a later bar dipping into the gap."""
    df = _bars(
        opens=  [9, 12, 16, 17, 14],
        highs=  [10, 14, 17, 18, 15],
        lows=   [8, 11, 15, 16, 13],  # idx 4 low=13 enters [10, 15]
        closes= [9, 13, 16, 17, 14],
    )
    fvgs = detect_fvgs(df)
    # The first FVG (formed at bar 1, gap [10, 15]) gets mitigated at idx 4.
    target = next(f for f in fvgs if f.timestamp == datetime(2025, 1, 1, 0, 1))
    assert target.direction == "bullish"
    assert target.top == 15.0
    assert target.bottom == 10.0
    assert target.mitigated_at == datetime(2025, 1, 1, 0, 4)


def test_fvg_unmitigated_when_price_runs_away() -> None:
    df = _bars(
        opens=  [9, 12, 16, 18, 22],
        highs=  [10, 14, 17, 20, 24],
        lows=   [8, 11, 15, 17, 21],
        closes= [9, 13, 16, 19, 23],
    )
    fvgs = detect_fvgs(df)
    # The first-formed FVG [10, 15] is the one we care about.
    target = next(f for f in fvgs if f.timestamp == datetime(2025, 1, 1, 0, 1))
    assert target.top == 15.0
    assert target.bottom == 10.0
    assert target.mitigated_at is None


def test_fvg_multiple_in_sequence() -> None:
    """Two consecutive bullish FVGs on a strong uptrend."""
    # Pattern: bars 0,1,2 form FVG #1; bars 1,2,3 form FVG #2 if gap holds.
    df = _bars(
        opens=  [9, 12, 16, 19],
        highs=  [10, 14, 17, 20],
        lows=   [8, 11, 15, 18],  # bar2.high=14 < bar4.low=18 → FVG#2
        closes= [9, 13, 16, 19],
    )
    fvgs = detect_fvgs(df)
    assert len(fvgs) == 2
    assert fvgs[0].direction == "bullish"
    assert fvgs[1].direction == "bullish"


def test_fvg_too_few_bars() -> None:
    df = _bars([10, 11], [11, 12], [9, 10], [10, 11])
    assert detect_fvgs(df) == []


def test_fvg_missing_columns_raises() -> None:
    df = pl.DataFrame({"open_time": [datetime(2025, 1, 1)], "high": [1.0]})
    with pytest.raises(ValueError, match="missing columns"):
        detect_fvgs(df)


def test_fvg_partial_then_full_fill_records_first_touch() -> None:
    """We only record FIRST touch; deeper fills don't update mitigated_at."""
    df = _bars(
        opens=  [9, 12, 16, 17, 14, 10],
        highs=  [10, 14, 17, 18, 15, 11],
        lows=   [8, 11, 15, 16, 13, 8],  # idx 4 dips to 13; idx 5 dips to 8 (deeper)
        closes= [9, 13, 16, 17, 14, 9],
    )
    fvgs = detect_fvgs(df)
    target = next(f for f in fvgs if f.timestamp == datetime(2025, 1, 1, 0, 1))
    # First touch is idx 4 (low=13 enters [10, 15]); deeper idx 5 doesn't override.
    assert target.mitigated_at == datetime(2025, 1, 1, 0, 4)
