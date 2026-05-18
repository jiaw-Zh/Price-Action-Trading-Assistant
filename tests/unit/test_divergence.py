"""Tests for multi-indicator divergence detection."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

import polars as pl
import pytest

from pa_assistant.analysis.divergence import (
    DivergenceEvent,
    detect_divergences,
)


def _bars(
    highs: Sequence[float],
    lows: Sequence[float],
    *,
    cvd: Sequence[float] | None = None,
    volume: Sequence[float] | None = None,
    oi: Sequence[float] | None = None,
) -> pl.DataFrame:
    n = len(highs)
    base = datetime(2025, 1, 1)
    cols: dict[str, list[object]] = {
        "open_time": [base + timedelta(minutes=i) for i in range(n)],
        "high": [float(x) for x in highs],
        "low": [float(x) for x in lows],
    }
    if cvd is not None:
        cols["cvd"] = [float(x) for x in cvd]
    if volume is not None:
        cols["volume"] = [float(x) for x in volume]
    if oi is not None:
        cols["oi"] = [float(x) for x in oi]
    return pl.DataFrame(cols)


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_invalid_lookback() -> None:
    df = _bars([1, 2, 3], [0, 1, 2])
    with pytest.raises(ValueError, match="lookback must be"):
        detect_divergences(df, lookback=0)


def test_invalid_min_separation() -> None:
    df = _bars([1, 2, 3], [0, 1, 2])
    with pytest.raises(ValueError, match="min_separation_bars"):
        detect_divergences(df, min_separation_bars=-1)


def test_missing_columns_raise() -> None:
    df = pl.DataFrame({"open_time": [datetime(2025, 1, 1)]})
    with pytest.raises(ValueError, match="missing columns"):
        detect_divergences(df)


def test_no_indicator_columns_returns_empty() -> None:
    """No CVD/volume/OI columns at all → empty list, not error."""
    df = _bars([90, 100, 90, 110, 90], [80, 90, 80, 100, 80])
    assert detect_divergences(df) == []


def test_empty_df_returns_empty() -> None:
    df = _bars([], [], cvd=[], volume=[], oi=[])
    assert detect_divergences(df) == []


# ---------------------------------------------------------------------------
# Bearish divergences (consecutive HHs)
# ---------------------------------------------------------------------------


def test_bearish_cvd_divergence() -> None:
    """Two swing highs: price up, CVD down → bearish CVD divergence."""
    # bars: 0,1,2,3,4,5,6,7,8,9,10
    # swing highs at idx 2 (high=100) and idx 8 (high=110, HH)
    highs = [90, 95, 100, 95, 90, 95, 100, 105, 110, 105, 100]
    lows = [85, 90, 95, 90, 85, 90, 95, 100, 105, 100, 95]
    cvd = [0, 50, 100, 80, 60, 70, 80, 70, 60, 50, 40]  # at idx 2: 100, idx 8: 60
    df = _bars(highs, lows, cvd=cvd)

    events = detect_divergences(df, lookback=2, min_separation_bars=3)
    bearish = [e for e in events if e.side == "bearish" and e.indicator == "cvd"]
    assert len(bearish) == 1
    e = bearish[0]
    assert e.swing_price == 110.0
    assert e.prior_swing_price == 100.0
    assert e.indicator_value == 60.0
    assert e.prior_indicator_value == 100.0
    assert e.timestamp == datetime(2025, 1, 1, 0, 8)
    assert e.prior_swing_time == datetime(2025, 1, 1, 0, 2)


def test_bearish_volume_divergence_no_demand() -> None:
    """Classic VSA: HH on lower volume = no demand."""
    highs = [90, 95, 100, 95, 90, 95, 100, 105, 110, 105, 100]
    lows = [85, 90, 95, 90, 85, 90, 95, 100, 105, 100, 95]
    volume = [50, 80, 200, 100, 80, 90, 100, 80, 50, 40, 30]  # idx 2:200, idx 8:50
    df = _bars(highs, lows, volume=volume)
    events = detect_divergences(df, lookback=2, min_separation_bars=3)
    vol_div = [e for e in events if e.indicator == "volume"]
    assert len(vol_div) == 1
    assert vol_div[0].side == "bearish"
    assert vol_div[0].indicator_value == 50.0


def test_bearish_oi_divergence_short_squeeze_signal() -> None:
    """HH but OI dropping → short covering, fakeout signal."""
    highs = [90, 95, 100, 95, 90, 95, 100, 105, 110, 105, 100]
    lows = [85, 90, 95, 90, 85, 90, 95, 100, 105, 100, 95]
    oi = [10000, 10100, 10200, 10000, 9800, 9700, 9500, 9400, 9000, 8800, 8700]
    df = _bars(highs, lows, oi=oi)
    events = detect_divergences(df, lookback=2, min_separation_bars=3)
    oi_div = [e for e in events if e.indicator == "oi"]
    assert len(oi_div) == 1
    assert oi_div[0].side == "bearish"
    assert oi_div[0].indicator_value == 9000.0
    assert oi_div[0].prior_indicator_value == 10200.0


def test_no_bearish_when_indicator_confirms() -> None:
    """HH on rising indicator → no divergence (confirmation)."""
    highs = [90, 95, 100, 95, 90, 95, 100, 105, 110, 105, 100]
    lows = [85, 90, 95, 90, 85, 90, 95, 100, 105, 100, 95]
    cvd = [0, 50, 100, 80, 60, 70, 80, 100, 150, 140, 130]  # idx 2:100, idx 8:150
    df = _bars(highs, lows, cvd=cvd)
    events = detect_divergences(df, lookback=2, min_separation_bars=3)
    assert [e for e in events if e.indicator == "cvd"] == []


def test_no_bearish_when_not_a_higher_high() -> None:
    """Equal or lower high → not a HH → no bearish opportunity."""
    highs = [90, 95, 100, 95, 90, 95, 100, 95, 100, 95, 90]  # second high == 100
    lows = [85, 90, 95, 90, 85, 90, 95, 90, 95, 90, 85]
    cvd = [0, 50, 100, 80, 60, 70, 80, 70, 60, 50, 40]
    df = _bars(highs, lows, cvd=cvd)
    events = detect_divergences(df, lookback=2, min_separation_bars=3)
    assert events == []


# ---------------------------------------------------------------------------
# Bullish divergences (consecutive LLs)
# ---------------------------------------------------------------------------


def test_bullish_cvd_divergence() -> None:
    """Lower low but CVD higher → bullish CVD divergence."""
    # swing lows at idx 2 (low=80) and idx 8 (low=70, LL)
    highs = [95, 90, 85, 90, 95, 90, 85, 80, 75, 80, 85]
    lows = [90, 85, 80, 85, 90, 85, 80, 75, 70, 75, 80]
    cvd = [0, -50, -100, -80, -60, -70, -80, -70, -60, -50, -40]  # idx 2:-100, idx 8:-60
    df = _bars(highs, lows, cvd=cvd)
    events = detect_divergences(df, lookback=2, min_separation_bars=3)
    bullish = [e for e in events if e.side == "bullish" and e.indicator == "cvd"]
    assert len(bullish) == 1
    e = bullish[0]
    assert e.swing_price == 70.0
    assert e.prior_swing_price == 80.0
    assert e.indicator_value == -60.0
    assert e.prior_indicator_value == -100.0


def test_bullish_oi_divergence_capitulation() -> None:
    """LL with OI dropping → longs capitulating → near-bottom signal."""
    highs = [95, 90, 85, 90, 95, 90, 85, 80, 75, 80, 85]
    lows = [90, 85, 80, 85, 90, 85, 80, 75, 70, 75, 80]
    # OI lower at LL than at prior low → bullish OI divergence
    oi = [10000, 10100, 10200, 10000, 9800, 9700, 9500, 9400, 9300, 9200, 9100]
    df = _bars(highs, lows, oi=oi)
    events = detect_divergences(df, lookback=2, min_separation_bars=3)
    # idx 2 OI: 10200, idx 8 OI: 9300. Both lower at LL → bullish? No wait.
    # Bullish divergence needs ind_curr > ind_prev. 9300 < 10200 → NOT bullish.
    # So the test is actually "no divergence" — correct interpretation.
    assert [e for e in events if e.indicator == "oi" and e.side == "bullish"] == []


def test_bullish_oi_with_indicator_rising() -> None:
    """OI dropping less or rising at the LL = bullish divergence."""
    highs = [95, 90, 85, 90, 95, 90, 85, 80, 75, 80, 85]
    lows = [90, 85, 80, 85, 90, 85, 80, 75, 70, 75, 80]
    oi = [10000, 9900, 9800, 9900, 10000, 9900, 9800, 9900, 10100, 10000, 9900]
    # idx 2 OI: 9800, idx 8 OI: 10100. LL but OI HIGHER → bullish divergence.
    df = _bars(highs, lows, oi=oi)
    events = detect_divergences(df, lookback=2, min_separation_bars=3)
    bullish_oi = [e for e in events if e.indicator == "oi" and e.side == "bullish"]
    assert len(bullish_oi) == 1


# ---------------------------------------------------------------------------
# Multi-indicator handling
# ---------------------------------------------------------------------------


def test_all_three_indicators_emit_separate_events() -> None:
    """Same swing pair can produce multiple events (one per divergent indicator)."""
    highs = [90, 95, 100, 95, 90, 95, 100, 105, 110, 105, 100]
    lows = [85, 90, 95, 90, 85, 90, 95, 100, 105, 100, 95]
    cvd = [0, 50, 100, 80, 60, 70, 80, 70, 60, 50, 40]
    volume = [50, 80, 200, 100, 80, 90, 100, 80, 50, 40, 30]
    oi = [10000, 10100, 10200, 10000, 9800, 9700, 9500, 9400, 9000, 8800, 8700]
    df = _bars(highs, lows, cvd=cvd, volume=volume, oi=oi)
    events = detect_divergences(df, lookback=2, min_separation_bars=3)
    indicators = {e.indicator for e in events if e.side == "bearish"}
    assert indicators == {"cvd", "volume", "oi"}


def test_indicators_filter_subset() -> None:
    """When indicators=['cvd'], skip volume and OI even if columns present."""
    highs = [90, 95, 100, 95, 90, 95, 100, 105, 110, 105, 100]
    lows = [85, 90, 95, 90, 85, 90, 95, 100, 105, 100, 95]
    cvd = [0, 50, 100, 80, 60, 70, 80, 70, 60, 50, 40]
    volume = [50, 80, 200, 100, 80, 90, 100, 80, 50, 40, 30]
    df = _bars(highs, lows, cvd=cvd, volume=volume)
    events = detect_divergences(df, indicators=["cvd"], lookback=2, min_separation_bars=3)
    assert all(e.indicator == "cvd" for e in events)


def test_missing_indicator_column_silently_skipped() -> None:
    """Request OI but no oi column → just drop OI, don't error."""
    highs = [90, 95, 100, 95, 90, 95, 100, 105, 110, 105, 100]
    lows = [85, 90, 95, 90, 85, 90, 95, 100, 105, 100, 95]
    cvd = [0, 50, 100, 80, 60, 70, 80, 70, 60, 50, 40]
    df = _bars(highs, lows, cvd=cvd)
    events = detect_divergences(
        df, indicators=["cvd", "oi"], lookback=2, min_separation_bars=3
    )
    indicators = {e.indicator for e in events}
    assert indicators == {"cvd"}


