"""1m OHLCV → higher-timeframe resampling, in pure Polars.

We persist only 1m klines (see ``ARCHITECTURE.md``); every higher timeframe
is derived on demand here. Aggregation rules:

============== =====================================
column         agg
============== =====================================
open           first
high           max
low            min
close          last
volume         sum
quote_volume   sum
trade_count    sum
taker_buy_base sum
============== =====================================

Bar boundaries are aligned to the timeframe modulo (e.g. 15m bars start at
:00, :15, :30, :45). Polars ``group_by_dynamic`` handles the alignment.
"""

from __future__ import annotations

from typing import Final

import polars as pl

# Polars duration-string aliases for our supported timeframes. We keep this
# explicit (rather than passing the user string straight through) so that
# unsupported values fail loudly with a clear list.
_TIMEFRAME_TO_EVERY: Final[dict[str, str]] = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1d",
    "1w": "1w",
}

SUPPORTED_TIMEFRAMES: Final[tuple[str, ...]] = tuple(_TIMEFRAME_TO_EVERY.keys())

_OHLCV_AGGS: Final[list[pl.Expr]] = [
    pl.col("open").first().alias("open"),
    pl.col("high").max().alias("high"),
    pl.col("low").min().alias("low"),
    pl.col("close").last().alias("close"),
    pl.col("volume").sum().alias("volume"),
    pl.col("quote_volume").sum().alias("quote_volume"),
    pl.col("trade_count").sum().alias("trade_count"),
    pl.col("taker_buy_base").sum().alias("taker_buy_base"),
]


def resample_ohlcv(df: pl.DataFrame, timeframe: str) -> pl.DataFrame:
    """Resample 1m OHLCV bars to ``timeframe``.

    Parameters
    ----------
    df:
        DataFrame containing at minimum: ``open_time`` (datetime), ``open``,
        ``high``, ``low``, ``close``, ``volume``. Additional columns
        (``quote_volume``, ``trade_count``, ``taker_buy_base``) are aggregated
        if present.
    timeframe:
        One of :data:`SUPPORTED_TIMEFRAMES`.

    Returns
    -------
    DataFrame with the same column names, sorted by ``open_time`` ascending.

    Notes
    -----
    Empty input returns an empty DataFrame with the same schema. ``1m`` is a
    no-op (returns a sorted copy with consistent schema).
    """
    if timeframe not in _TIMEFRAME_TO_EVERY:
        raise ValueError(
            f"Unsupported timeframe {timeframe!r}. "
            f"Supported: {', '.join(SUPPORTED_TIMEFRAMES)}"
        )

    if df.is_empty():
        return df.sort("open_time")

    if timeframe == "1m":
        return df.sort("open_time")

    every = _TIMEFRAME_TO_EVERY[timeframe]

    # Pick only aggregations whose source columns exist.
    aggs = [agg for agg in _OHLCV_AGGS if str(agg.meta.output_name()) in df.columns]

    return (
        df.sort("open_time")
        .group_by_dynamic("open_time", every=every, label="left", closed="left")
        .agg(aggs)
        .sort("open_time")
    )
