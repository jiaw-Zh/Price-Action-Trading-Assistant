"""Tests for Volume Profile (POC / VAH / VAL)."""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

from pa_assistant.analysis.profile import compute_volume_profile


def _bars(
    highs: list[float],
    lows: list[float],
    volumes: list[float],
) -> pl.DataFrame:
    n = len(highs)
    base = datetime(2025, 1, 1)
    return pl.DataFrame(
        {
            "open_time": [base + timedelta(minutes=i) for i in range(n)],
            "high": highs,
            "low": lows,
            "volume": volumes,
        }
    )


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


def test_profile_emits_correct_n_bins() -> None:
    df = _bars([110.0] * 5, [90.0] * 5, [10.0] * 5)
    p = compute_volume_profile(df, n_bins=20)
    assert p.bins.height == 20
    assert "price_low" in p.bins.columns
    assert "price_high" in p.bins.columns
    assert "price_mid" in p.bins.columns
    assert "volume" in p.bins.columns


def test_profile_bin_width_correct() -> None:
    df = _bars([110.0], [90.0], [10.0])
    p = compute_volume_profile(df, n_bins=10)
    # range = 90..110, width = 2 per bin
    assert p.bin_width == pytest.approx(2.0)


def test_profile_total_volume_equals_input() -> None:
    df = _bars(
        highs=[110.0, 105.0, 100.0],
        lows=[100.0, 95.0, 90.0],
        volumes=[10.0, 20.0, 30.0],
    )
    p = compute_volume_profile(df, n_bins=20)
    assert p.total_volume == pytest.approx(60.0, rel=1e-9)


# ---------------------------------------------------------------------------
# POC / Value Area
# ---------------------------------------------------------------------------


def test_profile_poc_is_highest_volume_bin() -> None:
    """A spike of volume at one price → POC sits there."""
    # Three bars, all narrow ranges around different prices.
    # The middle one has 10x the volume.
    df = _bars(
        highs=[100.5, 105.5, 110.5],
        lows=[99.5, 104.5, 109.5],
        volumes=[1.0, 100.0, 1.0],
    )
    p = compute_volume_profile(df, n_bins=50)
    # Range = 99.5..110.5, the spike is at 105 → POC should be very close.
    assert abs(p.poc - 105.0) < 1.0


def test_profile_value_area_contains_at_least_target_pct() -> None:
    """The value area volume should be >= value_area_pct * total."""
    df = _bars(
        highs=[120.0, 110.0, 100.0, 90.0, 80.0],
        lows=[110.0, 100.0, 90.0, 80.0, 70.0],
        volumes=[5.0, 20.0, 50.0, 20.0, 5.0],
    )
    p = compute_volume_profile(df, n_bins=50, value_area_pct=0.7)
    assert p.value_area_volume >= 0.7 * p.total_volume


def test_profile_value_area_brackets_poc() -> None:
    """VAH > POC > VAL must hold (unless degenerate)."""
    df = _bars(
        highs=[110.0, 105.0, 100.0],
        lows=[100.0, 95.0, 90.0],
        volumes=[10.0, 50.0, 10.0],
    )
    p = compute_volume_profile(df, n_bins=50)
    assert p.val <= p.poc <= p.vah


# ---------------------------------------------------------------------------
# Volume distribution
# ---------------------------------------------------------------------------


def test_profile_uniform_distribution_across_range() -> None:
    """A single wide bar's volume should distribute uniformly across its bins."""
    df = _bars(highs=[100.0], lows=[0.0], volumes=[100.0])
    p = compute_volume_profile(df, n_bins=10)
    # 10 bins each 10 wide. Uniform → 10.0 each.
    vols = p.bins.get_column("volume").to_list()
    for v in vols:
        assert v == pytest.approx(10.0, rel=1e-9)


def test_profile_zero_range_bar_dumps_into_one_bin() -> None:
    """A bar with high == low should put all its volume into one bin."""
    df = _bars(
        highs=[100.0, 50.0, 100.0],  # narrow top, point at 50, narrow top
        lows=[100.0, 50.0, 100.0],
        volumes=[10.0, 10.0, 10.0],
    )
    p = compute_volume_profile(df, n_bins=50)
    assert p.total_volume == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_profile_all_bars_same_price_collapses() -> None:
    df = _bars(highs=[100.0] * 5, lows=[100.0] * 5, volumes=[10.0] * 5)
    p = compute_volume_profile(df)
    assert p.bins.height == 1
    assert p.poc == 100.0
    assert p.vah == 100.0
    assert p.val == 100.0
    assert p.total_volume == 50.0


def test_profile_zero_volume_input_safe() -> None:
    """All bars zero volume — should not raise, returns empty profile."""
    df = _bars(
        highs=[110.0, 105.0],
        lows=[100.0, 95.0],
        volumes=[0.0, 0.0],
    )
    p = compute_volume_profile(df, n_bins=10)
    assert p.total_volume == 0.0
    assert p.value_area_volume == 0.0


def test_profile_empty_raises() -> None:
    df = pl.DataFrame(
        schema={"high": pl.Float64, "low": pl.Float64, "volume": pl.Float64}
    )
    with pytest.raises(ValueError, match="empty"):
        compute_volume_profile(df)


def test_profile_invalid_value_area_pct() -> None:
    df = _bars([110.0], [100.0], [10.0])
    with pytest.raises(ValueError, match="value_area_pct"):
        compute_volume_profile(df, value_area_pct=0.0)
    with pytest.raises(ValueError, match="value_area_pct"):
        compute_volume_profile(df, value_area_pct=1.5)


def test_profile_invalid_n_bins() -> None:
    df = _bars([110.0], [100.0], [10.0])
    with pytest.raises(ValueError, match="n_bins"):
        compute_volume_profile(df, n_bins=0)


def test_profile_missing_columns_raises() -> None:
    df = pl.DataFrame({"open_time": [datetime(2025, 1, 1)], "high": [100.0]})
    with pytest.raises(ValueError, match="missing columns"):
        compute_volume_profile(df)


def test_profile_value_area_full_pct_covers_all() -> None:
    """value_area_pct=1.0 should expand to cover all bins with volume."""
    df = _bars(
        highs=[110.0, 100.0, 90.0],
        lows=[100.0, 90.0, 80.0],
        volumes=[10.0, 10.0, 10.0],
    )
    p = compute_volume_profile(df, n_bins=30, value_area_pct=1.0)
    assert p.value_area_volume == pytest.approx(p.total_volume, rel=1e-9)
