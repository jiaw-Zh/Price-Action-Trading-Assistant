"""Context aggregation: synthesize all analysis modules into a single report.

This is the system's headline deliverable. Individual analysis modules
each answer one question (where's the structure? where's the liquidity?
what phase is Wyckoff in?). This module composes them into one coherent
"market context report" that a trader can read end-to-end and use to
make a decision.

Design
------

* **Pure functions** — orchestration only. No DuckDB, no HTTP. Caller
  feeds in DataFrames + already-fetched scalars.
* **Composable** — each sub-context is built by a small helper that
  takes only what it needs. The aggregator wires them together.
* **Renderable** — separate ``render_text`` / ``render_markdown`` so
  the data structure can drive multiple output formats (terminal, file,
  messaging app).
* **Frozen dataclasses** — every output is immutable; serializable.

Layout
------

7 sub-contexts then the umbrella report:

* :class:`TrendContext`     — HTF / working-TF trend alignment
* :class:`WyckoffContext`   — current FSM state + next-watch hint
* :class:`LiquidityMap`     — pools above / below price, distances
* :class:`ZoneContext`      — active Order Blocks + FVGs
* :class:`FlowContext`      — CVD trend, VWAP, POC, recent divergences
* :class:`StopHuntContext`  — recent confirmed sweeps, bias implication
* :class:`FundingContext`   — OI snapshot, weighted funding rate
* :class:`Scorecard`        — bullish / bearish factor lists + net bias
* :class:`ContextReport`    — the umbrella; everything above + key levels
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pa_assistant.analysis.divergence import DivergenceEvent
from pa_assistant.analysis.liquidity import LiquidityLevel
from pa_assistant.analysis.stop_hunt import StopHunt
from pa_assistant.analysis.structure import StructureEvent, Trend
from pa_assistant.analysis.wyckoff import WyckoffSnapshot
from pa_assistant.analysis.zones import FairValueGap, OrderBlock

Bias = Literal["bullish", "bearish", "neutral"]
TrendAlignment = Literal[
    "aligned_bull",  # HTF up + working up
    "aligned_bear",  # HTF down + working down
    "htf_bear_ltf_bull",  # HTF down, working showing reversal
    "htf_bull_ltf_bear",  # HTF up, working showing pullback / reversal
    "neutral",  # no clear trend on at least one side
]
Language = Literal["en", "zh"]


# ---------------------------------------------------------------------------
# Translation tables (en -> zh) used by Chinese-language renderers
# ---------------------------------------------------------------------------

_ZH_BIAS: dict[str, str] = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
_ZH_DIRECTION: dict[str, str] = {"up": "上行", "down": "下行", "none": "无方向"}
_ZH_ALIGNMENT: dict[str, str] = {
    "aligned_bull": "双周期一致看多",
    "aligned_bear": "双周期一致看空",
    "htf_bear_ltf_bull": "HTF 看空 / 工作周期看多",
    "htf_bull_ltf_bear": "HTF 看多 / 工作周期看空",
    "neutral": "中性",
}
_ZH_INDICATOR: dict[str, str] = {"cvd": "CVD", "volume": "成交量", "oi": "OI"}
_ZH_PHASE: dict[str, str] = {
    "neutral": "中性",
    "accumulation_phase_a": "累积阶段 A",
    "accumulation_phase_b": "累积阶段 B",
    "accumulation_phase_c": "累积阶段 C",
    "accumulation_phase_d": "累积阶段 D",
    "accumulation_phase_e": "累积阶段 E",
    "distribution_phase_a": "派发阶段 A",
    "distribution_phase_b": "派发阶段 B",
    "distribution_phase_c": "派发阶段 C",
    "distribution_phase_d": "派发阶段 D",
    "distribution_phase_e": "派发阶段 E",
}


@dataclass(frozen=True, slots=True)
class TrendContext:
    """Higher-TF and working-TF trend alignment."""

    htf_timeframe: str | None
    htf_trend: Trend
    htf_last_event: StructureEvent | None
    working_timeframe: str
    working_trend: Trend
    working_last_event: StructureEvent | None
    alignment: TrendAlignment


@dataclass(frozen=True, slots=True)
class WyckoffContext:
    """Wraps the FSM snapshot with a forward-looking watch hint."""

    snapshot: WyckoffSnapshot
    next_watch: str  # e.g. "Spring below $76,666 OR breakout above $77,758"


@dataclass(frozen=True, slots=True)
class LiquidityMap:
    """Pools above / below current price, sorted by distance."""

    above: tuple[LiquidityLevel, ...]
    below: tuple[LiquidityLevel, ...]
    most_recent_swept: LiquidityLevel | None


@dataclass(frozen=True, slots=True)
class ZoneContext:
    """Active supply / demand zones."""

    active_order_blocks: tuple[OrderBlock, ...]
    active_fvgs: tuple[FairValueGap, ...]
    nearest_above: OrderBlock | FairValueGap | None
    nearest_below: OrderBlock | FairValueGap | None


@dataclass(frozen=True, slots=True)
class FlowContext:
    """Volume / order-flow snapshot."""

    cvd_trend: Trend  # over the last N bars
    cvd_recent_change: float
    vwap: float | None
    vwap_distance_pct: float | None  # (price - vwap) / price
    poc: float | None  # Point of Control over recent N bars
    recent_divergences: tuple[DivergenceEvent, ...]


@dataclass(frozen=True, slots=True)
class StopHuntContext:
    """Recent confirmed liquidity sweeps."""

    recent: tuple[StopHunt, ...]
    most_recent: StopHunt | None
    bias_implication: Bias  # bullish if recent low-side sweep confirmed, etc.


@dataclass(frozen=True, slots=True)
class FundingContext:
    """OI + weighted funding snapshot. Optional fields tolerate missing data."""

    oi: float | None
    oi_change_24h_pct: float | None
    funding_rate: float | None  # 5-source OI-weighted, latest


@dataclass(frozen=True, slots=True)
class Scorecard:
    """Human-readable bullish / bearish factor lists + computed net bias."""

    bullish_factors: tuple[str, ...]
    bearish_factors: tuple[str, ...]

    @property
    def net_bias(self) -> Bias:
        """Simple majority + margin rule.

        Bullish if ``len(bullish) - len(bearish) >= 2``; bearish if the
        reverse; else neutral. The +2 margin avoids flip-flopping on a
        one-factor difference.
        """
        diff = len(self.bullish_factors) - len(self.bearish_factors)
        if diff >= 2:
            return "bullish"
        if diff <= -2:
            return "bearish"
        return "neutral"


@dataclass(frozen=True, slots=True)
class ContextReport:
    """The umbrella report."""

    timestamp: datetime
    symbol: str
    timeframe: str
    current_price: float

    trend: TrendContext
    wyckoff: WyckoffContext
    liquidity: LiquidityMap
    zones: ZoneContext
    flow: FlowContext
    stop_hunts: StopHuntContext
    funding: FundingContext

    # Key actionable price levels
    invalidation_long: float | None  # close below this invalidates a long thesis
    invalidation_short: float | None  # close above this invalidates a short thesis
    nearest_magnet: float | None  # nearest unswept liquidity pool

    scorecard: Scorecard



# ---------------------------------------------------------------------------
# Sub-context builders
# ---------------------------------------------------------------------------


def _classify_alignment(htf: Trend, working: Trend) -> TrendAlignment:
    """Map (htf, working) trend pair into an alignment label."""
    if htf == "up" and working == "up":
        return "aligned_bull"
    if htf == "down" and working == "down":
        return "aligned_bear"
    if htf == "down" and working == "up":
        return "htf_bear_ltf_bull"
    if htf == "up" and working == "down":
        return "htf_bull_ltf_bear"
    return "neutral"


def build_trend_context(
    *,
    working_timeframe: str,
    working_trend: Trend,
    working_events: list[StructureEvent],
    htf_timeframe: str | None = None,
    htf_trend: Trend = "none",
    htf_events: list[StructureEvent] | None = None,
) -> TrendContext:
    """Compose trend context from already-computed structure data.

    Caller is responsible for running :func:`detect_structure_events` on
    each timeframe and threading the results in.
    """
    return TrendContext(
        htf_timeframe=htf_timeframe,
        htf_trend=htf_trend,
        htf_last_event=htf_events[-1] if htf_events else None,
        working_timeframe=working_timeframe,
        working_trend=working_trend,
        working_last_event=working_events[-1] if working_events else None,
        alignment=_classify_alignment(htf_trend, working_trend),
    )


def build_liquidity_map(
    levels: list[LiquidityLevel],
    *,
    current_price: float,
) -> LiquidityMap:
    """Split pools into above / below price, sorted by distance.

    Skips already-swept pools (spent liquidity) for the directional lists,
    but surfaces the most recently swept one as ``most_recent_swept`` —
    useful for inferring "which direction the last flush came from".
    """
    unswept = [lv for lv in levels if lv.swept_at is None]
    above = sorted(
        (lv for lv in unswept if lv.price > current_price),
        key=lambda lv: lv.price - current_price,
    )
    below = sorted(
        (lv for lv in unswept if lv.price < current_price),
        key=lambda lv: current_price - lv.price,
    )
    swept = [lv for lv in levels if lv.swept_at is not None]
    most_recent_swept = (
        max(swept, key=lambda lv: lv.swept_at)  # type: ignore[arg-type, return-value]
        if swept
        else None
    )
    return LiquidityMap(
        above=tuple(above),
        below=tuple(below),
        most_recent_swept=most_recent_swept,
    )


def build_zone_context(
    order_blocks: list[OrderBlock],
    fvgs: list[FairValueGap],
    *,
    current_price: float,
) -> ZoneContext:
    """Filter to active (unmitigated) zones; identify nearest above / below."""
    active_obs = tuple(ob for ob in order_blocks if ob.mitigated_at is None)
    active_fvgs = tuple(fvg for fvg in fvgs if fvg.mitigated_at is None)

    # All active zones, each with a representative price (mid of body / gap)
    candidates: list[tuple[float, OrderBlock | FairValueGap]] = []
    for ob in active_obs:
        candidates.append(((ob.top + ob.bottom) / 2, ob))
    for fvg in active_fvgs:
        candidates.append(((fvg.top + fvg.bottom) / 2, fvg))

    above = [(p, z) for p, z in candidates if p > current_price]
    below = [(p, z) for p, z in candidates if p < current_price]
    nearest_above = min(above, key=lambda pz: pz[0] - current_price)[1] if above else None
    nearest_below = (
        min(below, key=lambda pz: current_price - pz[0])[1] if below else None
    )
    return ZoneContext(
        active_order_blocks=active_obs,
        active_fvgs=active_fvgs,
        nearest_above=nearest_above,
        nearest_below=nearest_below,
    )



def build_flow_context(
    *,
    cvd_series: list[float],
    vwap: float | None,
    current_price: float,
    poc: float | None,
    divergences: list[DivergenceEvent],
    cvd_lookback: int = 8,
    divergence_recency_bars: int = 12,
) -> FlowContext:
    """Build FlowContext from already-computed series.

    ``cvd_series`` is the cumulative volume delta column over the working
    timeframe. We summarise its trend over the last ``cvd_lookback`` bars
    and report the absolute delta change there.

    ``divergences`` is filtered to the most recent ``divergence_recency_bars``
    timestamps in the input list, regardless of indicator type.
    """
    cvd_trend: Trend = "none"
    cvd_change = 0.0
    if len(cvd_series) >= cvd_lookback + 1:
        recent = cvd_series[-cvd_lookback:]
        baseline = cvd_series[-cvd_lookback - 1]
        cvd_change = recent[-1] - baseline
        if cvd_change > 0:
            cvd_trend = "up"
        elif cvd_change < 0:
            cvd_trend = "down"

    vwap_distance_pct: float | None = None
    if vwap is not None and current_price > 0:
        vwap_distance_pct = (current_price - vwap) / current_price

    # Take the last N divergences chronologically (already sorted by detector)
    recent_divs = tuple(divergences[-divergence_recency_bars:])

    return FlowContext(
        cvd_trend=cvd_trend,
        cvd_recent_change=cvd_change,
        vwap=vwap,
        vwap_distance_pct=vwap_distance_pct,
        poc=poc,
        recent_divergences=recent_divs,
    )


def build_stop_hunt_context(
    hunts: list[StopHunt],
    *,
    recency_count: int = 5,
) -> StopHuntContext:
    """Tail of confirmed hunts; bias from the most recent confirmed sweep.

    A confirmed low-side sweep implies bullish reversal pressure;
    high-side implies bearish. If the most recent sweep is unconfirmed
    (price closed back through the pool but reversal stuck check failed),
    we fall back to neutral.
    """
    confirmed = [h for h in hunts if h.confirmed]
    recent = tuple(confirmed[-recency_count:])
    most_recent = recent[-1] if recent else None
    bias: Bias = "neutral"
    if most_recent is not None:
        bias = "bullish" if most_recent.side == "low" else "bearish"
    return StopHuntContext(
        recent=recent,
        most_recent=most_recent,
        bias_implication=bias,
    )


def build_funding_context(
    *,
    oi: float | None,
    oi_24h_ago: float | None,
    funding_rate: float | None,
) -> FundingContext:
    """Pure assembly. Caller supplies the latest OI, OI from 24h ago,
    and the latest 5-source weighted funding rate.
    """
    change_pct: float | None = None
    if oi is not None and oi_24h_ago is not None and oi_24h_ago != 0:
        change_pct = (oi - oi_24h_ago) / oi_24h_ago
    return FundingContext(
        oi=oi,
        oi_change_24h_pct=change_pct,
        funding_rate=funding_rate,
    )


def build_wyckoff_context(
    snapshot: WyckoffSnapshot, *, language: Language = "en"
) -> WyckoffContext:
    """Wrap a snapshot with a forward-looking watch hint.

    The hint is phase-aware: it tells the user what event would advance
    the FSM from here.
    """
    from pa_assistant.analysis.wyckoff import WyckoffPhase

    phase = snapshot.phase
    rl = snapshot.range_low
    rh = snapshot.range_high

    def _fmt(p: float | None) -> str:
        return f"${p:,.0f}" if p is not None else "?"

    if language == "zh":
        hints_zh: dict[WyckoffPhase, str] = {
            WyckoffPhase.NEUTRAL: "等待高量级 climax 事件 (SC/BC) 定义区间",
            WyckoffPhase.ACC_A: f"等待 AR 自动反弹突破 {_fmt(rl)} 形成区间上沿",
            WyckoffPhase.ACC_B: (
                f"等待 Spring 跌破 {_fmt(rl)} 或突破 {_fmt(rh)}"
            ),
            WyckoffPhase.ACC_C: f"等待 SOS (Sign of Strength) 向上突破 {_fmt(rh)}",
            WyckoffPhase.ACC_D: f"等待 LPS (Last Point of Support) 在 {_fmt(rh)} 之上守住",
            WyckoffPhase.ACC_E: "上行趋势进行中——继续跟踪结构延续",
            WyckoffPhase.DIST_A: f"等待 AR 自动回调跌破 {_fmt(rh)} 形成区间下沿",
            WyckoffPhase.DIST_B: (
                f"等待 UTAD 突破 {_fmt(rh)} 或跌破 {_fmt(rl)}"
            ),
            WyckoffPhase.DIST_C: f"等待 SOW (Sign of Weakness) 向下跌破 {_fmt(rl)}",
            WyckoffPhase.DIST_D: f"等待 LPSY (Last Point of Supply) 在 {_fmt(rl)} 之下守住",
            WyckoffPhase.DIST_E: "下行趋势进行中——继续跟踪结构延续",
        }
        return WyckoffContext(snapshot=snapshot, next_watch=hints_zh[phase])

    # Per-phase forward-looking watch (English)
    hints: dict[WyckoffPhase, str] = {
        WyckoffPhase.NEUTRAL: "Watch for a high-volume climax (SC or BC) to define a range",
        WyckoffPhase.ACC_A: f"Watch for AR (auto-rally) above {_fmt(rl)} to define range high",
        WyckoffPhase.ACC_B: (
            f"Watch for Spring below {_fmt(rl)} OR breakout above {_fmt(rh)}"
        ),
        WyckoffPhase.ACC_C: f"Watch for SOS (sign of strength) breakout above {_fmt(rh)}",
        WyckoffPhase.ACC_D: f"Watch for LPS (last point of support) holding {_fmt(rh)}",
        WyckoffPhase.ACC_E: "Markup in progress — track structure for trend continuation",
        WyckoffPhase.DIST_A: f"Watch for AR (auto-reaction) below {_fmt(rh)} to define range low",
        WyckoffPhase.DIST_B: (
            f"Watch for UTAD above {_fmt(rh)} OR breakdown below {_fmt(rl)}"
        ),
        WyckoffPhase.DIST_C: f"Watch for SOW (sign of weakness) breakdown below {_fmt(rl)}",
        WyckoffPhase.DIST_D: f"Watch for LPSY (last point of supply) holding {_fmt(rl)}",
        WyckoffPhase.DIST_E: "Markdown in progress — track structure for trend continuation",
    }
    return WyckoffContext(snapshot=snapshot, next_watch=hints[phase])



# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------


# Funding rate thresholds. Binance "normal" is +0.01% (0.0001) per 8h; values
# below -0.0003 or above +0.0003 are unusually skewed and contrarian-relevant.
_FUNDING_BULLISH_EXTREME = -0.0003
_FUNDING_BEARISH_EXTREME = 0.0003


def build_scorecard(
    *,
    trend: TrendContext,
    wyckoff: WyckoffContext,
    liquidity: LiquidityMap,
    zones: ZoneContext,
    flow: FlowContext,
    stop_hunts: StopHuntContext,
    funding: FundingContext,
    min_divergence_strength: float = 0.4,
    language: Language = "en",
) -> Scorecard:
    """Produce bullish / bearish factor lists from the sub-contexts.

    Each factor is a one-line human-readable string, ordered by category:

    1. Wyckoff phase bias
    2. Trend alignment
    3. Stop hunt direction
    4. Divergences (filtered by min strength)
    5. Active zones above / below
    6. Liquidity magnets
    7. CVD trend
    8. Funding rate extremes

    All checks are pure rules. No magic numbers buried beyond the
    constants at the top of this section.
    """
    from pa_assistant.analysis.wyckoff import WyckoffPhase

    bullish: list[str] = []
    bearish: list[str] = []
    zh = language == "zh"

    # 1. Wyckoff
    phase = wyckoff.snapshot.phase
    if phase in {WyckoffPhase.ACC_C, WyckoffPhase.ACC_D, WyckoffPhase.ACC_E}:
        if zh:
            bullish.append(f"Wyckoff {_ZH_PHASE[phase.value]} (累积阶段已确认)")
        else:
            bullish.append(f"Wyckoff {phase.value} (accumulation committed)")
    elif phase == WyckoffPhase.ACC_B:
        bullish.append(
            "Wyckoff 阶段 B (供给减少)" if zh else "Wyckoff Phase B (supply diminishing)"
        )
    elif phase == WyckoffPhase.ACC_A:
        bullish.append(
            "Wyckoff 阶段 A (卖出高潮已记录)"
            if zh
            else "Wyckoff Phase A (selling climax registered)"
        )
    elif phase in {WyckoffPhase.DIST_C, WyckoffPhase.DIST_D, WyckoffPhase.DIST_E}:
        if zh:
            bearish.append(f"Wyckoff {_ZH_PHASE[phase.value]} (派发阶段已确认)")
        else:
            bearish.append(f"Wyckoff {phase.value} (distribution committed)")
    elif phase == WyckoffPhase.DIST_B:
        bearish.append(
            "Wyckoff 阶段 B (需求减少)" if zh else "Wyckoff Phase B (demand diminishing)"
        )
    elif phase == WyckoffPhase.DIST_A:
        bearish.append(
            "Wyckoff 阶段 A (买入高潮已记录)"
            if zh
            else "Wyckoff Phase A (buying climax registered)"
        )

    # 2. Trend alignment
    if trend.alignment == "aligned_bull":
        bullish.append(
            "HTF 与工作周期均向上" if zh else "HTF + working timeframe both trending up"
        )
    elif trend.alignment == "aligned_bear":
        bearish.append(
            "HTF 与工作周期均向下" if zh else "HTF + working timeframe both trending down"
        )
    elif trend.alignment == "htf_bear_ltf_bull":
        bullish.append(
            "工作周期可能反转, 逆于 HTF 下行"
            if zh
            else "Working TF reversal candidate against HTF downtrend"
        )
    elif trend.alignment == "htf_bull_ltf_bear":
        bearish.append(
            "工作周期可能反转, 逆于 HTF 上行"
            if zh
            else "Working TF reversal candidate against HTF uptrend"
        )

    # 3. Stop hunts
    if stop_hunts.most_recent is not None:
        h = stop_hunts.most_recent
        if h.side == "low":
            if zh:
                bullish.append(
                    f"近期确认 Spring 跌破 ${h.pool_price:,.0f} "
                    f"({h.wick_ratio:.0%} 长针拒绝)"
                )
            else:
                bullish.append(
                    f"Recent confirmed Spring below ${h.pool_price:,.0f} "
                    f"({h.wick_ratio:.0%} wick rejection)"
                )
        else:
            if zh:
                bearish.append(
                    f"近期确认 UTAD 突破 ${h.pool_price:,.0f} "
                    f"({h.wick_ratio:.0%} 长针拒绝)"
                )
            else:
                bearish.append(
                    f"Recent confirmed UTAD above ${h.pool_price:,.0f} "
                    f"({h.wick_ratio:.0%} wick rejection)"
                )

    # 4. Divergences
    for div in flow.recent_divergences:
        if div.strength < min_divergence_strength:
            continue
        if zh:
            ind_zh = _ZH_INDICATOR.get(div.indicator, div.indicator)
            side_zh = _ZH_BIAS.get(div.side, div.side)
            msg = (
                f"{ind_zh} {side_zh}背离 ({div.strength:.0%}) "
                f"于 ${div.swing_price:,.0f}"
            )
        else:
            msg = (
                f"{div.indicator} {div.side} divergence ({div.strength:.0%}) "
                f"at ${div.swing_price:,.0f}"
            )
        if div.side == "bullish":
            bullish.append(msg)
        else:
            bearish.append(msg)

    # 5. Active zones
    bear_obs_above = [
        ob
        for ob in zones.active_order_blocks
        if ob.direction == "bearish" and ob.bottom > 0
    ]
    bull_obs_below = [ob for ob in zones.active_order_blocks if ob.direction == "bullish"]
    if len(bear_obs_above) >= 2:
        if zh:
            bearish.append(f"{len(bear_obs_above)} 个生效中的看跌订单块")
        else:
            bearish.append(f"{len(bear_obs_above)} active bearish Order Block(s)")
    if len(bull_obs_below) >= 2:
        if zh:
            bullish.append(f"{len(bull_obs_below)} 个生效中的看涨订单块")
        else:
            bullish.append(f"{len(bull_obs_below)} active bullish Order Block(s)")

    # 6. Liquidity magnets — untested pools above price = upside magnet
    if len(liquidity.above) >= 2 and len(liquidity.below) == 0:
        if zh:
            bullish.append(f"上方 {len(liquidity.above)} 个未触发流动性池 (磁吸)")
        else:
            bullish.append(
                f"{len(liquidity.above)} untested liquidity pool(s) above (magnets)"
            )
    elif len(liquidity.below) >= 2 and len(liquidity.above) == 0:
        if zh:
            bearish.append(f"下方 {len(liquidity.below)} 个未触发流动性池 (磁吸)")
        else:
            bearish.append(
                f"{len(liquidity.below)} untested liquidity pool(s) below (magnets)"
            )

    # 7. CVD trend
    if flow.cvd_trend == "up":
        if zh:
            bullish.append(f"CVD 近期上行 ({flow.cvd_recent_change:+,.0f})")
        else:
            bullish.append(f"CVD rising over recent bars ({flow.cvd_recent_change:+,.0f})")
    elif flow.cvd_trend == "down":
        if zh:
            bearish.append(f"CVD 近期下行 ({flow.cvd_recent_change:+,.0f})")
        else:
            bearish.append(f"CVD falling over recent bars ({flow.cvd_recent_change:+,.0f})")

    # 8. Funding rate extreme (contrarian)
    if funding.funding_rate is not None:
        if funding.funding_rate <= _FUNDING_BULLISH_EXTREME:
            if zh:
                bullish.append(
                    f"资金费率负向极值 ({funding.funding_rate:.4f}, 反向看多)"
                )
            else:
                bullish.append(
                    f"Funding rate negative extreme ({funding.funding_rate:.4f}, contrarian bullish)"
                )
        elif funding.funding_rate >= _FUNDING_BEARISH_EXTREME:
            if zh:
                bearish.append(
                    f"资金费率正向极值 ({funding.funding_rate:.4f}, 反向看空)"
                )
            else:
                bearish.append(
                    f"Funding rate positive extreme ({funding.funding_rate:.4f}, contrarian bearish)"
                )

    return Scorecard(bullish_factors=tuple(bullish), bearish_factors=tuple(bearish))


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def build_context_report(
    *,
    timestamp: datetime,
    symbol: str,
    timeframe: str,
    current_price: float,
    trend: TrendContext,
    wyckoff: WyckoffContext,
    liquidity: LiquidityMap,
    zones: ZoneContext,
    flow: FlowContext,
    stop_hunts: StopHuntContext,
    funding: FundingContext,
    language: Language = "en",
) -> ContextReport:
    """Compose the full ContextReport from already-built sub-contexts.

    Caller is responsible for running each module's primitive detectors
    and threading the results into the sub-context builders. This
    function only assembles the report and computes the umbrella fields
    (invalidations, magnets, scorecard).
    """
    invalidation_long: float | None = None
    invalidation_short: float | None = None

    # Wyckoff range edges are the most direct invalidations
    if wyckoff.snapshot.side == "accumulation" and wyckoff.snapshot.range_low is not None:
        invalidation_long = wyckoff.snapshot.range_low
    if wyckoff.snapshot.side == "distribution" and wyckoff.snapshot.range_high is not None:
        invalidation_short = wyckoff.snapshot.range_high

    # Nearest unswept liquidity pool by absolute distance is the magnet
    nearest_magnet: float | None = None
    nearest_above_price = liquidity.above[0].price if liquidity.above else None
    nearest_below_price = liquidity.below[0].price if liquidity.below else None
    if nearest_above_price is not None and nearest_below_price is not None:
        nearest_magnet = (
            nearest_above_price
            if (nearest_above_price - current_price)
            < (current_price - nearest_below_price)
            else nearest_below_price
        )
    elif nearest_above_price is not None:
        nearest_magnet = nearest_above_price
    elif nearest_below_price is not None:
        nearest_magnet = nearest_below_price

    scorecard = build_scorecard(
        trend=trend,
        wyckoff=wyckoff,
        liquidity=liquidity,
        zones=zones,
        flow=flow,
        stop_hunts=stop_hunts,
        funding=funding,
        language=language,
    )

    return ContextReport(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        current_price=current_price,
        trend=trend,
        wyckoff=wyckoff,
        liquidity=liquidity,
        zones=zones,
        flow=flow,
        stop_hunts=stop_hunts,
        funding=funding,
        invalidation_long=invalidation_long,
        invalidation_short=invalidation_short,
        nearest_magnet=nearest_magnet,
        scorecard=scorecard,
    )



# ---------------------------------------------------------------------------
# Renderer (plain text, terminal-friendly)
# ---------------------------------------------------------------------------


def _format_phase(phase_value: str, *, language: Language = "en") -> str:
    """Pretty-print 'accumulation_phase_b' -> 'Accumulation Phase B' (or 累积阶段 B)."""
    if language == "zh":
        return _ZH_PHASE.get(phase_value, phase_value)
    if phase_value == "neutral":
        return "Neutral"
    side, _, sub = phase_value.partition("_phase_")
    return f"{side.capitalize()} Phase {sub.upper()}"


def render_text(report: ContextReport) -> str:
    """Render a ContextReport as a terminal-friendly multi-section text block."""
    lines: list[str] = []
    p = report.current_price

    # Header
    lines.append(
        f"{report.symbol}  {report.timeframe}  ${p:,.2f}    "
        f"{report.timestamp:%Y-%m-%d %H:%M UTC}"
    )
    lines.append("")

    # --- TREND ---
    lines.append("── Trend ───────────────────────────────────────────")
    if report.trend.htf_timeframe:
        lines.append(f"    HTF ({report.trend.htf_timeframe}): {report.trend.htf_trend}")
    lines.append(
        f"    {report.trend.working_timeframe}: {report.trend.working_trend}"
    )
    lines.append(f"    Alignment: {report.trend.alignment}")
    lines.append("")

    # --- WYCKOFF ---
    lines.append("── Wyckoff ─────────────────────────────────────────")
    snap = report.wyckoff.snapshot
    lines.append(
        f"    State: {_format_phase(snap.phase.value)}  "
        f"(confidence {snap.confidence:.0%})"
    )
    if snap.range_low is not None or snap.range_high is not None:
        rl = f"${snap.range_low:,.0f}" if snap.range_low is not None else "?"
        rh = f"${snap.range_high:,.0f}" if snap.range_high is not None else "?"
        lines.append(f"    Range: {rl} - {rh}")
    lines.append(f"    Next:  {report.wyckoff.next_watch}")
    lines.append("")

    # --- LIQUIDITY MAP ---
    lines.append("── Liquidity Map ───────────────────────────────────")
    if report.liquidity.above:
        lines.append("    Above:")
        for lv in report.liquidity.above[:3]:
            dist_pct = (lv.price - p) / p * 100
            lines.append(
                f"      ${lv.price:>10,.0f}   {lv.side:<5s}  "
                f"({len(lv.touches)} touches)   +{dist_pct:.2f}%"
            )
    if report.liquidity.below:
        lines.append("    Below:")
        for lv in report.liquidity.below[:3]:
            dist_pct = (p - lv.price) / p * 100
            lines.append(
                f"      ${lv.price:>10,.0f}   {lv.side:<5s}  "
                f"({len(lv.touches)} touches)   -{dist_pct:.2f}%"
            )
    if report.liquidity.most_recent_swept is not None:
        s = report.liquidity.most_recent_swept
        lines.append(f"    Most recent sweep: {s.side} @ ${s.price:,.0f}")
    lines.append("")

    # --- ZONES ---
    lines.append("── Supply / Demand Zones ───────────────────────────")
    n_obs = len(report.zones.active_order_blocks)
    n_fvg = len(report.zones.active_fvgs)
    lines.append(f"    Active: {n_obs} Order Block(s),  {n_fvg} FVG(s)")
    if report.zones.nearest_above is not None:
        z = report.zones.nearest_above
        kind = "OB" if isinstance(z, OrderBlock) else "FVG"
        lines.append(
            f"    Nearest above: {kind} {z.direction}  ${z.bottom:,.0f}-${z.top:,.0f}"
        )
    if report.zones.nearest_below is not None:
        z = report.zones.nearest_below
        kind = "OB" if isinstance(z, OrderBlock) else "FVG"
        lines.append(
            f"    Nearest below: {kind} {z.direction}  ${z.bottom:,.0f}-${z.top:,.0f}"
        )
    lines.append("")

    # --- FLOW ---
    lines.append("── Flow / Volume ───────────────────────────────────")
    lines.append(
        f"    CVD:  {report.flow.cvd_trend}  ({report.flow.cvd_recent_change:+,.0f})"
    )
    if report.flow.vwap is not None and report.flow.vwap_distance_pct is not None:
        sign = "above" if report.flow.vwap_distance_pct > 0 else "below"
        lines.append(
            f"    VWAP: ${report.flow.vwap:,.2f}  "
            f"(price {abs(report.flow.vwap_distance_pct) * 100:.2f}% {sign})"
        )
    if report.flow.poc is not None:
        lines.append(f"    POC:  ${report.flow.poc:,.2f}")
    if report.flow.recent_divergences:
        lines.append("    Recent divergences:")
        for div in list(report.flow.recent_divergences)[-4:]:
            arrow = "▲" if div.side == "bullish" else "▼"
            lines.append(
                f"      {arrow} {div.indicator:<6s}  {div.side:<8s}  "
                f"strength {div.strength:.0%}  @${div.swing_price:,.0f}"
            )
    lines.append("")

    # --- STOP HUNTS ---
    lines.append("── Stop Hunts ──────────────────────────────────────")
    if report.stop_hunts.most_recent is not None:
        h = report.stop_hunts.most_recent
        arrow = "↓ low" if h.side == "low" else "↑ high"
        lines.append(
            f"    Most recent ({arrow}): pool ${h.pool_price:,.0f}  "
            f"wick {h.wick_ratio:.0%}  vol x{h.volume_ratio:.1f}  "
            f"{'confirmed' if h.confirmed else 'unconfirmed'}"
        )
        lines.append(f"    Bias implication: {report.stop_hunts.bias_implication}")
    else:
        lines.append("    (no recent confirmed sweeps)")
    lines.append("")

    # --- FUNDING / OI ---
    lines.append("── Funding / OI ────────────────────────────────────")
    if report.funding.oi is not None:
        oi_line = f"    OI:      {report.funding.oi:,.0f}"
        if report.funding.oi_change_24h_pct is not None:
            oi_line += f"  ({report.funding.oi_change_24h_pct * 100:+.2f}% 24h)"
        lines.append(oi_line)
    if report.funding.funding_rate is not None:
        lines.append(
            f"    Funding: {report.funding.funding_rate:+.4f}  (5-source weighted)"
        )
    if report.funding.oi is None and report.funding.funding_rate is None:
        lines.append("    (no OI or funding data — run `pa poll-oi` / `pa poll-funding`)")
    lines.append("")

    # --- KEY LEVELS ---
    lines.append("── Key Levels ──────────────────────────────────────")
    if report.invalidation_long is not None:
        lines.append(
            f"    Long invalidation:  close < ${report.invalidation_long:,.0f}"
        )
    if report.invalidation_short is not None:
        lines.append(
            f"    Short invalidation: close > ${report.invalidation_short:,.0f}"
        )
    if report.nearest_magnet is not None:
        side = "up" if report.nearest_magnet > p else "down"
        lines.append(f"    Nearest magnet:     ${report.nearest_magnet:,.0f}  ({side})")
    lines.append("")

    # --- SCORECARD ---
    lines.append("── Scorecard ───────────────────────────────────────")
    lines.append(f"    Bullish factors ({len(report.scorecard.bullish_factors)}):")
    for f in report.scorecard.bullish_factors:
        lines.append(f"      + {f}")
    lines.append(f"    Bearish factors ({len(report.scorecard.bearish_factors)}):")
    for f in report.scorecard.bearish_factors:
        lines.append(f"      - {f}")
    lines.append("")
    lines.append(f"    Net bias: {report.scorecard.net_bias.upper()}")

    return "\n".join(lines)


def render_markdown(report: ContextReport, *, language: Language = "en") -> str:
    """Render ContextReport as markdown for messaging platforms.

    Uses GitHub-flavored markdown subset (headings, bold, lists) — broadly
    compatible with Telegram MarkdownV2 (after escape), WeChat Work
    markdown card, and as plain readable text on platforms that don't
    style it.
    """
    p = report.current_price
    out: list[str] = []
    zh = language == "zh"

    out.append(
        f"**{report.symbol} {report.timeframe}** ${p:,.2f} "
        f"({report.timestamp:%Y-%m-%d %H:%M UTC})"
    )
    out.append("")

    # Wyckoff (lead with the headline)
    snap = report.wyckoff.snapshot
    phase_label = _format_phase(snap.phase.value, language=language)
    if zh:
        out.append(f"**Wyckoff:** {phase_label} (置信度 {snap.confidence:.0%})")
    else:
        out.append(f"**Wyckoff:** {phase_label} (confidence {snap.confidence:.0%})")
    if snap.range_low is not None or snap.range_high is not None:
        rl = f"${snap.range_low:,.0f}" if snap.range_low is not None else "?"
        rh = f"${snap.range_high:,.0f}" if snap.range_high is not None else "?"
        if zh:
            out.append(f"  区间: {rl} - {rh}")
        else:
            out.append(f"  Range: {rl} - {rh}")
    if zh:
        out.append(f"  下一步: {report.wyckoff.next_watch}")
    else:
        out.append(f"  Next: {report.wyckoff.next_watch}")
    out.append("")

    # Trend
    if zh:
        align_label = _ZH_ALIGNMENT.get(report.trend.alignment, report.trend.alignment)
        trend_line = f"**趋势:** {align_label}"
        if report.trend.htf_timeframe:
            htf_dir = _ZH_DIRECTION.get(report.trend.htf_trend, report.trend.htf_trend)
            wk_dir = _ZH_DIRECTION.get(
                report.trend.working_trend, report.trend.working_trend
            )
            trend_line += (
                f" (HTF {report.trend.htf_timeframe} {htf_dir}, "
                f"{report.trend.working_timeframe} {wk_dir})"
            )
    else:
        trend_line = f"**Trend:** {report.trend.alignment}"
        if report.trend.htf_timeframe:
            trend_line += (
                f" (HTF {report.trend.htf_timeframe} {report.trend.htf_trend}, "
                f"{report.trend.working_timeframe} {report.trend.working_trend})"
            )
    out.append(trend_line)
    out.append("")

    # Scorecard (the actionable bit)
    bias_label = (
        _ZH_BIAS.get(report.scorecard.net_bias, report.scorecard.net_bias)
        if zh
        else report.scorecard.net_bias.upper()
    )
    if zh:
        out.append(f"**综合倾向: {bias_label}**")
    else:
        out.append(f"**Net bias: {bias_label}**")
    out.append("")
    if report.scorecard.bullish_factors:
        out.append("看多因素: " if zh else "Bullish factors:")
        for f in report.scorecard.bullish_factors:
            out.append(f"- {f}")
        out.append("")
    if report.scorecard.bearish_factors:
        out.append("看空因素: " if zh else "Bearish factors:")
        for f in report.scorecard.bearish_factors:
            out.append(f"- {f}")
        out.append("")

    # Key levels
    if (
        report.invalidation_long is not None
        or report.invalidation_short is not None
        or report.nearest_magnet is not None
    ):
        out.append("**关键价位:**" if zh else "**Key levels:**")
        if report.invalidation_long is not None:
            if zh:
                out.append(f"- 做多失效: 收盘 < ${report.invalidation_long:,.0f}")
            else:
                out.append(
                    f"- Long invalidation: close < ${report.invalidation_long:,.0f}"
                )
        if report.invalidation_short is not None:
            if zh:
                out.append(f"- 做空失效: 收盘 > ${report.invalidation_short:,.0f}")
            else:
                out.append(
                    f"- Short invalidation: close > ${report.invalidation_short:,.0f}"
                )
        if report.nearest_magnet is not None:
            if zh:
                side = "上方" if report.nearest_magnet > p else "下方"
                out.append(f"- 最近磁吸: ${report.nearest_magnet:,.0f} ({side})")
            else:
                side = "up" if report.nearest_magnet > p else "down"
                out.append(
                    f"- Nearest magnet: ${report.nearest_magnet:,.0f} ({side})"
                )

    return "\n".join(out)
