"""Supply/demand zones: Order Blocks and Fair Value Gaps.

These are the two most popular "high-probability reaction zone" concepts
in modern smart-money / ICT analysis. Both are rectangular price regions
that often act as future support/resistance once price returns.

Order Block (OB)
----------------

The **last opposite-direction candle** immediately before a structure
break (BOS or CHoCH). The theory: institutions accumulated/distributed
on this candle, then drove price the other way; future tests of the
zone often find the same liquidity flowing back.

* **Bullish OB** — last bearish (close < open) candle before a *_up
  break. Body region (open..close) is the primary zone; full wick
  (low..high) is a wider safety region.
* **Bearish OB** — last bullish (close > open) candle before a *_down
  break.

We cap the backward search at ``lookback`` bars (default 10) — if the
opposite candle is more than that far back, the OB is too stale to be
meaningful.

Fair Value Gap (FVG)
--------------------

A three-bar imbalance pattern. The middle bar is the displacement; the
gap is the unfilled price region between bar 1's wick and bar 3's wick:

* **Bullish FVG** — ``bar1.high < bar3.low``. The gap [bar1.high,
  bar3.low] is the bullish imbalance.
* **Bearish FVG** — ``bar1.low > bar3.high``. The gap [bar3.high,
  bar1.low] is the bearish imbalance.

No structure event needed — pure geometry.

Mitigation
----------

A zone is *mitigated* once any subsequent bar's wick re-enters its
range (touches the boundary). For OB, that's price returning to the
body. For FVG, it's any tick into the gap. Mitigation is recorded as
the timestamp of the first re-entering bar; ``None`` means the zone
remains untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import polars as pl

from pa_assistant.analysis.structure import StructureEvent

ZoneDirection = Literal["bullish", "bearish"]


@dataclass(frozen=True, slots=True)
class OrderBlock:
    """A bullish or bearish order block.

    Attributes
    ----------
    timestamp:
        The OB candle's ``open_time``.
    direction:
        ``"bullish"`` → demand zone (last bearish candle before *_up).
        ``"bearish"`` → supply zone (last bullish candle before *_down).
    top / bottom:
        Body region: max/min of (open, close). The "tight" zone.
    wick_top / wick_bottom:
        Full candle range: high / low. The "wide" zone for safer entries.
    triggered_by:
        Timestamp of the BOS/CHoCH event that confirmed this OB.
    mitigated_at:
        Timestamp of the first bar after the trigger whose range re-enters
        the body (top..bottom). ``None`` means still untouched.
    """

    timestamp: datetime
    direction: ZoneDirection
    top: float
    bottom: float
    wick_top: float
    wick_bottom: float
    triggered_by: datetime
    mitigated_at: datetime | None


@dataclass(frozen=True, slots=True)
class FairValueGap:
    """A three-bar imbalance / fair-value gap.

    Attributes
    ----------
    timestamp:
        ``open_time`` of the middle (displacement) bar.
    direction:
        ``"bullish"`` (gap ABOVE bar 1's high) or ``"bearish"`` (gap BELOW
        bar 1's low).
    top / bottom:
        Inclusive price bounds of the gap.
    mitigated_at:
        Timestamp of the first bar after bar 3 whose range touches the
        gap (low <= top for bullish; high >= bottom for bearish).
        ``None`` if untouched.
    """

    timestamp: datetime
    direction: ZoneDirection
    top: float
    bottom: float
    mitigated_at: datetime | None


# ---------------------------------------------------------------------------
# Order block detection
# ---------------------------------------------------------------------------


def detect_order_blocks(
    df: pl.DataFrame,
    events: list[StructureEvent],
    *,
    lookback: int = 10,
) -> list[OrderBlock]:
    """Identify Order Blocks driven by ``events`` against bars in ``df``.

    For each event:

    1. Walk backward up to ``lookback`` bars from the event bar
    2. Find the first opposite-direction candle (bearish for *_up,
       bullish for *_down)
    3. That candle's body becomes the OB
    4. Walk forward from the event bar to determine first-touch mitigation

    Parameters
    ----------
    df:
        Must contain ``open_time``, ``open``, ``high``, ``low``, ``close``;
        sorted ascending by ``open_time``.
    events:
        BOS / CHoCH events from :func:`detect_structure_events`.
    lookback:
        Maximum bars to scan back for the opposite candle. Default 10.

    Returns
    -------
    List of :class:`OrderBlock`, in chronological order of the events that
    triggered them. Events without an opposite candle in range are skipped.
    """
    if lookback < 1:
        raise ValueError(f"lookback must be >= 1, got {lookback}")

    required = {"open_time", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"detect_order_blocks: missing columns {missing}")

    if df.is_empty() or not events:
        return []

    rows: list[tuple[datetime, float, float, float, float]] = list(
        df.select(["open_time", "open", "high", "low", "close"]).iter_rows()
    )
    time_to_idx: dict[datetime, int] = {r[0]: i for i, r in enumerate(rows)}

    obs: list[OrderBlock] = []

    for event in events:
        event_idx = time_to_idx.get(event.timestamp)
        if event_idx is None or event_idx == 0:
            continue

        is_up = event.event_type.endswith("_up")
        # Walk backward looking for the opposite-direction candle.
        ob_idx: int | None = None
        start = event_idx - 1
        stop = max(-1, event_idx - lookback - 1)
        for j in range(start, stop, -1):
            _ts, o, _h, _l, c = rows[j]
            is_bearish_bar = c < o
            is_bullish_bar = c > o
            if is_up and is_bearish_bar:
                ob_idx = j
                break
            if (not is_up) and is_bullish_bar:
                ob_idx = j
                break

        if ob_idx is None:
            continue

        ob_ts, o, h, low_price, c = rows[ob_idx]
        body_top = max(o, c)
        body_bottom = min(o, c)
        ob_direction: ZoneDirection = "bullish" if is_up else "bearish"

        # Walk forward FROM AFTER the event bar to find mitigation.
        mitigated_at: datetime | None = None
        for k in range(event_idx + 1, len(rows)):
            kts, _, kh, kl, _ = rows[k]
            if ob_direction == "bullish" and kl <= body_top:
                mitigated_at = kts
                break
            if ob_direction == "bearish" and kh >= body_bottom:
                mitigated_at = kts
                break

        obs.append(
            OrderBlock(
                timestamp=ob_ts,
                direction=ob_direction,
                top=body_top,
                bottom=body_bottom,
                wick_top=h,
                wick_bottom=low_price,
                triggered_by=event.timestamp,
                mitigated_at=mitigated_at,
            )
        )

    return obs


# ---------------------------------------------------------------------------
# Fair Value Gap detection
# ---------------------------------------------------------------------------


def detect_fvgs(df: pl.DataFrame) -> list[FairValueGap]:
    """Detect 3-bar fair-value-gap patterns and their mitigation.

    Parameters
    ----------
    df:
        Must contain ``open_time``, ``high``, ``low``; sorted ascending by
        ``open_time``.

    Returns
    -------
    List of :class:`FairValueGap`, one per detected gap, in chronological
    order. ``mitigated_at`` is the timestamp of the first bar after the
    gap-forming triplet whose range touches the gap.
    """
    required = {"open_time", "high", "low"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"detect_fvgs: missing columns {missing}")

    if df.height < 3:
        return []

    rows: list[tuple[datetime, float, float]] = list(
        df.select(["open_time", "high", "low"]).iter_rows()
    )
    fvgs: list[FairValueGap] = []

    for i in range(1, len(rows) - 1):
        _ts1, h1, l1 = rows[i - 1]
        ts_mid, _h2, _l2 = rows[i]
        _ts3, h3, l3 = rows[i + 1]

        direction: ZoneDirection
        top: float
        bottom: float
        if h1 < l3:
            # Bullish FVG: gap is [bar1.high, bar3.low]
            direction = "bullish"
            top = l3
            bottom = h1
        elif l1 > h3:
            # Bearish FVG: gap is [bar3.high, bar1.low]
            direction = "bearish"
            top = l1
            bottom = h3
        else:
            continue

        # Mitigation walk starts AFTER bar 3.
        mitigated_at: datetime | None = None
        for k in range(i + 2, len(rows)):
            kts, kh, kl = rows[k]
            if direction == "bullish" and kl <= top:
                mitigated_at = kts
                break
            if direction == "bearish" and kh >= bottom:
                mitigated_at = kts
                break

        fvgs.append(
            FairValueGap(
                timestamp=ts_mid,
                direction=direction,
                top=top,
                bottom=bottom,
                mitigated_at=mitigated_at,
            )
        )

    return fvgs
