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
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

import polars as pl

from pa_assistant.analysis.divergence import DivergenceEvent
from pa_assistant.analysis.liquidity import detect_liquidity_levels
from pa_assistant.analysis.stop_hunt import StopHunt, detect_stop_hunts
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

# Stop hunts on bars below this interval are too noisy to be meaningful
# Wyckoff events. Wyckoff Springs / UTADs are macro-structural — minute-level
# wick noise produces false positives.
MIN_BAR_SECONDS_FOR_STOP_HUNTS = 3600  # 1 hour


def detect_wyckoff_events(
    df: pl.DataFrame,
    *,
    swing_lookback: int = 3,
    volume_climax_z: float = 2.0,
    volume_window: int = 20,
    eq_tolerance_bps: float = 10.0,
    ar_lookahead_bars: int = 30,
    ar_min_rally_pct: float = 0.005,
    st_max_lookahead_bars: int = 60,
    st_tolerance_pct: float = 0.01,
    sos_volume_z: float = 1.5,
    sos_body_ratio: float = 0.5,
    sos_lookahead_bars: int = 30,
    lps_lookahead_bars: int = 20,
    lps_tolerance_pct: float = 0.005,
    divergences: list[DivergenceEvent] | None = None,
) -> list[WyckoffEvent]:
    """Detect Wyckoff vocabulary events from an OHLCV-with-extras DataFrame.

    The input must contain ``open_time``, ``open``, ``high``, ``low``,
    ``close``, ``volume``. Optional ``cvd`` / ``oi`` columns combined with
    ``divergences=`` enrich confluence scoring.

    Detection passes
    ----------------
    1. **Climaxes** (SC, BC) — bars with volume z-score >= ``volume_climax_z``
       at a swing extreme with rejection wick >= 40%.
    2. **Springs / UTADs** — confirmed liquidity-pool sweeps (from
       :func:`detect_stop_hunts`). **Skipped** when the input timeframe
       is below 1 hour (sub-hourly stop hunts are too noisy to mean
       anything Wyckoff-wise).
    3. **AR / AR_DIST** — for each climax, the first significant swing in
       the opposite direction within ``ar_lookahead_bars``. Together SC+AR
       define the initial trading range.
    4. **ST / ST_DIST** — swing extremes inside the established range that
       approach the climax price (within ``st_tolerance_pct``) on
       diminishing volume — Wyckoff's "test" of supply / demand.
    5. **SOS / SOW** — after a Spring (or UTAD), a breakout bar whose
       close clears the range edge with volume z-score >= ``sos_volume_z``
       and body ratio >= ``sos_body_ratio``.
    6. **LPS / LPSY** — after SOS / SOW, a swing that holds above (below)
       the former range edge, now flipped to support (resistance).

    Stop-hunt timeframe guard
    -------------------------
    The detector inspects the median bar interval of ``df`` and skips
    Spring/UTAD detection when it is below 1 hour. Wyckoff Springs are
    macro-structural events; sub-hourly wick noise produces false
    positives that pollute the FSM. For Springs to be detected, run on
    a 1h+ resampled DataFrame.
    """
    required = {"open_time", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"detect_wyckoff_events: missing columns {missing}")

    if df.is_empty() or df.height < volume_window + swing_lookback * 2:
        return []

    annotated = detect_swings(df, lookback=swing_lookback)
    annotated = annotated.with_columns(
        (
            (pl.col("volume") - pl.col("volume").rolling_mean(volume_window))
            / pl.col("volume").rolling_std(volume_window)
        ).alias("_vol_z"),
    )
    rows = annotated.to_dicts()

    div_by_ts: dict[datetime, list[DivergenceEvent]] = {}
    if divergences:
        for d in divergences:
            div_by_ts.setdefault(d.timestamp, []).append(d)

    events: list[WyckoffEvent] = []

    # --- Pass 1: climaxes ---------------------------------------------------
    climaxes = _detect_climaxes(rows, volume_climax_z, div_by_ts)
    events.extend(climaxes)

    # --- Pass 2: Springs / UTADs (1h+ only) ---------------------------------
    if _bar_interval_seconds(df) >= MIN_BAR_SECONDS_FOR_STOP_HUNTS:
        levels = detect_liquidity_levels(df, tolerance_bps=eq_tolerance_bps)
        stop_hunts = detect_stop_hunts(df, levels)
        events.extend(_to_spring_utad_events(stop_hunts, rows, div_by_ts))

    # --- Pass 3: AR/AR_DIST per climax --------------------------------------
    ar_events: list[WyckoffEvent] = []
    for c in climaxes:
        ar = _find_automatic_rally(
            rows,
            c,
            lookahead_bars=ar_lookahead_bars,
            min_pct=ar_min_rally_pct,
        )
        if ar is not None:
            ar_events.append(ar)
    events.extend(ar_events)

    # --- Pass 4: ST/ST_DIST inside each established range -------------------
    for c in climaxes:
        ar = _find_ar_for_climax(c, ar_events)
        if ar is None:
            continue
        events.extend(
            _find_secondary_tests(
                rows,
                climax=c,
                ar=ar,
                max_bars=st_max_lookahead_bars,
                tolerance_pct=st_tolerance_pct,
            )
        )

    # --- Pass 5: SOS / SOW after Spring / UTAD ------------------------------
    sos_events: list[WyckoffEvent] = []
    spring_or_utad_events = [
        e
        for e in events
        if e.event_type in {WyckoffEventType.SPRING, WyckoffEventType.UTAD}
    ]
    for s in spring_or_utad_events:
        # Use the most recent AR (same side) before this Spring/UTAD as the
        # range edge to break.
        side: Side = (
            "accumulation" if s.event_type == WyckoffEventType.SPRING else "distribution"
        )
        edge = _find_range_edge_for(s, ar_events, side=side)
        if edge is None:
            continue
        sos = _find_sos_after(
            rows,
            anchor=s,
            range_edge=edge,
            side=side,
            volume_z=sos_volume_z,
            body_ratio_min=sos_body_ratio,
            lookahead_bars=sos_lookahead_bars,
        )
        if sos is not None:
            sos_events.append(sos)
    events.extend(sos_events)

    # --- Pass 6: LPS / LPSY after SOS / SOW ---------------------------------
    for sos in sos_events:
        side = "accumulation" if sos.event_type == WyckoffEventType.SOS else "distribution"
        edge = _find_range_edge_for(sos, ar_events, side=side)
        if edge is None:
            continue
        lps = _find_lps_after(
            rows,
            anchor=sos,
            range_edge=edge,
            side=side,
            lookahead_bars=lps_lookahead_bars,
            tolerance_pct=lps_tolerance_pct,
        )
        if lps is not None:
            events.append(lps)

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
        # Range re-anchoring inside Phase B
        if et == WyckoffEventType.SC and event.price < (state.range_low or float("inf")):
            return replace(state, range_low=event.price, events=new_events)
        if et == WyckoffEventType.AR and event.price > (state.range_high or float("-inf")):
            return replace(state, range_high=event.price, events=new_events)
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
        # Range re-anchoring inside Phase B
        if et == WyckoffEventType.BC and event.price > (state.range_high or float("-inf")):
            return replace(state, range_high=event.price, events=new_events)
        if et == WyckoffEventType.AR_DIST and event.price < (state.range_low or float("inf")):
            return replace(state, range_low=event.price, events=new_events)
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


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _bar_interval_seconds(df: pl.DataFrame) -> int:
    """Median bar interval in seconds. ``0`` if undeterminable."""
    if df.height < 2:
        return 0
    deltas = df.get_column("open_time").diff().drop_nulls()
    if deltas.is_empty():
        return 0
    median_delta = deltas.median()
    if median_delta is None or not isinstance(median_delta, timedelta):
        return 0
    return int(median_delta.total_seconds())


