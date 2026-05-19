"""Tests for the context aggregation report."""

from __future__ import annotations

from datetime import datetime, timedelta

from pa_assistant.analysis.context import (
    FlowContext,
    FundingContext,
    LiquidityMap,
    Scorecard,
    StopHuntContext,
    TrendContext,
    WyckoffContext,
    ZoneContext,
    build_context_report,
    build_flow_context,
    build_funding_context,
    build_liquidity_map,
    build_scorecard,
    build_stop_hunt_context,
    build_trend_context,
    build_wyckoff_context,
    build_zone_context,
    render_text,
)
from pa_assistant.analysis.divergence import DivergenceEvent
from pa_assistant.analysis.liquidity import LiquidityLevel
from pa_assistant.analysis.stop_hunt import StopHunt
from pa_assistant.analysis.structure import StructureEvent
from pa_assistant.analysis.wyckoff import WyckoffPhase, WyckoffSnapshot
from pa_assistant.analysis.zones import FairValueGap, OrderBlock

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ts(hour: int = 0) -> datetime:
    return datetime(2025, 1, 1) + timedelta(hours=hour)


def _liquidity_level(
    *,
    price: float,
    side: str = "high",
    swept_at: datetime | None = None,
) -> LiquidityLevel:
    return LiquidityLevel(
        price=price,
        side=side,  # type: ignore[arg-type]
        touches=[_ts()],
        first_seen=_ts(),
        last_seen=_ts(1),
        spread_bps=2.0,
        swept_at=swept_at,
    )


def _stop_hunt(*, side: str = "low", confirmed: bool = True) -> StopHunt:
    return StopHunt(
        timestamp=_ts(10),
        side=side,  # type: ignore[arg-type]
        pool_price=77800.0,
        pool_touches=3,
        extreme=77500.0 if side == "low" else 78100.0,
        close=77900.0 if side == "low" else 77800.0,
        wick_ratio=0.78,
        volume_ratio=2.5,
        confirmed=confirmed,
    )


def _wyckoff_snap(
    phase: WyckoffPhase,
    range_low: float | None = None,
    range_high: float | None = None,
    confidence: float = 0.7,
) -> WyckoffSnapshot:
    return WyckoffSnapshot(
        timestamp=_ts(20),
        phase=phase,
        range_low=range_low,
        range_high=range_high,
        events=(),
        confidence=confidence,
    )


def _div(
    side: str = "bullish",
    indicator: str = "cvd",
    strength: float = 0.8,
    swing_price: float = 76014.0,
) -> DivergenceEvent:
    return DivergenceEvent(
        timestamp=_ts(15),
        side=side,  # type: ignore[arg-type]
        indicator=indicator,  # type: ignore[arg-type]
        swing_price=swing_price,
        prior_swing_price=76800.0,
        prior_swing_time=_ts(10),
        indicator_value=100.0,
        prior_indicator_value=50.0,
        strength=strength,
    )


# ---------------------------------------------------------------------------
# Sub-context builders
# ---------------------------------------------------------------------------


def test_trend_alignment_aligned_bull() -> None:
    ctx = build_trend_context(
        working_timeframe="1h",
        working_trend="up",
        working_events=[],
        htf_timeframe="4h",
        htf_trend="up",
        htf_events=[],
    )
    assert ctx.alignment == "aligned_bull"


def test_trend_alignment_htf_bear_ltf_bull() -> None:
    ctx = build_trend_context(
        working_timeframe="1h",
        working_trend="up",
        working_events=[],
        htf_timeframe="1d",
        htf_trend="down",
        htf_events=[],
    )
    assert ctx.alignment == "htf_bear_ltf_bull"


def test_trend_alignment_neutral_when_either_none() -> None:
    ctx = build_trend_context(
        working_timeframe="1h",
        working_trend="none",
        working_events=[],
    )
    assert ctx.alignment == "neutral"


def test_liquidity_map_splits_above_below_and_filters_swept() -> None:
    levels = [
        _liquidity_level(price=80000.0, side="high"),
        _liquidity_level(price=75000.0, side="low"),
        _liquidity_level(price=78000.0, side="high", swept_at=_ts(5)),
    ]
    lm = build_liquidity_map(levels, current_price=77000.0)
    assert len(lm.above) == 1
    assert len(lm.below) == 1
    assert lm.above[0].price == 80000.0
    assert lm.below[0].price == 75000.0
    # The swept high pool surfaces as most_recent_swept
    assert lm.most_recent_swept is not None
    assert lm.most_recent_swept.price == 78000.0


