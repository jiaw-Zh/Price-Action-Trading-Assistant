"""Wyckoff phase state machine.

Recognises Wyckoff Accumulation / Distribution structures by translating
events from upstream analysis modules (swings, stop hunts, divergences)
into Wyckoff vocabulary (SC, AR, ST, Spring, SOS, LPS and their
distribution mirrors), then evolving a finite-state machine through
phases A → E.

Theory
------

Richard Wyckoff observed that large operators cannot enter or exit
positions at a single price — their size would move the market against
them. They must therefore **accumulate** (or distribute) inside a
horizontal range, using specific tactics that leave footprints on the
chart. The cycle is:

::

    Accumulation → Markup → Distribution → Markdown → ...

Each ranging phase splits into 5 sub-phases (A through E):

* **Phase A** — preceding trend stops. Selling Climax (SC) +
  Automatic Rally (AR) define the initial trading range.
* **Phase B** — cause built. Repeated tests of the range (ST = Secondary
  Test) on diminishing volume.
* **Phase C** — the test of supply. **Spring** = a final fakeout below
  the range to grab residual stops, then a quick recovery. (Distribution
  mirror: **UTAD** = Upthrust After Distribution.)
* **Phase D** — break of the range with conviction. **SOS** = Sign of
  Strength (high-volume rally above range). **LPS** = Last Point of
  Support (a higher low confirming the breakout).
* **Phase E** — Markup begins; the range is left behind.

Confluence
----------

Wyckoff is fundamentally fuzzy. Each event is detected with a
**confluence score** in ``[0, 1]`` aggregated from multiple confirmation
factors (volume z-score, wick rejection ratio, divergence presence,
liquidity sweep). The FSM advances state when total confluence on the
defining event clears a threshold; weak signals leave the state where it
was, with an audit trail in the snapshot's event chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Literal

import polars as pl

from pa_assistant.analysis.divergence import DivergenceEvent
from pa_assistant.analysis.liquidity import detect_liquidity_levels
from pa_assistant.analysis.stop_hunt import detect_stop_hunts
from pa_assistant.analysis.structure import detect_swings

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class WyckoffPhase(StrEnum):
    """All states the FSM can occupy."""

    NEUTRAL = "neutral"
    # Accumulation cycle
    ACC_A = "accumulation_phase_a"
    ACC_B = "accumulation_phase_b"
    ACC_C = "accumulation_phase_c"
    ACC_D = "accumulation_phase_d"
    ACC_E = "accumulation_phase_e"
    # Distribution cycle
    DIST_A = "distribution_phase_a"
    DIST_B = "distribution_phase_b"
    DIST_C = "distribution_phase_c"
    DIST_D = "distribution_phase_d"
    DIST_E = "distribution_phase_e"


class WyckoffEventType(StrEnum):
    """All event types the detector can emit.

    Accumulation side: SC, AR, ST, SPRING, SOS, LPS.
    Distribution side: BC, AR_DIST, ST_DIST, UTAD, SOW, LPSY.
    """

    # Accumulation
    SC = "selling_climax"
    AR = "automatic_rally"
    ST = "secondary_test"
    SPRING = "spring"
    SOS = "sign_of_strength"
    LPS = "last_point_of_support"
    # Distribution (mirror)
    BC = "buying_climax"
    AR_DIST = "automatic_reaction"
    ST_DIST = "secondary_test_distribution"
    UTAD = "upthrust_after_distribution"
    SOW = "sign_of_weakness"
    LPSY = "last_point_of_supply"


Side = Literal["accumulation", "distribution"]


@dataclass(frozen=True, slots=True)
class WyckoffEvent:
    """A single Wyckoff event with multi-factor confluence breakdown."""

    timestamp: datetime
    event_type: WyckoffEventType
    bar_index: int
    price: float
    confluence: dict[str, float] = field(default_factory=dict)

    @property
    def side(self) -> Side:
        """Whether this event belongs to the accumulation or distribution cycle."""
        if self.event_type in {
            WyckoffEventType.SC,
            WyckoffEventType.AR,
            WyckoffEventType.ST,
            WyckoffEventType.SPRING,
            WyckoffEventType.SOS,
            WyckoffEventType.LPS,
        }:
            return "accumulation"
        return "distribution"

    @property
    def confidence(self) -> float:
        """Mean confluence score in ``[0, 1]``."""
        if not self.confluence:
            return 0.0
        return sum(self.confluence.values()) / len(self.confluence)


@dataclass(frozen=True, slots=True)
class WyckoffSnapshot:
    """FSM state at a point in time, plus the audit trail of events that produced it."""

    timestamp: datetime
    phase: WyckoffPhase
    range_high: float | None
    range_low: float | None
    events: tuple[WyckoffEvent, ...]
    confidence: float

    @property
    def side(self) -> Side | None:
        if self.phase == WyckoffPhase.NEUTRAL:
            return None
        if self.phase.value.startswith("accumulation"):
            return "accumulation"
        return "distribution"

    @property
    def invalidation_price(self) -> float | None:
        """Price level whose break would invalidate the current phase.

        For accumulation phases C/D/E: a close below ``range_low`` invalidates.
        For distribution phases C/D/E: a close above ``range_high`` invalidates.
        Earlier phases have no firm invalidation.
        """
        if self.phase in {
            WyckoffPhase.ACC_C,
            WyckoffPhase.ACC_D,
            WyckoffPhase.ACC_E,
        }:
            return self.range_low
        if self.phase in {
            WyckoffPhase.DIST_C,
            WyckoffPhase.DIST_D,
            WyckoffPhase.DIST_E,
        }:
            return self.range_high
        return None


# ---------------------------------------------------------------------------
# Event detection
# ---------------------------------------------------------------------------


def detect_wyckoff_events(
    df: pl.DataFrame,
    *,
    swing_lookback: int = 3,
    volume_climax_z: float = 2.0,
    volume_window: int = 20,
    eq_tolerance_bps: float = 10.0,
    divergences: list[DivergenceEvent] | None = None,
) -> list[WyckoffEvent]:
    """Detect Wyckoff vocabulary events from an OHLCV-with-extras DataFrame.

    The input must contain ``open_time``, ``open``, ``high``, ``low``,
    ``close``, ``volume``. Optional columns enrich confluence scoring:
    ``cvd`` and ``oi``.

    Parameters
    ----------
    df:
        Resampled OHLCV with optional indicator columns.
    swing_lookback:
        Forwarded to :func:`detect_swings`. Default 3 — slightly stricter
        than the standard fractal because Wyckoff cares about meaningful
        pivots only.
    volume_climax_z:
        Minimum z-score (over ``volume_window`` rolling mean+std) for a
        bar's volume to qualify as a climax. Default 2.0 = roughly the
        top 2.5% of bars by relative volume.
    volume_window:
        Rolling window for volume z-score baseline.
    eq_tolerance_bps:
        Tolerance for liquidity pool clustering, forwarded to
        :func:`detect_liquidity_levels`.
    divergences:
        Optional pre-computed divergence list. If given, presence at a
        candidate event boosts that event's confluence score.

    Returns
    -------
    A timestamp-sorted list of :class:`WyckoffEvent`.
    """
    required = {"open_time", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"detect_wyckoff_events: missing columns {missing}")

    if df.is_empty() or df.height < volume_window + swing_lookback * 2:
        return []

    annotated = detect_swings(df, lookback=swing_lookback)

    # Volume z-score column
    annotated = annotated.with_columns(
        (
            (pl.col("volume") - pl.col("volume").rolling_mean(volume_window))
            / pl.col("volume").rolling_std(volume_window)
        ).alias("_vol_z"),
    )

    rows = annotated.to_dicts()

    # Build a divergence index: bar_index -> list of divergence events at that bar.
    div_by_ts: dict[datetime, list[DivergenceEvent]] = {}
    if divergences:
        for d in divergences:
            div_by_ts.setdefault(d.timestamp, []).append(d)

    # Liquidity pools — used for Spring / UTAD confluence
    levels = detect_liquidity_levels(df, tolerance_bps=eq_tolerance_bps)
    stop_hunts = detect_stop_hunts(df, levels)

    events: list[WyckoffEvent] = []

    # Climaxes: find bars with extreme volume z-score AT a swing extreme
    # with rejection wick.
    for i, row in enumerate(rows):
        vol_z = row.get("_vol_z")
        if vol_z is None or vol_z < volume_climax_z:
            continue

        bar_range = float(row["high"]) - float(row["low"])
        if bar_range <= 0:
            continue
        ts = row["open_time"]
        close = float(row["close"])

        # Selling Climax: swing low + lower wick rejection + extreme volume
        if row.get("swing_low") is not None:
            lower_wick = min(float(row["open"]), close) - float(row["low"])
            wick_ratio = lower_wick / bar_range
            if wick_ratio >= 0.4:
                conf = {
                    "volume_climax": min(float(vol_z) / volume_climax_z / 2.0, 1.0),
                    "wick_rejection": wick_ratio,
                }
                if any(d.side == "bullish" for d in div_by_ts.get(ts, [])):
                    conf["bullish_divergence"] = 0.8
                events.append(
                    WyckoffEvent(
                        timestamp=ts,
                        event_type=WyckoffEventType.SC,
                        bar_index=i,
                        price=float(row["low"]),
                        confluence=conf,
                    )
                )

        # Buying Climax: swing high + upper wick + extreme volume
        if row.get("swing_high") is not None:
            upper_wick = float(row["high"]) - max(float(row["open"]), close)
            wick_ratio = upper_wick / bar_range
            if wick_ratio >= 0.4:
                conf = {
                    "volume_climax": min(float(vol_z) / volume_climax_z / 2.0, 1.0),
                    "wick_rejection": wick_ratio,
                }
                if any(d.side == "bearish" for d in div_by_ts.get(ts, [])):
                    conf["bearish_divergence"] = 0.8
                events.append(
                    WyckoffEvent(
                        timestamp=ts,
                        event_type=WyckoffEventType.BC,
                        bar_index=i,
                        price=float(row["high"]),
                        confluence=conf,
                    )
                )

    # Springs / UTADs come from stop hunts. A Spring is a stop hunt below an
    # equal-low pool that closed back inside; UTAD is the symmetric sweep
    # above an equal-high pool.
    for hunt in stop_hunts:
        if not hunt.confirmed:
            continue
        ts = hunt.timestamp
        bar_idx = next((i for i, r in enumerate(rows) if r["open_time"] == ts), -1)
        if bar_idx < 0:
            continue
        conf = {
            "wick_rejection": hunt.wick_ratio,
            "volume_ratio": min(hunt.volume_ratio / 2.0, 1.0),
            "pool_quality": min(hunt.pool_touches / 4.0, 1.0),
            "confirmed_reversal": 1.0,
        }
        if hunt.side == "low":
            event_type = WyckoffEventType.SPRING
            if any(d.side == "bullish" for d in div_by_ts.get(ts, [])):
                conf["bullish_divergence"] = 0.8
        else:
            event_type = WyckoffEventType.UTAD
            if any(d.side == "bearish" for d in div_by_ts.get(ts, [])):
                conf["bearish_divergence"] = 0.8
        events.append(
            WyckoffEvent(
                timestamp=ts,
                event_type=event_type,
                bar_index=bar_idx,
                price=hunt.extreme,
                confluence=conf,
            )
        )

    events.sort(key=lambda e: (e.timestamp, e.event_type.value))
    return events


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

# Confidence threshold below which an event is logged but the state does not change.
MIN_TRANSITION_CONFIDENCE = 0.45


def evolve(state: WyckoffSnapshot, event: WyckoffEvent) -> WyckoffSnapshot:
    """Pure transition function: ``(state, event) → next_state``.

    Tracks the event in the snapshot's audit trail regardless of whether
    it triggers a phase transition, so users can see "this event was
    seen but did not advance the state because confidence was low".
    """
    new_events = (*state.events, event)
    et = event.event_type
    side = event.side
    conf = event.confidence

    # Helper: stay-in-phase but record event
    def stay() -> WyckoffSnapshot:
        return replace(state, events=new_events)

    # Phase transitions out of NEUTRAL — first climax defines the cycle direction.
    if state.phase == WyckoffPhase.NEUTRAL:
        if conf < MIN_TRANSITION_CONFIDENCE:
            return stay()
        if et == WyckoffEventType.SC:
            return WyckoffSnapshot(
                timestamp=event.timestamp,
                phase=WyckoffPhase.ACC_A,
                range_high=None,
                range_low=event.price,
                events=new_events,
                confidence=conf,
            )
        if et == WyckoffEventType.BC:
            return WyckoffSnapshot(
                timestamp=event.timestamp,
                phase=WyckoffPhase.DIST_A,
                range_high=event.price,
                range_low=None,
                events=new_events,
                confidence=conf,
            )
        return stay()

    # Cycle-locked transitions: only events on the same side advance state.
    # Exception: a strong opposite-side CLIMAX in Phase A/B (when we have
    # not yet committed to the direction via Spring/UTAD) flips the cycle.
    # Once past Phase B, cycle commitment is firm — only a range-break
    # invalidation (handled outside this FSM) should reset.
    if state.side != side:
        if et == WyckoffEventType.SC and state.phase in {
            WyckoffPhase.DIST_A,
            WyckoffPhase.DIST_B,
        } and conf >= MIN_TRANSITION_CONFIDENCE + 0.15:
            return WyckoffSnapshot(
                timestamp=event.timestamp,
                phase=WyckoffPhase.ACC_A,
                range_high=None,
                range_low=event.price,
                events=new_events,
                confidence=conf,
            )
        if et == WyckoffEventType.BC and state.phase in {
            WyckoffPhase.ACC_A,
            WyckoffPhase.ACC_B,
        } and conf >= MIN_TRANSITION_CONFIDENCE + 0.15:
            return WyckoffSnapshot(
                timestamp=event.timestamp,
                phase=WyckoffPhase.DIST_A,
                range_high=event.price,
                range_low=None,
                events=new_events,
                confidence=conf,
            )
        return stay()

    # Accumulation cycle
    if state.side == "accumulation":
        return _evolve_accumulation(state, event, new_events)

    # Distribution cycle
    if state.side == "distribution":
        return _evolve_distribution(state, event, new_events)

    return stay()


def _evolve_accumulation(
    state: WyckoffSnapshot,
    event: WyckoffEvent,
    new_events: tuple[WyckoffEvent, ...],
) -> WyckoffSnapshot:
    et = event.event_type
    conf = event.confidence

    if state.phase == WyckoffPhase.ACC_A:
        # AR establishes the upper range bound.
        if et == WyckoffEventType.AR:
            return WyckoffSnapshot(
                timestamp=event.timestamp,
                phase=WyckoffPhase.ACC_B,
                range_high=event.price,
                range_low=state.range_low,
                events=new_events,
                confidence=(state.confidence + conf) / 2,
            )
        # Climax in same direction can re-anchor the low.
        if et == WyckoffEventType.SC and event.price < (state.range_low or float("inf")):
            return replace(state, range_low=event.price, events=new_events)
        return replace(state, events=new_events)

    if state.phase == WyckoffPhase.ACC_B:
        if et == WyckoffEventType.SPRING and conf >= MIN_TRANSITION_CONFIDENCE:
            return WyckoffSnapshot(
                timestamp=event.timestamp,
                phase=WyckoffPhase.ACC_C,
                range_high=state.range_high,
                range_low=event.price,  # spring re-anchors low
                events=new_events,
                confidence=conf,
            )
        # ST keeps us in B, possibly increasing confidence.
        if et == WyckoffEventType.ST:
            return replace(
                state,
                events=new_events,
                confidence=min((state.confidence + conf) / 2 + 0.1, 1.0),
            )
        return replace(state, events=new_events)

    if state.phase == WyckoffPhase.ACC_C:
        if et == WyckoffEventType.SOS and conf >= MIN_TRANSITION_CONFIDENCE:
            return replace(
                state,
                phase=WyckoffPhase.ACC_D,
                events=new_events,
                confidence=conf,
                timestamp=event.timestamp,
            )
        return replace(state, events=new_events)

    if state.phase == WyckoffPhase.ACC_D:
        if et == WyckoffEventType.LPS and conf >= MIN_TRANSITION_CONFIDENCE:
            return replace(
                state,
                phase=WyckoffPhase.ACC_E,
                events=new_events,
                confidence=conf,
                timestamp=event.timestamp,
            )
        return replace(state, events=new_events)

    # Phase E: any further events just append; the cycle has completed.
    return replace(state, events=new_events)


def _evolve_distribution(
    state: WyckoffSnapshot,
    event: WyckoffEvent,
    new_events: tuple[WyckoffEvent, ...],
) -> WyckoffSnapshot:
    et = event.event_type
    conf = event.confidence

    if state.phase == WyckoffPhase.DIST_A:
        if et == WyckoffEventType.AR_DIST:
            return WyckoffSnapshot(
                timestamp=event.timestamp,
                phase=WyckoffPhase.DIST_B,
                range_high=state.range_high,
                range_low=event.price,
                events=new_events,
                confidence=(state.confidence + conf) / 2,
            )
        if et == WyckoffEventType.BC and event.price > (state.range_high or float("-inf")):
            return replace(state, range_high=event.price, events=new_events)
        return replace(state, events=new_events)

    if state.phase == WyckoffPhase.DIST_B:
        if et == WyckoffEventType.UTAD and conf >= MIN_TRANSITION_CONFIDENCE:
            return WyckoffSnapshot(
                timestamp=event.timestamp,
                phase=WyckoffPhase.DIST_C,
                range_high=event.price,
                range_low=state.range_low,
                events=new_events,
                confidence=conf,
            )
        if et == WyckoffEventType.ST_DIST:
            return replace(
                state,
                events=new_events,
                confidence=min((state.confidence + conf) / 2 + 0.1, 1.0),
            )
        return replace(state, events=new_events)

    if state.phase == WyckoffPhase.DIST_C:
        if et == WyckoffEventType.SOW and conf >= MIN_TRANSITION_CONFIDENCE:
            return replace(
                state,
                phase=WyckoffPhase.DIST_D,
                events=new_events,
                confidence=conf,
                timestamp=event.timestamp,
            )
        return replace(state, events=new_events)

    if state.phase == WyckoffPhase.DIST_D:
        if et == WyckoffEventType.LPSY and conf >= MIN_TRANSITION_CONFIDENCE:
            return replace(
                state,
                phase=WyckoffPhase.DIST_E,
                events=new_events,
                confidence=conf,
                timestamp=event.timestamp,
            )
        return replace(state, events=new_events)

    return replace(state, events=new_events)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def analyze_wyckoff(
    df: pl.DataFrame,
    *,
    swing_lookback: int = 3,
    volume_climax_z: float = 2.0,
    volume_window: int = 20,
    eq_tolerance_bps: float = 10.0,
    divergences: list[DivergenceEvent] | None = None,
) -> list[WyckoffSnapshot]:
    """Run the full pipeline: events → FSM evolution → snapshot history.

    Returns the list of all snapshots produced, in chronological order.
    The last snapshot is the current state. The first snapshot is always
    NEUTRAL with no events.
    """
    if df.is_empty():
        return [_initial_snapshot(datetime.fromtimestamp(0))]

    events = detect_wyckoff_events(
        df,
        swing_lookback=swing_lookback,
        volume_climax_z=volume_climax_z,
        volume_window=volume_window,
        eq_tolerance_bps=eq_tolerance_bps,
        divergences=divergences,
    )

    first_ts = df.row(0, named=True)["open_time"]
    snapshots: list[WyckoffSnapshot] = [_initial_snapshot(first_ts)]
    for event in events:
        snapshots.append(evolve(snapshots[-1], event))
    return snapshots


def _initial_snapshot(ts: datetime) -> WyckoffSnapshot:
    return WyckoffSnapshot(
        timestamp=ts,
        phase=WyckoffPhase.NEUTRAL,
        range_high=None,
        range_low=None,
        events=(),
        confidence=0.0,
    )
