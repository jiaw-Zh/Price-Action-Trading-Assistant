"""Tests for delta / CVD / VWAP."""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

from pa_assistant.analysis.volume import compute_delta, compute_vwap


def _bars(
    n: int,
    *,
    price: float = 100.0,
    volumes: list[float] | None = None,
    taker_buy_bases: list[float] | None = None,
) -> pl.DataFrame:
    base = datetime(2025, 1, 1)
    if volumes is None:
        volumes = [10.0] * n
    if taker_buy_bases is None:
        taker_buy_bases = [v / 2 for v in volumes]
    rows = []
    for i in range(n):
        rows.append(
            {
                "open_time": base + timedelta(minutes=i),
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price,
                "volume": volumes[i],
                "quote_volume": volumes[i] * price,
                "taker_buy_base": taker_buy_bases[i],
            }
        )
    return pl.DataFrame(rows)


# ---------------------------------------------------------------------------
# compute_delta
# ---------------------------------------------------------------------------


def test_delta_balanced_is_zero() -> None:
    """taker_buy = volume/2 → equal buy/sell aggression → delta = 0."""
    df = _bars(5, volumes=[10.0] * 5, taker_buy_bases=[5.0] * 5)
    out = compute_delta(df)
    assert out.get_column("delta").to_list() == [0.0] * 5
    assert out.get_column("cvd").to_list() == [0.0] * 5


def test_delta_all_buys_positive() -> None:
    """All volume is taker buy → delta = +volume."""
    df = _bars(3, volumes=[10.0, 20.0, 5.0], taker_buy_bases=[10.0, 20.0, 5.0])
    out = compute_delta(df)
    assert out.get_column("delta").to_list() == [10.0, 20.0, 5.0]
    assert out.get_column("cvd").to_list() == [10.0, 30.0, 35.0]


def test_delta_all_sells_negative() -> None:
    df = _bars(3, volumes=[10.0, 20.0, 5.0], taker_buy_bases=[0.0, 0.0, 0.0])
    out = compute_delta(df)
    assert out.get_column("delta").to_list() == [-10.0, -20.0, -5.0]
    assert out.get_column("cvd").to_list() == [-10.0, -30.0, -35.0]


def test_delta_mixed() -> None:
    df = _bars(3, volumes=[10.0, 10.0, 10.0], taker_buy_bases=[7.0, 3.0, 5.0])
    out = compute_delta(df)
    # 2*7-10=4, 2*3-10=-4, 2*5-10=0
    assert out.get_column("delta").to_list() == [4.0, -4.0, 0.0]
    assert out.get_column("cvd").to_list() == [4.0, 0.0, 0.0]


def test_delta_missing_columns_raises() -> None:
    df = pl.DataFrame({"open_time": [datetime(2025, 1, 1)], "volume": [1.0]})
    with pytest.raises(ValueError, match="missing columns"):
        compute_delta(df)


def test_delta_zero_volume() -> None:
    df = _bars(2, volumes=[0.0, 0.0], taker_buy_bases=[0.0, 0.0])
    out = compute_delta(df)
    assert out.get_column("delta").to_list() == [0.0, 0.0]


# ---------------------------------------------------------------------------
# compute_vwap
# ---------------------------------------------------------------------------


def test_vwap_constant_price_collapses_to_price() -> None:
    """When all bars trade at the same typical price, VWAP == that price
    and sigma bands collapse onto VWAP."""
    df = _bars(5)  # all price = 100, high=101, low=99, close=100 → typical = 100
    out = compute_vwap(df)
    vwap = out.get_column("vwap").to_list()
    assert vwap == [100.0] * 5
    # sigma bands all equal vwap (zero variance)
    assert out.get_column("vwap_upper_1").to_list() == [100.0] * 5
    assert out.get_column("vwap_lower_2").to_list() == [100.0] * 5


def test_vwap_volume_weighting() -> None:
    """A high-volume bar pulls VWAP toward its price."""
    base = datetime(2025, 1, 1)
    df = pl.DataFrame(
        {
            "open_time": [base, base + timedelta(minutes=1)],
            "open": [100.0, 200.0],
            "high": [100.0, 200.0],
            "low": [100.0, 200.0],
            "close": [100.0, 200.0],
            "volume": [1.0, 9.0],
            "quote_volume": [100.0 * 1.0, 200.0 * 9.0],
            "taker_buy_base": [0.5, 4.5],
        }
    )
    out = compute_vwap(df)
    # vwap[0] = 100/1 = 100
    # vwap[1] = (100 + 1800) / (1 + 9) = 190
    assert out.get_column("vwap").to_list() == pytest.approx([100.0, 190.0])


def test_vwap_anchor_at_filters_earlier_bars() -> None:
    """anchor_at drops rows before it and restarts the cumulative sum."""
    df = _bars(5)
    anchor = datetime(2025, 1, 1, 0, 2)  # third bar onward
    out = compute_vwap(df, anchor_at=anchor)
    assert out.height == 3
    times = out.get_column("open_time").to_list()
    assert times[0] == anchor


def test_vwap_anchor_after_all_data_returns_empty() -> None:
    df = _bars(3)
    far_future = datetime(2030, 1, 1)
    out = compute_vwap(df, anchor_at=far_future)
    assert out.is_empty()


def test_vwap_custom_bands_named_correctly() -> None:
    df = _bars(3)
    out = compute_vwap(df, bands=(0.5, 1.5, 3.0))
    cols = out.columns
    for name in (
        "vwap_upper_0_5", "vwap_lower_0_5",
        "vwap_upper_1_5", "vwap_lower_1_5",
        "vwap_upper_3", "vwap_lower_3",
    ):
        assert name in cols, f"missing column {name}"


def test_vwap_negative_band_raises() -> None:
    df = _bars(3)
    with pytest.raises(ValueError, match="must be positive"):
        compute_vwap(df, bands=(0.0, 1.0))


def test_vwap_missing_columns_raises() -> None:
    df = pl.DataFrame({"open_time": [datetime(2025, 1, 1)]})
    with pytest.raises(ValueError, match="missing columns"):
        compute_vwap(df)


def test_vwap_bands_widen_with_price_dispersion() -> None:
    """A high-volume outlier should push the sigma bands wider."""
    base = datetime(2025, 1, 1)
    df = pl.DataFrame(
        {
            "open_time": [base + timedelta(minutes=i) for i in range(3)],
            "open": [100.0, 100.0, 200.0],
            "high": [100.0, 100.0, 200.0],
            "low": [100.0, 100.0, 200.0],
            "close": [100.0, 100.0, 200.0],
            "volume": [1.0, 1.0, 1.0],
            "quote_volume": [100.0, 100.0, 200.0],
            "taker_buy_base": [0.5, 0.5, 0.5],
        }
    )
    out = compute_vwap(df)
    # Last bar's sigma band should be strictly wider than its VWAP.
    last = out.row(2, named=True)
    assert last["vwap_upper_1"] > last["vwap"]
    assert last["vwap_lower_1"] < last["vwap"]
    # And 2sigma wider than 1sigma.
    assert last["vwap_upper_2"] > last["vwap_upper_1"]
    assert last["vwap_lower_2"] < last["vwap_lower_1"]
