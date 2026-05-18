"""Volume Profile: distribute traded volume across price bins.

Each bar's volume is distributed *uniformly* across the price bins that
its ``[low, high]`` range overlaps. This is the standard approach used by
TradingView and other charting platforms — it's more accurate than
"dump everything at typical price" because wide-range bars realistically
spread volume across multiple price levels.

Outputs
-------

The :class:`VolumeProfile` dataclass exposes the per-bin DataFrame plus
three derived levels:

* **POC** (Point of Control) — the bin (or rather its midpoint) that
  holds the most volume. The price level with the strongest acceptance.
* **VAH** / **VAL** (Value Area High / Low) — the price bounds of the
  contiguous bins centred on the POC that together hold ``value_area_pct``
  (default 70%) of the total volume. The classic "value area" of
  market-profile theory.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True, slots=True)
class VolumeProfile:
    """Result of :func:`compute_volume_profile`.

    Attributes
    ----------
    bins:
        DataFrame with ``price_low``, ``price_high``, ``price_mid``,
        ``volume`` columns; one row per bin, sorted ascending by price.
    poc:
        Mid-price of the bin holding the most volume.
    vah:
        Top of the value-area band (highest ``price_high`` in the band).
    val:
        Bottom of the value-area band (lowest ``price_low`` in the band).
    total_volume:
        Sum of volume across all bins.
    value_area_volume:
        Sum of volume contained in the value area.
    bin_width:
        Width of each bin in price units.
    """

    bins: pl.DataFrame
    poc: float
    vah: float
    val: float
    total_volume: float
    value_area_volume: float
    bin_width: float


def compute_volume_profile(
    df: pl.DataFrame,
    *,
    n_bins: int = 50,
    value_area_pct: float = 0.70,
) -> VolumeProfile:
    """Build a price-binned volume profile from a DataFrame of OHLCV bars.

    Parameters
    ----------
    df:
        Must contain ``high``, ``low``, ``volume``. Empty input raises.
    n_bins:
        Number of equal-width price bins between ``min(low)`` and
        ``max(high)``. Default 50.
    value_area_pct:
        Fraction of total volume defining the value area. Default 0.70.

    Returns
    -------
    :class:`VolumeProfile`. If all bars sit at a single price (high == low
    for every bar), all bins collapse to that price.

    Notes
    -----
    Volume distribution: a bar with low=L, high=H, volume=V spans the
    bins in [L, H]; each bin gets ``V * (overlap_width / (H - L))`` of
    the volume. For zero-range bars (H == L) all volume goes into the
    bin containing that price.
    """
    if df.is_empty():
        raise ValueError("compute_volume_profile: empty DataFrame")
    if not 0.0 < value_area_pct <= 1.0:
        raise ValueError(
            f"value_area_pct must be in (0, 1], got {value_area_pct}"
        )
    if n_bins < 1:
        raise ValueError(f"n_bins must be >= 1, got {n_bins}")

    required = {"high", "low", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"compute_volume_profile: missing columns {missing}")

    price_min = float(df.get_column("low").min())  # type: ignore[arg-type]
    price_max = float(df.get_column("high").max())  # type: ignore[arg-type]

    # Degenerate case: every bar at the same price.
    if price_max == price_min:
        total = float(df.get_column("volume").sum())
        bins_df = pl.DataFrame(
            {
                "price_low": [price_min],
                "price_high": [price_max],
                "price_mid": [price_min],
                "volume": [total],
            }
        )
        return VolumeProfile(
            bins=bins_df,
            poc=price_min,
            vah=price_max,
            val=price_min,
            total_volume=total,
            value_area_volume=total,
            bin_width=0.0,
        )

    bin_width = (price_max - price_min) / n_bins

    # Distribute each bar's volume across the bins it covers.
    # Implementation: iterate rows in Python — for our typical input size
    # (~10k bars x 50 bins = 500k ops) this is well under a second; a
    # vectorised version is possible but obscures the logic.
    bin_volumes = [0.0] * n_bins
    rows = df.select(["low", "high", "volume"]).iter_rows()
    for low, high, vol in rows:
        if vol <= 0:
            continue
        if high == low:
            idx = min(int((low - price_min) / bin_width), n_bins - 1)
            bin_volumes[idx] += float(vol)
            continue
        bar_range = high - low
        # Indexes of first/last overlapping bin.
        first_bin = max(0, int((low - price_min) / bin_width))
        last_bin = min(n_bins - 1, int((high - price_min) / bin_width))
        # Inclusive distribution
        for i in range(first_bin, last_bin + 1):
            bin_lo = price_min + i * bin_width
            bin_hi = bin_lo + bin_width
            overlap = min(high, bin_hi) - max(low, bin_lo)
            if overlap <= 0:
                continue
            bin_volumes[i] += float(vol) * (overlap / bar_range)

    bins_df = pl.DataFrame(
        {
            "price_low": [price_min + i * bin_width for i in range(n_bins)],
            "price_high": [price_min + (i + 1) * bin_width for i in range(n_bins)],
            "price_mid": [price_min + (i + 0.5) * bin_width for i in range(n_bins)],
            "volume": bin_volumes,
        }
    )

    total_volume = sum(bin_volumes)
    if total_volume <= 0:
        # All bars had zero volume — degenerate but valid.
        poc_idx = n_bins // 2
        return VolumeProfile(
            bins=bins_df,
            poc=float(bins_df.row(poc_idx, named=True)["price_mid"]),
            vah=price_max,
            val=price_min,
            total_volume=0.0,
            value_area_volume=0.0,
            bin_width=bin_width,
        )

    # POC = bin index with maximum volume.
    poc_idx = max(range(n_bins), key=lambda i: bin_volumes[i])
    poc = price_min + (poc_idx + 0.5) * bin_width

    # Value area: expand outward from POC bin until value_area_pct of
    # total volume is contained. Standard market-profile algorithm:
    # at each step, take the larger of the two neighbouring bins.
    target = total_volume * value_area_pct
    accumulated = bin_volumes[poc_idx]
    lo_idx = poc_idx
    hi_idx = poc_idx
    while accumulated < target and (lo_idx > 0 or hi_idx < n_bins - 1):
        below = bin_volumes[lo_idx - 1] if lo_idx > 0 else -1.0
        above = bin_volumes[hi_idx + 1] if hi_idx < n_bins - 1 else -1.0
        if below >= above and lo_idx > 0:
            lo_idx -= 1
            accumulated += bin_volumes[lo_idx]
        elif hi_idx < n_bins - 1:
            hi_idx += 1
            accumulated += bin_volumes[hi_idx]
        else:
            break

    val = price_min + lo_idx * bin_width
    vah = price_min + (hi_idx + 1) * bin_width

    return VolumeProfile(
        bins=bins_df,
        poc=poc,
        vah=vah,
        val=val,
        total_volume=total_volume,
        value_area_volume=accumulated,
        bin_width=bin_width,
    )