def _f(row: dict[str, object], key: str) -> float:
    """Extract a float from a row dict (silences mypy on Polars' object typing)."""
    return float(row[key])  # type: ignore[arg-type]


def _detect_climaxes(
    rows: list[dict[str, object]],
    volume_climax_z: float,
    div_by_ts: dict[datetime, list[DivergenceEvent]],
) -> list[WyckoffEvent]:
    """Pass 1: Selling/Buying climaxes (swing extreme + volume + rejection wick)."""
    events: list[WyckoffEvent] = []
    for i, row in enumerate(rows):
        vol_z = row.get("_vol_z")
        if vol_z is None or float(vol_z) < volume_climax_z:  # type: ignore[arg-type]
            continue
        high_v = _f(row, "high")
        low_v = _f(row, "low")
        bar_range = high_v - low_v
        if bar_range <= 0:
            continue
        ts = row["open_time"]
        assert isinstance(ts, datetime)
        open_v = _f(row, "open")
        close_v = _f(row, "close")

        if row.get("swing_low") is not None:
            lower_wick = min(open_v, close_v) - low_v
            wick_ratio = lower_wick / bar_range
            if wick_ratio >= 0.4:
                conf = {
                    "volume_climax": min(float(vol_z) / volume_climax_z / 2.0, 1.0),  # type: ignore[arg-type]
                    "wick_rejection": wick_ratio,
                }
                if any(d.side == "bullish" for d in div_by_ts.get(ts, [])):
                    conf["bullish_divergence"] = 0.8
                events.append(
                    WyckoffEvent(
                        timestamp=ts,
                        event_type=WyckoffEventType.SC,
                        bar_index=i,
                        price=low_v,
                        confluence=conf,
                    )
                )

        if row.get("swing_high") is not None:
            upper_wick = high_v - max(open_v, close_v)
            wick_ratio = upper_wick / bar_range
            if wick_ratio >= 0.4:
                conf = {
                    "volume_climax": min(float(vol_z) / volume_climax_z / 2.0, 1.0),  # type: ignore[arg-type]
                    "wick_rejection": wick_ratio,
                }
                if any(d.side == "bearish" for d in div_by_ts.get(ts, [])):
                    conf["bearish_divergence"] = 0.8
                events.append(
                    WyckoffEvent(
                        timestamp=ts,
                        event_type=WyckoffEventType.BC,
                        bar_index=i,
                        price=high_v,
                        confluence=conf,
                    )
                )
    return events