def test_liquidity_map_handles_empty_input() -> None:
    lm = build_liquidity_map([], current_price=77000.0)
    assert lm.above == ()
    assert lm.below == ()
    assert lm.most_recent_swept is None


def test_zone_context_filters_to_active() -> None:
    obs = [
        OrderBlock(
            timestamp=_ts(1),
            direction="bearish",
            top=80000.0,
            bottom=79500.0,
            wick_top=80100.0,
            wick_bottom=79400.0,
            triggered_by=_ts(2),
            mitigated_at=None,  # ACTIVE
        ),
        OrderBlock(
            timestamp=_ts(3),
            direction="bullish",
            top=75000.0,
            bottom=74500.0,
            wick_top=75100.0,
            wick_bottom=74400.0,
            triggered_by=_ts(4),
            mitigated_at=_ts(5),  # MITIGATED — should be filtered out
        ),
    ]
    fvgs = [
        FairValueGap(
            timestamp=_ts(7),
            direction="bearish",
            top=78000.0,
            bottom=77800.0,
            mitigated_at=None,  # ACTIVE
        ),
    ]
    zc = build_zone_context(obs, fvgs, current_price=77000.0)
    assert len(zc.active_order_blocks) == 1
    assert zc.active_order_blocks[0].direction == "bearish"
    assert len(zc.active_fvgs) == 1
    # Nearest above 77000 should be the FVG (mid 77900) — closer than OB (mid 79750)
    assert zc.nearest_above is zc.active_fvgs[0]
    assert zc.nearest_below is None  # all active zones are above price


def test_flow_context_cvd_trend_classification() -> None:
    ctx = build_flow_context(
        cvd_series=[100.0] * 10 + [200.0],  # rising
        vwap=78000.0,
        current_price=77000.0,
        poc=77500.0,
        divergences=[],
        cvd_lookback=8,
    )
    assert ctx.cvd_trend == "up"
    assert ctx.cvd_recent_change > 0
    assert ctx.vwap_distance_pct is not None
    assert ctx.vwap_distance_pct < 0  # price below vwap


def test_flow_context_handles_short_cvd_series() -> None:
    ctx = build_flow_context(
        cvd_series=[1.0, 2.0],
        vwap=None,
        current_price=77000.0,
        poc=None,
        divergences=[],
        cvd_lookback=8,
    )
    assert ctx.cvd_trend == "none"
    assert ctx.cvd_recent_change == 0.0


def test_stop_hunt_bias_bullish_on_low_sweep() -> None:
    ctx = build_stop_hunt_context([_stop_hunt(side="low", confirmed=True)])
    assert ctx.bias_implication == "bullish"
    assert ctx.most_recent is not None


def test_stop_hunt_bias_bearish_on_high_sweep() -> None:
    ctx = build_stop_hunt_context([_stop_hunt(side="high", confirmed=True)])
    assert ctx.bias_implication == "bearish"


def test_stop_hunt_skips_unconfirmed() -> None:
    ctx = build_stop_hunt_context([_stop_hunt(confirmed=False)])
    assert ctx.most_recent is None
    assert ctx.bias_implication == "neutral"


def test_funding_context_computes_24h_change() -> None:
    ctx = build_funding_context(oi=104000.0, oi_24h_ago=100000.0, funding_rate=-0.0001)
    assert ctx.oi_change_24h_pct is not None
    assert ctx.oi_change_24h_pct == 0.04
    assert ctx.funding_rate == -0.0001


def test_funding_context_handles_missing_data() -> None:
    ctx = build_funding_context(oi=None, oi_24h_ago=None, funding_rate=None)
    assert ctx.oi is None
    assert ctx.oi_change_24h_pct is None


def test_wyckoff_context_phase_b_watch_hint() -> None:
    snap = _wyckoff_snap(WyckoffPhase.ACC_B, range_low=76000.0, range_high=78000.0)
    ctx = build_wyckoff_context(snap)
    assert "Spring" in ctx.next_watch
    assert "$76,000" in ctx.next_watch
    assert "$78,000" in ctx.next_watch