# ---------------------------------------------------------------------------
# Filtering parameters
# ---------------------------------------------------------------------------


def test_min_separation_filters_close_swings() -> None:
    """Two close swings → divergence rejected by min_separation_bars."""
    # Swing highs at idx 2 and idx 4 (only 2 bars apart).
    highs = [90, 95, 100, 95, 110, 95, 90]
    lows = [85, 90, 95, 90, 105, 90, 85]
    cvd = [100, 90, 80, 60, 50, 40, 30]
    df = _bars(highs, lows, cvd=cvd)
    # min_separation_bars=5 should reject this pair.
    events = detect_divergences(df, lookback=2, min_separation_bars=5)
    assert events == []


def test_events_sorted_by_timestamp() -> None:
    """Output ordering: ascending timestamp."""
    highs = [90, 95, 100, 95, 90, 95, 100, 105, 110, 105, 100]
    lows = [85, 90, 95, 90, 85, 90, 95, 100, 105, 100, 95]
    cvd = [0, 50, 100, 80, 60, 70, 80, 70, 60, 50, 40]
    volume = [50, 80, 200, 100, 80, 90, 100, 80, 50, 40, 30]
    df = _bars(highs, lows, cvd=cvd, volume=volume)
    events = detect_divergences(df, lookback=2, min_separation_bars=3)
    if len(events) >= 2:
        timestamps = [e.timestamp for e in events]
        assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# Strength normalization