def _to_spring_utad_events(
    stop_hunts: list[StopHunt],
    rows: list[dict[str, object]],
    div_by_ts: dict[datetime, list[DivergenceEvent]],
) -> list[WyckoffEvent]:
    """Pass 2: confirmed stop hunts -> Springs (low side) / UTADs (high side)."""
    events: list[WyckoffEvent] = []
    for hunt in stop_hunts:
        if not hunt.confirmed:
            continue
        bar_idx = next(
            (i for i, r in enumerate(rows) if r["open_time"] == hunt.timestamp), -1
        )
        if bar_idx < 0:
            continue
        conf = {
            "wick_rejection": hunt.wick_ratio,
            "volume_ratio": min(hunt.volume_ratio / 2.0, 1.0),
            "pool_quality": min(hunt.pool_touches / 4.0, 1.0),
            "confirmed_reversal": 1.0,
        }
        if hunt.side == "low":
            et = WyckoffEventType.SPRING
            if any(d.side == "bullish" for d in div_by_ts.get(hunt.timestamp, [])):
                conf["bullish_divergence"] = 0.8
        else:
            et = WyckoffEventType.UTAD
            if any(d.side == "bearish" for d in div_by_ts.get(hunt.timestamp, [])):
                conf["bearish_divergence"] = 0.8
        events.append(
            WyckoffEvent(
                timestamp=hunt.timestamp,
                event_type=et,
                bar_index=bar_idx,
                price=hunt.extreme,
                confluence=conf,
            )
        )
    return events


