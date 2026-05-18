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
    opens = [base] * n_warmup + [95.0, 92.0, 88.0, 95.0, 95.0, 95.0, 95.0]
    closes = [base] * n_warmup + [92.0, 88.0, 92.0, 95.0, 95.0, 95.0, 95.0]
    highs = [base + 0.5] * n_warmup + [96.0, 93.0, 93.0, 96.0, 96.0, 96.0, 96.0]
    # Climax bar at idx n_warmup+2: huge lower wick down to 80, close 92
    lows = [base - 0.5] * n_warmup + [91.0, 86.0, 80.0, 94.0, 94.0, 94.0, 94.0]
    volumes = [10.0] * n_warmup + [12.0, 15.0, 200.0, 20.0, 15.0, 12.0, 10.0]

    df = _bars(
        opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes
    )
    events = detect_wyckoff_events(
        df, swing_lookback=2, volume_climax_z=2.0, volume_window=20
    )
    sc_events = [e for e in events if e.event_type == WyckoffEventType.SC]
    assert len(sc_events) >= 1
    assert sc_events[0].price <= 82.0  # close to the low extreme


def test_detect_buying_climax_on_volume_spike_at_swing_high() -> None:
    """Symmetric: swing high with volume spike + upper wick → BC detected."""
    n_warmup = 30
    base = 100.0
    opens = [base] * n_warmup + [105.0, 108.0, 112.0, 105.0, 105.0, 105.0, 105.0]
    closes = [base] * n_warmup + [108.0, 112.0, 108.0, 105.0, 105.0, 105.0, 105.0]
    lows = [base - 0.5] * n_warmup + [104.0, 107.0, 107.0, 104.0, 104.0, 104.0, 104.0]
    highs = [base + 0.5] * n_warmup + [109.0, 114.0, 120.0, 106.0, 106.0, 106.0, 106.0]
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
    opens = [100.0] * n_warmup + [95.0, 92.0, 88.0, 95.0, 95.0, 95.0, 95.0]
    closes = [100.0] * n_warmup + [92.0, 88.0, 92.0, 95.0, 95.0, 95.0, 95.0]
    highs = [100.5] * n_warmup + [96.0, 93.0, 93.0, 96.0, 96.0, 96.0, 96.0]
    lows = [99.5] * n_warmup + [91.0, 86.0, 80.0, 94.0, 94.0, 94.0, 94.0]
    volumes = [10.0] * n_warmup + [12.0, 15.0, 200.0, 20.0, 15.0, 12.0, 10.0]

    df = _bars(opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes)
    snaps = analyze_wyckoff(df, volume_window=20, swing_lookback=2)
    # First snapshot is NEUTRAL; later snapshots reflect events.
    assert snaps[0].phase == WyckoffPhase.NEUTRAL
    # If at least one strong SC was detected, last state should be ACC_A.
    if len(snaps) > 1:
        assert snaps[-1].phase in {
            WyckoffPhase.NEUTRAL,
            WyckoffPhase.ACC_A,
            WyckoffPhase.ACC_B,
        }