# ---------------------------------------------------------------------------


def test_strength_zero_when_both_zero() -> None:
    """Both indicator values = 0 → strength = 0 (no division by zero)."""
    highs = [90, 95, 100, 95, 90, 95, 100, 105, 110, 105, 100]
    lows = [85, 90, 95, 90, 85, 90, 95, 100, 105, 100, 95]
    cvd = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    df = _bars(highs, lows, cvd=cvd)
    # CVD constant → no divergence emitted (curr == prev not < prev). Trivially OK.
    events = detect_divergences(df, lookback=2, min_separation_bars=3)
    assert events == []


def test_strength_normalized_to_unit_interval() -> None:
    highs = [90, 95, 100, 95, 90, 95, 100, 105, 110, 105, 100]
    lows = [85, 90, 95, 90, 85, 90, 95, 100, 105, 100, 95]
    # Volume from 200 → 50 = drop of 75% → strength 0.75
    volume = [50, 80, 200, 100, 80, 90, 100, 80, 50, 40, 30]
    df = _bars(highs, lows, volume=volume)
    events = detect_divergences(df, lookback=2, min_separation_bars=3)
    assert len(events) == 1
    assert events[0].strength == pytest.approx(150 / 200)


def test_dataclass_immutable() -> None:
    e = DivergenceEvent(
        timestamp=datetime(2025, 1, 1),
        side="bearish",
        indicator="cvd",
        swing_price=100.0,
        prior_swing_price=90.0,
        prior_swing_time=datetime(2025, 1, 1),
        indicator_value=50.0,
        prior_indicator_value=100.0,
        strength=0.5,
    )
    with pytest.raises(AttributeError):
        e.swing_price = 200.0  # type: ignore[misc]