def _find_automatic_rally(
    rows: list[dict[str, object]],
    climax: WyckoffEvent,
    *,
    lookahead_bars: int,
    min_pct: float,
) -> WyckoffEvent | None:
    """Pass 3: For an SC, find the highest swing high in the next N bars.

    For a BC, find the lowest swing low. Generates AR (accumulation) or
    AR_DIST (distribution).
    """
    start = climax.bar_index + 1
    end = min(start + lookahead_bars, len(rows))
    if start >= end:
        return None

    if climax.event_type == WyckoffEventType.SC:
        swing_col = "swing_high"
        seed = float("-inf")
        target_et = WyckoffEventType.AR
        accumulating = True
    else:
        swing_col = "swing_low"
        seed = float("inf")
        target_et = WyckoffEventType.AR_DIST
        accumulating = False

    best_idx = -1
    best_price = seed
    for i in range(start, end):
        sw = rows[i].get(swing_col)
        if sw is None:
            continue
        sw_f = float(sw)  # type: ignore[arg-type]
        rally_pct = abs(sw_f - climax.price) / climax.price
        if rally_pct < min_pct:
            continue
        if accumulating:
            if sw_f > best_price:
                best_price = sw_f
                best_idx = i
        else:
            if sw_f < best_price:
                best_price = sw_f
                best_idx = i

    if best_idx < 0:
        return None

    rally_pct = abs(best_price - climax.price) / climax.price
    distance = best_idx - climax.bar_index
    conf = {
        "rally_magnitude": min(rally_pct / 0.05, 1.0),
        "promptness": max(0.0, 1.0 - distance / lookahead_bars),
        "structural_pivot": 0.7,
    }
    ts = rows[best_idx]["open_time"]
    assert isinstance(ts, datetime)
    return WyckoffEvent(
        timestamp=ts,
        event_type=target_et,
        bar_index=best_idx,
        price=best_price,
        confluence=conf,
    )


def _find_ar_for_climax(
    climax: WyckoffEvent, ar_events: list[WyckoffEvent]
) -> WyckoffEvent | None:
    """Find the AR event that immediately follows a given climax."""
    target_et = (
        WyckoffEventType.AR
        if climax.event_type == WyckoffEventType.SC
        else WyckoffEventType.AR_DIST
    )
    candidates = [
        ar for ar in ar_events if ar.event_type == target_et and ar.bar_index > climax.bar_index
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda ar: ar.bar_index)


def _find_secondary_tests(
    rows: list[dict[str, object]],
    *,
    climax: WyckoffEvent,
    ar: WyckoffEvent,
    max_bars: int,
    tolerance_pct: float,
) -> list[WyckoffEvent]:
    """Pass 4: STs inside the [climax, AR] range that retest the climax price.

    A valid ST has:
      - swing extreme within ``tolerance_pct`` of the climax price
      - lower volume z-score than the climax (Wyckoff "diminishing supply/demand")
    """
    start = ar.bar_index + 1
    end = min(start + max_bars, len(rows))
    if start >= end:
        return []

    climax_vol_z_norm = climax.confluence.get("volume_climax", 0.0)
    is_acc = climax.event_type == WyckoffEventType.SC
    swing_col = "swing_low" if is_acc else "swing_high"
    target_et = WyckoffEventType.ST if is_acc else WyckoffEventType.ST_DIST

    events: list[WyckoffEvent] = []
    for i in range(start, end):
        row = rows[i]
        sw = row.get(swing_col)
        if sw is None:
            continue
        sw_f = float(sw)  # type: ignore[arg-type]
        diff_pct = abs(sw_f - climax.price) / climax.price
        if diff_pct > tolerance_pct:
            continue
        vol_z = row.get("_vol_z")
        vol_z_f = float(vol_z) if vol_z is not None else 0.0  # type: ignore[arg-type]
        # ST should have notably lower volume than climax
        normalized_vol = min(max(vol_z_f, 0.0) / 4.0, 1.0)
        if normalized_vol >= climax_vol_z_norm * 0.8:
            continue
        ts = row["open_time"]
        assert isinstance(ts, datetime)
        conf = {
            "test_proximity": 1.0 - min(diff_pct / tolerance_pct, 1.0),
            "volume_diminishment": 1.0 - normalized_vol,
            "structural_pivot": 0.6,
        }
        events.append(
            WyckoffEvent(
                timestamp=ts,
                event_type=target_et,
                bar_index=i,
                price=sw_f,
                confluence=conf,
            )
        )
    return events


def _find_range_edge_for(
    anchor: WyckoffEvent,
    ar_events: list[WyckoffEvent],
    *,
    side: Side,
) -> float | None:
    """Find the most recent AR (accumulation) / AR_DIST (distribution) before
    ``anchor``. Returns its price — the edge that SOS / SOW must clear.
    """
    target_et = WyckoffEventType.AR if side == "accumulation" else WyckoffEventType.AR_DIST
    candidates = [
        ar
        for ar in ar_events
        if ar.event_type == target_et and ar.bar_index < anchor.bar_index
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda ar: ar.bar_index).price