def test_wyckoff_context_neutral_hint() -> None:
    snap = _wyckoff_snap(WyckoffPhase.NEUTRAL)
    ctx = build_wyckoff_context(snap)
    assert "climax" in ctx.next_watch.lower()


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------


def _empty_subcontexts() -> tuple[
    TrendContext,
    WyckoffContext,
    LiquidityMap,
    ZoneContext,
    FlowContext,
    StopHuntContext,
    FundingContext,
]:
    """Build a 'no signal' baseline of subcontexts for scorecard tests."""
    return (
        build_trend_context(
            working_timeframe="1h",
            working_trend="none",
            working_events=[],
        ),
        build_wyckoff_context(_wyckoff_snap(WyckoffPhase.NEUTRAL)),
        build_liquidity_map([], current_price=77000.0),
        build_zone_context([], [], current_price=77000.0),
        build_flow_context(
            cvd_series=[],
            vwap=None,
            current_price=77000.0,
            poc=None,
            divergences=[],
        ),
        build_stop_hunt_context([]),
        build_funding_context(oi=None, oi_24h_ago=None, funding_rate=None),
    )


def test_scorecard_neutral_baseline_has_no_factors() -> None:
    trend, wyckoff, liq, zones, flow, hunts, funding = _empty_subcontexts()
    sc = build_scorecard(
        trend=trend,
        wyckoff=wyckoff,
        liquidity=liq,
        zones=zones,
        flow=flow,
        stop_hunts=hunts,
        funding=funding,
    )
    assert sc.bullish_factors == ()
    assert sc.bearish_factors == ()
    assert sc.net_bias == "neutral"


def test_scorecard_acc_phase_b_emits_bullish_factor() -> None:
    trend, _, liq, zones, flow, hunts, funding = _empty_subcontexts()
    wyckoff = build_wyckoff_context(
        _wyckoff_snap(WyckoffPhase.ACC_B, range_low=76000.0, range_high=78000.0)
    )
    sc = build_scorecard(
        trend=trend,
        wyckoff=wyckoff,
        liquidity=liq,
        zones=zones,
        flow=flow,
        stop_hunts=hunts,
        funding=funding,
    )
    assert any("Phase B" in f for f in sc.bullish_factors)


def test_scorecard_low_side_stop_hunt_emits_bullish_factor() -> None:
    trend, wyckoff, liq, zones, flow, _, funding = _empty_subcontexts()
    hunts = build_stop_hunt_context([_stop_hunt(side="low", confirmed=True)])
    sc = build_scorecard(
        trend=trend,
        wyckoff=wyckoff,
        liquidity=liq,
        zones=zones,
        flow=flow,
        stop_hunts=hunts,
        funding=funding,
    )
    assert any("Spring" in f for f in sc.bullish_factors)


def test_scorecard_extreme_negative_funding_emits_bullish_factor() -> None:
    trend, wyckoff, liq, zones, flow, hunts, _ = _empty_subcontexts()
    funding = build_funding_context(oi=None, oi_24h_ago=None, funding_rate=-0.0005)
    sc = build_scorecard(
        trend=trend,
        wyckoff=wyckoff,
        liquidity=liq,
        zones=zones,
        flow=flow,
        stop_hunts=hunts,
        funding=funding,
    )
    assert any("Funding" in f and "contrarian" in f for f in sc.bullish_factors)


def test_scorecard_strong_divergence_above_threshold_counts() -> None:
    trend, wyckoff, liq, zones, _, hunts, funding = _empty_subcontexts()
    flow = build_flow_context(
        cvd_series=[],
        vwap=None,
        current_price=77000.0,
        poc=None,
        divergences=[_div(strength=0.9), _div(strength=0.1)],
    )
    sc = build_scorecard(
        trend=trend,
        wyckoff=wyckoff,
        liquidity=liq,
        zones=zones,
        flow=flow,
        stop_hunts=hunts,
        funding=funding,
        min_divergence_strength=0.4,
    )
    # Only the 0.9 strength divergence should count (0.1 below threshold)
    div_factors = [f for f in sc.bullish_factors if "divergence" in f]
    assert len(div_factors) == 1
    assert "90%" in div_factors[0]


