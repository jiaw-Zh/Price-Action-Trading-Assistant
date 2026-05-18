"""Tests for resampling 1m → higher timeframes."""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

from pa_assistant.analysis.resample import SUPPORTED_TIMEFRAMES, resample_ohlcv


def _make_1m(n: int, start: datetime | None = None) -> pl.DataFrame:
    """Build n synthetic 1m bars with predictable values."""
    start = start or datetime(2025, 1, 1, 0, 0, 0)
    rows = []
    for i in range(n):
        rows.append(
            {
                "open_time": start + timedelta(minutes=i),
                "open": 100.0 + i,
                "high": 101.0 + i,
                "low": 99.0 + i,
                "close": 100.5 + i,
                "volume": 10.0,
                "quote_volume": 1000.0,
                "trade_count": 5,
                "taker_buy_base": 4.0,
            }
        )
    return pl.DataFrame(rows)


def test_resample_1m_to_5m_basic() -> None:
    df = _make_1m(15)  # exactly 3 x 5m bars
    out = resample_ohlcv(df, "5m")
    assert out.height == 3
    # First 5m bar covers minutes 0-4 → open=100, high=105, low=99, close=104.5
    first = out.row(0, named=True)
    assert first["open"] == 100.0
    assert first["high"] == 105.0
    assert first["low"] == 99.0
    assert first["close"] == 104.5
    assert first["volume"] == 50.0  # 5 x 10
    assert first["trade_count"] == 25  # 5 x 5


def test_resample_aligns_to_timeframe_boundary() -> None:
    """Bars should align to wall-clock boundaries, not to the input start."""
    # Start at 00:03 — input crosses the 00:00-00:05 boundary
    df = _make_1m(10, start=datetime(2025, 1, 1, 0, 3))
    out = resample_ohlcv(df, "5m")
    # Expect bars at 00:00, 00:05, 00:10
    times = out.get_column("open_time").to_list()
    assert times[0] == datetime(2025, 1, 1, 0, 0)
    assert times[1] == datetime(2025, 1, 1, 0, 5)


def test_resample_to_1m_is_noop_but_sorts() -> None:
    df = _make_1m(5)
    shuffled = df.sample(fraction=1.0, shuffle=True, seed=42)
    out = resample_ohlcv(shuffled, "1m")
    assert out.height == 5
    # Should be sorted by open_time
    times = out.get_column("open_time").to_list()
    assert times == sorted(times)


def test_resample_partial_last_bar() -> None:
    """A 5m bar with only 3 minutes of data should still emit (partial bar)."""
    df = _make_1m(8)  # 5 + 3 = 1 full + 1 partial 5m bar
    out = resample_ohlcv(df, "5m")
    assert out.height == 2
    second = out.row(1, named=True)
    # Second bar covers minutes 5-7 (3 bars)
    assert second["open"] == 105.0
    assert second["volume"] == 30.0  # 3 x 10


def test_resample_empty_returns_empty() -> None:
    df = pl.DataFrame(
        schema={
            "open_time": pl.Datetime,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
        }
    )
    out = resample_ohlcv(df, "15m")
    assert out.height == 0


def test_resample_unsupported_timeframe_raises() -> None:
    df = _make_1m(5)
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        resample_ohlcv(df, "7m")


def test_resample_only_required_columns() -> None:
    """If optional columns are absent, resample only what's there."""
    df = pl.DataFrame(
        {
            "open_time": [
                datetime(2025, 1, 1, 0, 0),
                datetime(2025, 1, 1, 0, 1),
                datetime(2025, 1, 1, 0, 2),
            ],
            "open": [100.0, 101.0, 102.0],
            "high": [102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, 102.0, 103.0],
            "volume": [10.0, 11.0, 12.0],
        }
    )
    out = resample_ohlcv(df, "5m")
    assert out.height == 1
    assert "trade_count" not in out.columns  # absent in input
    row = out.row(0, named=True)
    assert row["open"] == 100.0
    assert row["high"] == 104.0
    assert row["low"] == 99.0
    assert row["close"] == 103.0
    assert row["volume"] == 33.0


@pytest.mark.parametrize("tf", SUPPORTED_TIMEFRAMES)
def test_all_supported_timeframes_run(tf: str) -> None:
    """Smoke: every supported timeframe should produce output without error."""
    df = _make_1m(120)  # 2h of data
    out = resample_ohlcv(df, tf)
    assert out.height >= 1