def _find_sos_after(
    rows: list[dict[str, object]],
    *,
    anchor: WyckoffEvent,
    range_edge: float,
    side: Side,
    volume_z: float,
    body_ratio_min: float,
    lookahead_bars: int,
) -> WyckoffEvent | None:
    """Pass 5: After Spring/UTAD, find first bar that breaks ``range_edge``
    with strong volume + dominant body. Generates SOS / SOW.
    """
    start = anchor.bar_index + 1
    end = min(start + lookahead_bars, len(rows))
    if start >= end:
        return None

    target_et = WyckoffEventType.SOS if side == "accumulation" else WyckoffEventType.SOW

    for i in range(start, end):
        row = rows[i]
        close_v = _f(row, "close")
        open_v = _f(row, "open")
        high_v = _f(row, "high")
        low_v = _f(row, "low")

        if side == "accumulation" and close_v <= range_edge:
            continue
        if side == "distribution" and close_v >= range_edge:
            continue

        bar_range = high_v - low_v
        if bar_range == 0:
            continue
        body_ratio = abs(close_v - open_v) / bar_range
        if body_ratio < body_ratio_min:
            continue
        vol_z = row.get("_vol_z")
        if vol_z is None or float(vol_z) < volume_z:  # type: ignore[arg-type]
            continue

        breakout_pct = abs(close_v - range_edge) / range_edge
        ts = row["open_time"]
        assert isinstance(ts, datetime)
        conf = {
            "volume_strength": min(float(vol_z) / volume_z / 2.0, 1.0),  # type: ignore[arg-type]
            "body_dominance": body_ratio,
            "breakout_magnitude": min(breakout_pct / 0.02, 1.0),
        }
        return WyckoffEvent(
            timestamp=ts,
            event_type=target_et,
            bar_index=i,
            price=close_v,
            confluence=conf,
        )
    return None


def _find_lps_after(
    rows: list[dict[str, object]],
    *,
    anchor: WyckoffEvent,
    range_edge: float,
    side: Side,
    lookahead_bars: int,
    tolerance_pct: float,
) -> WyckoffEvent | None:
    """Pass 6: After SOS/SOW, find swing that holds at the former range edge
    (now flipped to support / resistance). Generates LPS / LPSY.
    """
    start = anchor.bar_index + 1
    end = min(start + lookahead_bars, len(rows))
    if start >= end:
        return None

    is_acc = side == "accumulation"
    swing_col = "swing_low" if is_acc else "swing_high"
    target_et = WyckoffEventType.LPS if is_acc else WyckoffEventType.LPSY

    # Tolerance: LPS may dip slightly below the former range_high (now
    # support); it just shouldn't break far below it.
    threshold = range_edge * (1 - tolerance_pct) if is_acc else range_edge * (1 + tolerance_pct)

    for i in range(start, end):
        row = rows[i]
        sw = row.get(swing_col)
        if sw is None:
            continue
        sw_f = float(sw)  # type: ignore[arg-type]
        # Hold check: for accumulation, swing low should not break too far
        # below the former range_high. For distribution, swing high should
        # not break too far above former range_low.
        if is_acc and sw_f < threshold:
            return None  # Failed hold — invalidation, not LPS
        if not is_acc and sw_f > threshold:
            return None
        # Distance from edge measures "support cleanliness"
        edge_distance_pct = abs(sw_f - range_edge) / range_edge
        ts = row["open_time"]
        assert isinstance(ts, datetime)
        conf = {
            "support_hold" if is_acc else "resistance_hold": 0.85,
            "structure_higher_low" if is_acc else "structure_lower_high": 0.7,
            "edge_proximity": max(0.0, 1.0 - edge_distance_pct / 0.02),
        }
        return WyckoffEvent(
            timestamp=ts,
            event_type=target_et,
            bar_index=i,
            price=sw_f,
            confluence=conf,
        )
    return None