def test_scorecard_net_bias_majority_with_margin() -> None:
    sc = Scorecard(
        bullish_factors=("a", "b", "c"),
        bearish_factors=("x",),
    )
    assert sc.net_bias == "bullish"  # 3 vs 1, diff >= 2


def test_scorecard_net_bias_neutral_on_thin_margin() -> None:
    sc = Scorecard(
        bullish_factors=("a", "b"),
        bearish_factors=("x",),
    )
    assert sc.net_bias == "neutral"  # 2 vs 1, diff < 2


# ---------------------------------------------------------------------------
# Umbrella build_context_report
# ---------------------------------------------------------------------------


def test_build_context_report_sets_long_invalidation_for_accumulation() -> None:
    trend, _, liq, zones, flow, hunts, funding = _empty_subcontexts()
    wyckoff = build_wyckoff_context(
        _wyckoff_snap(WyckoffPhase.ACC_C, range_low=76000.0, range_high=78000.0)
    )
    report = build_context_report(
        timestamp=_ts(20),
        symbol="BTCUSDT",
        timeframe="1h",
        current_price=77000.0,
        trend=trend,
        wyckoff=wyckoff,
        liquidity=liq,
        zones=zones,
        flow=flow,
        stop_hunts=hunts,
        funding=funding,
    )
    assert report.invalidation_long == 76000.0
    assert report.invalidation_short is None


def test_build_context_report_picks_nearest_magnet() -> None:
    trend, wyckoff, _, zones, flow, hunts, funding = _empty_subcontexts()
    liq = build_liquidity_map(
        [
            _liquidity_level(price=78000.0, side="high"),  # 1000 above
            _liquidity_level(price=76500.0, side="low"),  # 500 below — nearer
        ],
        current_price=77000.0,
    )
    report = build_context_report(
        timestamp=_ts(),
        symbol="BTCUSDT",
        timeframe="1h",
        current_price=77000.0,
        trend=trend,
        wyckoff=wyckoff,
        liquidity=liq,
        zones=zones,
        flow=flow,
        stop_hunts=hunts,
        funding=funding,
    )
    assert report.nearest_magnet == 76500.0


# ---------------------------------------------------------------------------
# Renderer (smoke tests)
# ---------------------------------------------------------------------------


def test_render_text_does_not_crash_on_minimal_report() -> None:
    trend, wyckoff, liq, zones, flow, hunts, funding = _empty_subcontexts()
    report = build_context_report(
        timestamp=_ts(),
        symbol="BTCUSDT",
        timeframe="1h",
        current_price=77000.0,
        trend=trend,
        wyckoff=wyckoff,
        liquidity=liq,
        zones=zones,
        flow=flow,
        stop_hunts=hunts,
        funding=funding,
    )
    text = render_text(report)
    assert "BTCUSDT" in text
    assert "Trend" in text
    assert "Wyckoff" in text
    assert "Scorecard" in text


def test_render_text_includes_factors_when_present() -> None:
    trend, _, liq, zones, flow, hunts, funding = _empty_subcontexts()
    wyckoff = build_wyckoff_context(
        _wyckoff_snap(WyckoffPhase.ACC_B, range_low=76000.0, range_high=78000.0)
    )
    report = build_context_report(
        timestamp=_ts(),
        symbol="BTCUSDT",
        timeframe="1h",
        current_price=77000.0,
        trend=trend,
        wyckoff=wyckoff,
        liquidity=liq,
        zones=zones,
        flow=flow,
        stop_hunts=hunts,
        funding=funding,
    )
    text = render_text(report)
    assert "Phase B" in text
    assert "Long invalidation" in text
    assert "76,000" in text


def test_immutability_of_context_report() -> None:
    trend, wyckoff, liq, zones, flow, hunts, funding = _empty_subcontexts()
    report = build_context_report(
        timestamp=_ts(),
        symbol="BTCUSDT",
        timeframe="1h",
        current_price=77000.0,
        trend=trend,
        wyckoff=wyckoff,
        liquidity=liq,
        zones=zones,
        flow=flow,
        stop_hunts=hunts,
        funding=funding,
    )
    # Frozen slots dataclass should reject attribute assignment
    try:
        report.symbol = "ETH"  # type: ignore[misc]
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("ContextReport should be immutable")


# Suppress mypy warnings about unused locals in fixtures — keep imports honored
_ = StructureEvent
