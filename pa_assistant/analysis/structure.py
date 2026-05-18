"""Market structure: swing detection + BOS / CHoCH.

Two responsibilities, kept separate:

1. :func:`detect_swings` — fractal-based swing high / swing low markers.
   Operates per-bar via Polars rolling windows. **Pure** (no events, no state).

2. :func:`detect_structure_events` — walks the bar sequence, tracking the
   most recent confirmed swing levels and the current trend; emits
   :class:`StructureEvent` records when price closes through a swing level.

Definitions
-----------

A bar at index ``i`` is a **swing high** with parameter ``lookback=N`` iff:

    high[i] >= max(high[i-N : i])  AND  high[i] > max(high[i+1 : i+N+1])

(strict on the right so plateaus are detected at their first occurrence).

A **swing low** is the mirror with ``low``.

Confirmation requires ``N`` future bars, so the rightmost ``N`` bars of a
DataFrame are *unconfirmed* and never marked.

BOS vs CHoCH
------------

We track the current trend as a state machine. After a fresh swing high
``H`` and the prior swing low ``L`` are both known:

* When the close of any subsequent bar > ``H``:
    * if trend was *up*  → **BOS_up**   (continuation)
    * if trend was *down* or *none* → **CHoCH_up** (reversal / breakout)
* When the close of any subsequent bar < ``L``:
    * if trend was *down* → **BOS_down**
    * if trend was *up* or *none*   → **CHoCH_down**

Each break event "consumes" the broken level (we won't emit again for the
same level) and rotates the trend state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import polars as pl

EventType = Literal["BOS_up", "BOS_down", "CHoCH_up", "CHoCH_down"]
Trend = Literal["up", "down", "none"]


@dataclass(frozen=True, slots=True)
class StructureEvent:
    """A market-structure break event.

    Attributes
    ----------
    timestamp:
        Open time of the bar whose close triggered the break.
    event_type:
        One of ``BOS_up``, ``BOS_down``, ``CHoCH_up``, ``CHoCH_down``.
    level:
        The swing level (price) that was broken.
    trend_before / trend_after:
        Trend state surrounding the event.
    """

    timestamp: datetime
    event_type: EventType
    level: float
    trend_before: Trend
    trend_after: Trend


# ---------------------------------------------------------------------------
# Swing detection
# ---------------------------------------------------------------------------


def detect_swings(df: pl.DataFrame, lookback: int = 2) -> pl.DataFrame:
    """Mark swing high / swing low bars.

    Adds two nullable Float64 columns:

    * ``swing_high`` — the high price if this bar is a confirmed swing high,
      else null
    * ``swing_low``  — the low price if this bar is a confirmed swing low,
      else null

    Definition (strict on both sides, the conservative fractal rule):

        swing_high(i) iff  high[i] > max(high[i-N..i-1])
                       AND high[i] > max(high[i+1..i+N])

    Strict-on-both-sides means plateaus (equal highs) produce no swing — a
    safer default than ambiguous "first peak" / "last peak" handling.

    Parameters
    ----------
    df:
        Must contain ``high``, ``low``. Sorted ascending by ``open_time``;
        other ordering is undefined.
    lookback:
        Number of bars on each side. Default 2 (William's classic 5-bar
        fractal). Higher values produce fewer, more significant swings.

    Returns
    -------
    A copy of ``df`` with the two columns appended. The first/last
    ``lookback`` bars are always null on both swing columns (insufficient
    neighbours).
    """
    if lookback < 1:
        raise ValueError(f"lookback must be >= 1, got {lookback}")
    if df.is_empty():
        return df.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("swing_high"),
            pl.lit(None, dtype=pl.Float64).alias("swing_low"),
        )

    n = lookback

    # Max/min of the N bars to the LEFT (exclusive of current).
    prev_max_high = pl.col("high").shift(1).rolling_max(window_size=n)
    prev_min_low = pl.col("low").shift(1).rolling_min(window_size=n)

    # Max/min of the N bars to the RIGHT (exclusive of current).
    # max_horizontal works element-wise across the supplied expressions.
    next_max_high = pl.max_horizontal(
        *(pl.col("high").shift(-i) for i in range(1, n + 1))
    )
    next_min_low = pl.min_horizontal(
        *(pl.col("low").shift(-i) for i in range(1, n + 1))
    )

    return df.with_columns(
        pl.when(
            prev_max_high.is_not_null()
            & next_max_high.is_not_null()
            & (pl.col("high") > prev_max_high)
            & (pl.col("high") > next_max_high)
        )
        .then(pl.col("high"))
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .alias("swing_high"),
        pl.when(
            prev_min_low.is_not_null()
            & next_min_low.is_not_null()
            & (pl.col("low") < prev_min_low)
            & (pl.col("low") < next_min_low)
        )
        .then(pl.col("low"))
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .alias("swing_low"),
    )


# ---------------------------------------------------------------------------
# Structure event detection
# ---------------------------------------------------------------------------


def detect_structure_events(df: pl.DataFrame) -> list[StructureEvent]:
    """Walk the bars, emit BOS / CHoCH events in chronological order.

    Expects the DataFrame to already contain ``swing_high`` and ``swing_low``
    columns from :func:`detect_swings`. Bars are processed in order; for each
    bar we:

    1. If a new swing high / swing low is confirmed at this bar, register it
       as the *most recent* swing level for break checks.
    2. If close > most recent unbroken swing high → emit break event
       (BOS_up if trend was up, else CHoCH_up). Mark that level consumed.
    3. Symmetric for swing low.

    Notes
    -----
    Price-based breaks use *close*, not high/low intra-bar wicks. This is
    the conservative ICT/SMC convention and avoids false breaks from
    momentary spikes (some traditions use HL2 / high; we picked close
    because it survives the typical stop-hunt wick).
    """
    required = {"open_time", "high", "low", "close", "swing_high", "swing_low"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"detect_structure_events: missing columns {missing}")

    if df.is_empty():
        return []

    events: list[StructureEvent] = []
    trend: Trend = "none"
    pending_high: float | None = None  # most recent unbroken swing high
    pending_low: float | None = None

    rows = df.select(
        ["open_time", "close", "swing_high", "swing_low"]
    ).iter_rows(named=True)

    for row in rows:
        ts: datetime = row["open_time"]
        close = float(row["close"])
        sh = row["swing_high"]
        sl = row["swing_low"]

        # 1. Check for upward break of the *existing* pending high. We do
        # this BEFORE registering this bar's own swing so that a bar which
        # both breaks the previous level AND forms a new swing emits the
        # break event for the old level (and then becomes the new pending).
        if pending_high is not None and close > pending_high:
            kind: EventType = "BOS_up" if trend == "up" else "CHoCH_up"
            new_trend: Trend = "up"
            events.append(
                StructureEvent(
                    timestamp=ts,
                    event_type=kind,
                    level=pending_high,
                    trend_before=trend,
                    trend_after=new_trend,
                )
            )
            trend = new_trend
            pending_high = None  # consumed
        elif pending_low is not None and close < pending_low:
            kind = "BOS_down" if trend == "down" else "CHoCH_down"
            new_trend = "down"
            events.append(
                StructureEvent(
                    timestamp=ts,
                    event_type=kind,
                    level=pending_low,
                    trend_before=trend,
                    trend_after=new_trend,
                )
            )
            trend = new_trend
            pending_low = None

        # 2. Register newly-confirmed swings AFTER the break check so we
        # don't shadow a level the very same bar was supposed to break.
        if sh is not None:
            pending_high = float(sh)
        if sl is not None:
            pending_low = float(sl)

    return events
