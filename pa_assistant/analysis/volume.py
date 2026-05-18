"""Volume-based analytics: per-bar delta, cumulative volume delta, VWAP.

What "delta" means here
-----------------------

Binance reports per-bar **taker buy volume** — the volume executed by
aggressive buyers (market orders hitting the ask). Taker sells = volume
minus taker buys. The bar's *delta* is the net of the two:

    delta_base = taker_buy_base - taker_sell_base
              = taker_buy_base - (volume - taker_buy_base)
              = 2 * taker_buy_base - volume

A positive delta means more aggressive buying than selling within that bar.

CVD (Cumulative Volume Delta) is the running sum of per-bar delta. Divergence
between CVD and price is the bread-and-butter of VSA / order-flow analysis:
price making new highs while CVD fails to follow signals weakening demand.

VWAP
----

We compute VWAP using **actual aggregated quote volume** (Binance reports
``quote_volume`` per bar based on real per-trade prices), which is more
accurate than the typical_price x volume approximation:

    VWAP[i] = cumsum(quote_volume) / cumsum(volume)

Standard-deviation bands fall back to the typical-price approximation
(we lack per-trade prices needed for the exact formula):

    sigma^2[i] = cumsum(typical^2 x volume) / cumsum(volume) - vwap^2

where ``typical = (high + low + close) / 3``.

Anchored VWAP simply restarts the cumulative sums at a chosen anchor
timestamp.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

import polars as pl

# Z-scores for the default sigma band set; can be overridden per-call.
DEFAULT_BANDS: Final[tuple[float, ...]] = (1.0, 2.0)


# ---------------------------------------------------------------------------
# Per-bar delta + cumulative delta (CVD)
# ---------------------------------------------------------------------------


def compute_delta(df: pl.DataFrame) -> pl.DataFrame:
    """Add ``delta`` and ``cvd`` columns (base-asset units).

    Parameters
    ----------
    df:
        Must contain ``volume`` and ``taker_buy_base`` (Float64). Sorted
        ascending by ``open_time``; result CVD assumes that ordering.

    Returns
    -------
    DataFrame with two new columns:

    * ``delta`` — per-bar net taker buy volume in base asset (BTC for BTCUSDT)
    * ``cvd``   — cumulative sum of ``delta`` from the first row
    """
    required = {"volume", "taker_buy_base"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"compute_delta: missing columns {missing}")

    return df.with_columns(
        (2.0 * pl.col("taker_buy_base") - pl.col("volume")).alias("delta"),
    ).with_columns(
        pl.col("delta").cum_sum().alias("cvd"),
    )


# ---------------------------------------------------------------------------
# VWAP + sigma bands
# ---------------------------------------------------------------------------


def compute_vwap(
    df: pl.DataFrame,
    *,
    anchor_at: datetime | None = None,
    bands: tuple[float, ...] = DEFAULT_BANDS,
) -> pl.DataFrame:
    """Compute VWAP and sigma bands.

    Parameters
    ----------
    df:
        Must contain ``open_time``, ``high``, ``low``, ``close``, ``volume``,
        ``quote_volume``. Sorted ascending by ``open_time``.
    anchor_at:
        If given, the cumulative sums restart from the first bar whose
        ``open_time >= anchor_at`` (rows before the anchor are dropped).
        ``None`` means anchor at the first bar.
    bands:
        Z-scores for sigma bands. ``(1.0, 2.0)`` produces ``vwap_upper_1``,
        ``vwap_lower_1``, ``vwap_upper_2``, ``vwap_lower_2`` columns.

    Returns
    -------
    DataFrame (subset of ``df`` from the anchor onward) with these added
    columns: ``vwap``, then for each ``b`` in ``bands`` a pair
    ``vwap_upper_{b}`` / ``vwap_lower_{b}``. Numeric labels are formatted
    so 1.0 → "1", 1.5 → "1_5".
    """
    required = {
        "open_time",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"compute_vwap: missing columns {missing}")

    if any(b <= 0 for b in bands):
        raise ValueError(f"band z-scores must be positive, got {bands}")

    if anchor_at is not None:
        df = df.filter(pl.col("open_time") >= anchor_at)
    if df.is_empty():
        return df

    # Per-bar typical price (HLC/3) — used for variance approximation only.
    typical = (pl.col("high") + pl.col("low") + pl.col("close")) / 3.0

    # Build the running aggregates first, then derive vwap and stdev.
    out = df.with_columns(typical.alias("_typical")).with_columns(
        pl.col("quote_volume").cum_sum().alias("_cum_qv"),
        pl.col("volume").cum_sum().alias("_cum_v"),
        (pl.col("_typical").pow(2) * pl.col("volume")).cum_sum().alias("_cum_p2v"),
    ).with_columns(
        (pl.col("_cum_qv") / pl.col("_cum_v")).alias("vwap"),
    ).with_columns(
        # sigma^2 = E[p^2 | weighted by v] - VWAP^2
        # Clamp at 0 to defend against floating-point negative variance.
        (
            pl.col("_cum_p2v") / pl.col("_cum_v") - pl.col("vwap").pow(2)
        )
        .clip(lower_bound=0.0)
        .sqrt()
        .alias("_stdev"),
    )

    for b in bands:
        suffix = _band_suffix(b)
        out = out.with_columns(
            (pl.col("vwap") + b * pl.col("_stdev")).alias(f"vwap_upper_{suffix}"),
            (pl.col("vwap") - b * pl.col("_stdev")).alias(f"vwap_lower_{suffix}"),
        )

    return out.drop("_typical", "_cum_qv", "_cum_v", "_cum_p2v", "_stdev")


def _band_suffix(b: float) -> str:
    """Format a band z-score as a column-suffix-safe string.

    1.0 → '1', 0.5 → '0_5', 2.5 → '2_5'.
    """
    if b == int(b):
        return str(int(b))
    return str(b).replace(".", "_")
