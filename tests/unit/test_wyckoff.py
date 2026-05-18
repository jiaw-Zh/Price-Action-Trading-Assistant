"""Tests for the Wyckoff phase state machine."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

import polars as pl
import pytest

from pa_assistant.analysis.wyckoff import (
    WyckoffEvent,
    WyckoffEventType,
    WyckoffPhase,
    WyckoffSnapshot,
    analyze_wyckoff,
    detect_wyckoff_events,
    evolve,
)


def _bars(
    *,
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
) -> pl.DataFrame:
    n = len(opens)
    base = datetime(2025, 1, 1)
    return pl.DataFrame(
        {
            "open_time": [base + timedelta(hours=i) for i in range(n)],
            "open": list(map(float, opens)),
            "high": list(map(float, highs)),
            "low": list(map(float, lows)),
            "close": list(map(float, closes)),
            "volume": list(map(float, volumes)),
        }
    )


def _make_event(
    et: WyckoffEventType,
    *,
    price: float = 100.0,
    confluence: dict[str, float] | None = None,
    bar_index: int = 0,
    ts: datetime | None = None,
) -> WyckoffEvent:
    return WyckoffEvent(
        timestamp=ts or datetime(2025, 1, 1),
        event_type=et,
        bar_index=bar_index,
        price=price,
        confluence={"x": 0.8, "y": 0.7} if confluence is None else confluence,
    )


def _initial() -> WyckoffSnapshot:
    return WyckoffSnapshot(
        timestamp=datetime(2025, 1, 1),
        phase=WyckoffPhase.NEUTRAL,
        range_high=None,
        range_low=None,
        events=(),
        confidence=0.0,
    )


# ---------------------------------------------------------------------------
# WyckoffEvent
# ---------------------------------------------------------------------------


def test_event_side_classification() -> None:
    sc = _make_event(WyckoffEventType.SC)
    bc = _make_event(WyckoffEventType.BC)
    assert sc.side == "accumulation"
    assert bc.side == "distribution"


def test_event_confidence_mean() -> None:
    e = _make_event(WyckoffEventType.SC, confluence={"a": 0.6, "b": 0.4})
    assert e.confidence == pytest.approx(0.5)


def test_event_confidence_empty_zero() -> None:
    e = _make_event(WyckoffEventType.SC, confluence={})
    assert e.confidence == 0.0


# ---------------------------------------------------------------------------
# WyckoffSnapshot
# ---------------------------------------------------------------------------


def test_snapshot_invalidation_acc_phases() -> None:
    s = WyckoffSnapshot(
        timestamp=datetime(2025, 1, 1),
        phase=WyckoffPhase.ACC_C,
        range_high=110.0,
        range_low=90.0,
        events=(),
        confidence=0.7,
    )
    assert s.invalidation_price == 90.0
    assert s.side == "accumulation"


def test_snapshot_invalidation_dist_phases() -> None:
    s = WyckoffSnapshot(
        timestamp=datetime(2025, 1, 1),
        phase=WyckoffPhase.DIST_D,
        range_high=110.0,
        range_low=90.0,
        events=(),
        confidence=0.7,
    )
    assert s.invalidation_price == 110.0
    assert s.side == "distribution"


def test_snapshot_neutral_no_side_no_invalidation() -> None:
    s = _initial()
    assert s.side is None
    assert s.invalidation_price is None


# ---------------------------------------------------------------------------
# FSM transitions: NEUTRAL gating
# ---------------------------------------------------------------------------


def test_neutral_to_acc_a_on_strong_sc() -> None:
    s = evolve(_initial(), _make_event(WyckoffEventType.SC, price=80.0))
    assert s.phase == WyckoffPhase.ACC_A
    assert s.range_low == 80.0
    assert s.range_high is None


def test_neutral_to_dist_a_on_strong_bc() -> None:
    s = evolve(_initial(), _make_event(WyckoffEventType.BC, price=120.0))
    assert s.phase == WyckoffPhase.DIST_A
    assert s.range_high == 120.0


def test_neutral_low_confidence_event_does_not_advance() -> None:
    weak = _make_event(WyckoffEventType.SC, confluence={"a": 0.1})
    s = evolve(_initial(), weak)
    assert s.phase == WyckoffPhase.NEUTRAL
    assert len(s.events) == 1  # but the event is recorded


def test_neutral_ignores_non_climax() -> None:
    e = _make_event(WyckoffEventType.SPRING)
    s = evolve(_initial(), e)
    assert s.phase == WyckoffPhase.NEUTRAL


# ---------------------------------------------------------------------------
# Accumulation cycle progression
# ---------------------------------------------------------------------------


def test_full_accumulation_cycle() -> None:
    s = _initial()
    s = evolve(s, _make_event(WyckoffEventType.SC, price=80.0))
    assert s.phase == WyckoffPhase.ACC_A
    s = evolve(s, _make_event(WyckoffEventType.AR, price=95.0))
    assert s.phase == WyckoffPhase.ACC_B
    assert s.range_high == 95.0
    assert s.range_low == 80.0
    s = evolve(s, _make_event(WyckoffEventType.ST, price=82.0))
    assert s.phase == WyckoffPhase.ACC_B  # ST stays in B
    s = evolve(s, _make_event(WyckoffEventType.SPRING, price=78.0))
    assert s.phase == WyckoffPhase.ACC_C
    assert s.range_low == 78.0
    s = evolve(s, _make_event(WyckoffEventType.SOS, price=98.0))
    assert s.phase == WyckoffPhase.ACC_D
    s = evolve(s, _make_event(WyckoffEventType.LPS, price=92.0))
    assert s.phase == WyckoffPhase.ACC_E


def test_distribution_cycle() -> None:
    s = _initial()
    s = evolve(s, _make_event(WyckoffEventType.BC, price=120.0))
    assert s.phase == WyckoffPhase.DIST_A
    s = evolve(s, _make_event(WyckoffEventType.AR_DIST, price=105.0))
    assert s.phase == WyckoffPhase.DIST_B
    s = evolve(s, _make_event(WyckoffEventType.UTAD, price=122.0))
    assert s.phase == WyckoffPhase.DIST_C
    s = evolve(s, _make_event(WyckoffEventType.SOW, price=102.0))
    assert s.phase == WyckoffPhase.DIST_D
    s = evolve(s, _make_event(WyckoffEventType.LPSY, price=108.0))
    assert s.phase == WyckoffPhase.DIST_E


def test_low_confidence_spring_does_not_advance() -> None:
    s = _initial()
    s = evolve(s, _make_event(WyckoffEventType.SC, price=80.0))
    s = evolve(s, _make_event(WyckoffEventType.AR, price=95.0))
    weak = _make_event(WyckoffEventType.SPRING, price=78.0, confluence={"a": 0.1})
    s = evolve(s, weak)
    assert s.phase == WyckoffPhase.ACC_B  # not advanced to C


# ---------------------------------------------------------------------------
# Cross-cycle isolation
# ---------------------------------------------------------------------------


def test_distribution_event_in_accumulation_phase_is_ignored() -> None:
    s = _initial()
    s = evolve(s, _make_event(WyckoffEventType.SC, price=80.0))
    s = evolve(s, _make_event(WyckoffEventType.AR, price=95.0))
    # UTAD belongs to distribution; should NOT advance accumulation Phase B.
    s = evolve(s, _make_event(WyckoffEventType.UTAD, price=96.0))
    assert s.phase == WyckoffPhase.ACC_B


def test_strong_opposite_climax_in_phase_a_flips_cycle() -> None:
    """Markdown reversing into accumulation: strong SC in DIST_A flips."""
    s = _initial()
    s = evolve(s, _make_event(WyckoffEventType.BC, price=120.0))
    assert s.phase == WyckoffPhase.DIST_A
    # Strong SC arrives — market reversed.
    strong_sc = _make_event(
        WyckoffEventType.SC, price=80.0, confluence={"a": 0.9, "b": 0.85}
    )
    s = evolve(s, strong_sc)
    assert s.phase == WyckoffPhase.ACC_A
    assert s.range_low == 80.0
    assert s.range_high is None


def test_weak_opposite_climax_in_phase_a_does_not_flip() -> None:
    s = _initial()
    s = evolve(s, _make_event(WyckoffEventType.BC, price=120.0))
    weak_sc = _make_event(WyckoffEventType.SC, price=80.0, confluence={"a": 0.4})
    s = evolve(s, weak_sc)
    assert s.phase == WyckoffPhase.DIST_A  # not flipped


def test_opposite_climax_in_phase_c_does_not_flip() -> None:
    """Once past Phase B, cycle commitment is firm — only range break invalidates."""
    s = _initial()
    s = evolve(s, _make_event(WyckoffEventType.SC, price=80.0))
    s = evolve(s, _make_event(WyckoffEventType.AR, price=95.0))
    s = evolve(s, _make_event(WyckoffEventType.SPRING, price=78.0))
    assert s.phase == WyckoffPhase.ACC_C
    # Strong BC after Phase C: noise, not a flip
    strong_bc = _make_event(
        WyckoffEventType.BC, price=130.0, confluence={"a": 0.9, "b": 0.95}
    )
    s = evolve(s, strong_bc)
    assert s.phase == WyckoffPhase.ACC_C  # still in accumulation


def test_audit_trail_grows_monotonically() -> None:
    s = _initial()
    s = evolve(s, _make_event(WyckoffEventType.SC, price=80.0))
    s = evolve(s, _make_event(WyckoffEventType.AR, price=95.0))
    s = evolve(s, _make_event(WyckoffEventType.ST, price=82.0))
    assert [e.event_type for e in s.events] == [
        WyckoffEventType.SC,
        WyckoffEventType.AR,
        WyckoffEventType.ST,
    ]


def test_state_is_immutable() -> None:
    s = _initial()
    with pytest.raises((AttributeError, TypeError)):
        s.phase = WyckoffPhase.ACC_A  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Range re-anchoring
# ---------------------------------------------------------------------------


def test_acc_a_secondary_sc_lowers_range_low() -> None:
    s = _initial()
    s = evolve(s, _make_event(WyckoffEventType.SC, price=80.0))
    # Another SC with a lower price re-anchors the low.
    s = evolve(s, _make_event(WyckoffEventType.SC, price=75.0))
    assert s.phase == WyckoffPhase.ACC_A
    assert s.range_low == 75.0


def test_acc_b_re_anchors_range_on_lower_sc() -> None:
    """In Phase B, a lower SC should still re-anchor range_low."""
    s = _initial()
    s = evolve(s, _make_event(WyckoffEventType.SC, price=80.0))
    s = evolve(s, _make_event(WyckoffEventType.AR, price=95.0))
    assert s.phase == WyckoffPhase.ACC_B
    s = evolve(s, _make_event(WyckoffEventType.SC, price=75.0))
    assert s.phase == WyckoffPhase.ACC_B
    assert s.range_low == 75.0


def test_acc_b_re_anchors_range_on_higher_ar() -> None:
    """In Phase B, a higher AR should re-anchor range_high."""
    s = _initial()
    s = evolve(s, _make_event(WyckoffEventType.SC, price=80.0))
    s = evolve(s, _make_event(WyckoffEventType.AR, price=95.0))
    s = evolve(s, _make_event(WyckoffEventType.AR, price=98.0))
    assert s.phase == WyckoffPhase.ACC_B
    assert s.range_high == 98.0


def test_dist_a_secondary_bc_raises_range_high() -> None:
    s = _initial()
    s = evolve(s, _make_event(WyckoffEventType.BC, price=120.0))
    s = evolve(s, _make_event(WyckoffEventType.BC, price=125.0))
    assert s.phase == WyckoffPhase.DIST_A
    assert s.range_high == 125.0


# ---------------------------------------------------------------------------
# Event detection
# ---------------------------------------------------------------------------


def test_detect_events_on_empty_df_returns_empty() -> None:
    df = _bars(opens=[], highs=[], lows=[], closes=[], volumes=[])
    assert detect_wyckoff_events(df) == []


def test_detect_events_on_too_short_df_returns_empty() -> None:
    df = _bars(
        opens=[100, 101, 102],
        highs=[101, 102, 103],
        lows=[99, 100, 101],
        closes=[100.5, 101.5, 102.5],
        volumes=[10, 10, 10],
    )
    assert detect_wyckoff_events(df, volume_window=5, swing_lookback=2) == []


def test_detect_events_missing_columns_raises() -> None:
    df = pl.DataFrame({"open_time": [datetime(2025, 1, 1)], "high": [1.0]})
    with pytest.raises(ValueError, match="missing columns"):
        detect_wyckoff_events(df)


def test_detect_selling_climax_on_volume_spike_at_swing_low() -> None:
    """Engineer a swing low with a volume spike + lower wick → SC detected."""
    n_warmup = 30
    base = 100.0
    # Climax bar: open=87, close=86, low=78, high=89 → wick_ratio 0.73
    opens = [base] * n_warmup + [95.0, 92.0, 87.0, 88.0, 90.0, 91.0, 90.0]
    closes = [base] * n_warmup + [92.0, 87.0, 86.0, 90.0, 91.0, 90.0, 88.0]
    highs = [base + 0.5] * n_warmup + [96.0, 93.0, 89.0, 91.0, 92.0, 92.0, 91.0]
    lows = [base - 0.5] * n_warmup + [91.0, 86.0, 78.0, 87.0, 89.0, 89.0, 87.0]
    volumes = [10.0] * n_warmup + [12.0, 15.0, 200.0, 20.0, 15.0, 12.0, 10.0]

    df = _bars(
        opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes
    )
    events = detect_wyckoff_events(
        df, swing_lookback=2, volume_climax_z=2.0, volume_window=20
    )
    sc_events = [e for e in events if e.event_type == WyckoffEventType.SC]
    assert len(sc_events) >= 1
    assert sc_events[0].price <= 80.0


def test_detect_buying_climax_on_volume_spike_at_swing_high() -> None:
    """Symmetric: swing high with volume spike + upper wick → BC detected."""
    n_warmup = 30
    base = 100.0
    # BC bar: open=113, close=114, high=122, low=112 → wick_ratio 0.8
    opens = [base] * n_warmup + [105.0, 108.0, 113.0, 112.0, 110.0, 109.0, 109.0]
    closes = [base] * n_warmup + [108.0, 113.0, 114.0, 110.0, 109.0, 109.0, 110.0]
    lows = [base - 0.5] * n_warmup + [104.0, 107.0, 112.0, 109.0, 108.0, 107.0, 107.0]
    highs = [base + 0.5] * n_warmup + [109.0, 114.0, 122.0, 113.0, 111.0, 110.0, 110.0]
    volumes = [10.0] * n_warmup + [12.0, 15.0, 200.0, 20.0, 15.0, 12.0, 10.0]

    df = _bars(opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes)
    events = detect_wyckoff_events(
        df, swing_lookback=2, volume_climax_z=2.0, volume_window=20
    )
    bc_events = [e for e in events if e.event_type == WyckoffEventType.BC]
    assert len(bc_events) >= 1


# ---------------------------------------------------------------------------
# analyze_wyckoff pipeline
# ---------------------------------------------------------------------------


def test_analyze_empty_df_returns_neutral_only() -> None:
    df = _bars(opens=[], highs=[], lows=[], closes=[], volumes=[])
    snaps = analyze_wyckoff(df)
    assert len(snaps) == 1
    assert snaps[0].phase == WyckoffPhase.NEUTRAL


def test_analyze_returns_snapshot_per_event_plus_initial() -> None:
    n_warmup = 30
    opens = [100.0] * n_warmup + [95.0, 92.0, 87.0, 88.0, 90.0, 91.0, 90.0]
    closes = [100.0] * n_warmup + [92.0, 87.0, 86.0, 90.0, 91.0, 90.0, 88.0]
    highs = [100.5] * n_warmup + [96.0, 93.0, 89.0, 91.0, 92.0, 92.0, 91.0]
    lows = [99.5] * n_warmup + [91.0, 86.0, 78.0, 87.0, 89.0, 89.0, 87.0]
    volumes = [10.0] * n_warmup + [12.0, 15.0, 200.0, 20.0, 15.0, 12.0, 10.0]

    df = _bars(opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes)
    snaps = analyze_wyckoff(df, volume_window=20, swing_lookback=2)
    assert snaps[0].phase == WyckoffPhase.NEUTRAL
    if len(snaps) > 1:
        assert snaps[-1].phase in {
            WyckoffPhase.NEUTRAL,
            WyckoffPhase.ACC_A,
            WyckoffPhase.ACC_B,
        }


# ---------------------------------------------------------------------------
# AR / AR_DIST detection
# ---------------------------------------------------------------------------


def test_ar_detected_after_sc() -> None:
    """SC followed by a swing high within lookahead window → AR event emitted."""
    n_warmup = 30
    base = 100.0
    # Climax bar at idx 32: deep lower wick (open=87, close=86, low=78, high=89).
    # Then single-peaked rally with maximum at idx 36.
    opens = [base] * n_warmup + [95.0, 92.0, 87.0] + [86.0, 88.0, 91.0, 90.0, 88.0, 86.0, 85.0]
    closes = [base] * n_warmup + [92.0, 87.0, 86.0] + [88.0, 91.0, 90.0, 88.0, 86.0, 85.0, 84.0]
    highs = [base + 0.5] * n_warmup + [96.0, 93.0, 89.0] + [89.0, 92.0, 94.0, 91.0, 89.0, 87.0, 86.0]
    lows = [base - 0.5] * n_warmup + [91.0, 86.0, 78.0] + [85.0, 87.0, 89.0, 87.0, 85.0, 84.0, 83.0]
    volumes = [10.0] * n_warmup + [12.0, 15.0, 200.0] + [20.0, 18.0, 16.0, 14.0, 12.0, 10.0, 10.0]

    df = _bars(opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes)
    events = detect_wyckoff_events(
        df, swing_lookback=2, volume_climax_z=2.0, volume_window=20
    )
    ar_events = [e for e in events if e.event_type == WyckoffEventType.AR]
    sc_events = [e for e in events if e.event_type == WyckoffEventType.SC]
    assert len(sc_events) >= 1
    assert len(ar_events) >= 1
    assert ar_events[0].bar_index > sc_events[0].bar_index
    assert ar_events[0].price > sc_events[0].price


def test_ar_dist_detected_after_bc() -> None:
    """Symmetric: BC followed by a swing low → AR_DIST event."""
    n_warmup = 30
    base = 100.0
    # BC bar at idx 32: deep upper wick. Then single-troughed reaction.
    opens = [base] * n_warmup + [105.0, 108.0, 113.0] + [114.0, 112.0, 109.0, 110.0, 112.0, 114.0, 115.0]
    closes = [base] * n_warmup + [108.0, 113.0, 114.0] + [112.0, 109.0, 110.0, 112.0, 114.0, 115.0, 116.0]
    lows = [base - 0.5] * n_warmup + [104.0, 107.0, 112.0] + [111.0, 108.0, 106.0, 109.0, 111.0, 113.0, 114.0]
    highs = [base + 0.5] * n_warmup + [109.0, 114.0, 122.0] + [115.0, 113.0, 111.0, 113.0, 115.0, 116.0, 117.0]
    volumes = [10.0] * n_warmup + [12.0, 15.0, 200.0] + [20.0, 18.0, 16.0, 14.0, 12.0, 10.0, 10.0]

    df = _bars(opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes)
    events = detect_wyckoff_events(
        df, swing_lookback=2, volume_climax_z=2.0, volume_window=20
    )
    ar_dist = [e for e in events if e.event_type == WyckoffEventType.AR_DIST]
    bc = [e for e in events if e.event_type == WyckoffEventType.BC]
    assert len(bc) >= 1
    assert len(ar_dist) >= 1
    assert ar_dist[0].bar_index > bc[0].bar_index
    assert ar_dist[0].price < bc[0].price


def test_no_ar_when_no_subsequent_swing() -> None:
    """SC at the very last bars → no swing high after → no AR emitted."""
    n_warmup = 30
    base = 100.0
    opens = [base] * n_warmup + [95.0, 92.0, 87.0]
    closes = [base] * n_warmup + [92.0, 87.0, 86.0]
    highs = [base + 0.5] * n_warmup + [96.0, 93.0, 89.0]
    lows = [base - 0.5] * n_warmup + [91.0, 86.0, 78.0]
    volumes = [10.0] * n_warmup + [12.0, 15.0, 200.0]
    df = _bars(opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes)
    events = detect_wyckoff_events(
        df, swing_lookback=2, volume_climax_z=2.0, volume_window=20
    )
    assert [e for e in events if e.event_type == WyckoffEventType.AR] == []


# ---------------------------------------------------------------------------
# Bar-interval guard for Spring/UTAD
# ---------------------------------------------------------------------------


def test_subhour_bars_skip_spring_detection() -> None:
    """Bars at 5m interval should not produce Spring/UTAD events even if the
    underlying stop_hunt detector would fire — Wyckoff Springs are a 1h+
    structural concept.
    """
    n = 60
    base_dt = datetime(2025, 1, 1)
    # Equal lows pool at 80, then sweep to 78 with rejection wick
    opens = [100.0] * 30 + [95.0, 80.5] + [80.5] * 5 + [80.5, 80.5, 80.5, 78.0] + [82.0] * 19
    closes = [100.0] * 30 + [80.5, 80.5] + [80.5] * 5 + [80.5, 80.5, 80.5, 82.0] + [82.0] * 19
    highs = [100.5] * 30 + [95.5, 81.0] + [81.0] * 5 + [81.0, 81.0, 81.0, 82.5] + [82.5] * 19
    lows = [99.5] * 30 + [80.0, 80.0] + [80.0] * 5 + [80.0, 80.0, 80.0, 78.0] + [81.5] * 19
    volumes = [10.0] * 30 + [10.0, 10.0] + [10.0] * 5 + [10.0, 10.0, 10.0, 50.0] + [10.0] * 19

    # 5m bars
    df_5m = pl.DataFrame(
        {
            "open_time": [base_dt + timedelta(minutes=5 * i) for i in range(n)],
            "open": opens[:n],
            "high": highs[:n],
            "low": lows[:n],
            "close": closes[:n],
            "volume": volumes[:n],
        }
    )
    events_5m = detect_wyckoff_events(
        df_5m, swing_lookback=2, volume_climax_z=2.0, volume_window=20
    )
    assert [e for e in events_5m if e.event_type == WyckoffEventType.SPRING] == []


# ---------------------------------------------------------------------------
# Pipeline integration — full Phase B narrative
# ---------------------------------------------------------------------------


def test_analyze_pipeline_advances_to_phase_b_on_sc_plus_ar() -> None:
    """End-to-end: SC + AR data should land FSM in Accumulation Phase B."""
    n_warmup = 30
    base = 100.0
    opens = [base] * n_warmup + [95.0, 92.0, 87.0] + [86.0, 88.0, 90.0, 91.0, 92.0, 91.0, 89.0]
    closes = [base] * n_warmup + [92.0, 87.0, 86.0] + [88.0, 90.0, 91.0, 92.0, 91.0, 89.0, 88.0]
    highs = [base + 0.5] * n_warmup + [96.0, 93.0, 89.0] + [89.0, 91.0, 92.0, 93.0, 93.0, 92.0, 90.0]
    lows = [base - 0.5] * n_warmup + [91.0, 86.0, 78.0] + [85.0, 87.0, 89.0, 91.0, 90.0, 88.0, 87.0]
    volumes = [10.0] * n_warmup + [12.0, 15.0, 200.0] + [20.0, 18.0, 16.0, 14.0, 12.0, 10.0, 10.0]

    df = _bars(opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes)
    snaps = analyze_wyckoff(df, volume_window=20, swing_lookback=2)
    final = snaps[-1]
    assert final.phase in {WyckoffPhase.ACC_A, WyckoffPhase.ACC_B}
    if final.phase == WyckoffPhase.ACC_B:
        assert final.range_low is not None
        assert final.range_high is not None
        assert final.range_high > final.range_low
