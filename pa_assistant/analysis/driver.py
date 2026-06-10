"""Price driver analysis — who is moving the market and how.

Combines OI change, CVD change, price direction, and funding rate into
a single ``DriverVerdict`` that answers:

* **Who** is driving the price (bulls or bears)?
* **How** (active position building vs forced liquidation)?
* **Why** (step-by-step reasoning chain)?

Pure functions only — no IO, no DB, no HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from pa_assistant.analysis.divergence import DivergenceEvent

DriverSide = Literal["bullish", "bearish", "neutral"]
Confidence = Literal["high", "medium", "low"]
_Direction = Literal["up", "down", "flat", "unknown"]

# Thresholds
_PRICE_THRESHOLD = 0.0001  # 0.01%
_OI_THRESHOLD = 0.0005  # 0.05%
_FUNDING_EXTREME = 0.0003  # ±0.03%
_MIN_DIVERGENCE_STRENGTH = 0.4


@dataclass(frozen=True, slots=True)
class DriverVerdict:
    """Result of price driver analysis for a time period."""

    # Metadata
    symbol: str
    timeframe: str
    timestamp: datetime

    # Raw metrics
    price_open: float
    price_close: float
    price_change_pct: float
    oi_start: float | None
    oi_end: float | None
    oi_change_pct: float | None
    cvd_change: float
    total_volume: float
    funding_rate: float | None

    # Interpretation
    driver_label: str
    driver_side: DriverSide
    confidence: Confidence
    reasoning_chain: tuple[str, ...]

    # Extra
    funding_note: str | None
    divergence_warnings: tuple[str, ...]


# ---------------------------------------------------------------------------
# Direction classifiers
# ---------------------------------------------------------------------------


def _price_direction(pct: float) -> _Direction:
    if pct > _PRICE_THRESHOLD:
        return "up"
    if pct < -_PRICE_THRESHOLD:
        return "down"
    return "flat"


def _oi_direction(pct: float | None) -> _Direction:
    if pct is None:
        return "unknown"
    if pct > _OI_THRESHOLD:
        return "up"
    if pct < -_OI_THRESHOLD:
        return "down"
    return "flat"


def _cvd_direction(change: float) -> _Direction:
    if change > 0:
        return "up"
    if change < 0:
        return "down"
    return "flat"


# ---------------------------------------------------------------------------
# Pattern matching → (label, side, confidence, conclusion)
# ---------------------------------------------------------------------------


def _match_pattern(
    price_dir: _Direction,
    oi_dir: _Direction,
    cvd_dir: _Direction,
) -> tuple[str, DriverSide, Confidence, str]:
    """Match (price, oi, cvd) directions to a verdict."""

    # --- Price UP ---
    if price_dir == "up":
        if oi_dir == "up" and cvd_dir == "up":
            return (
                "多头主动建仓",
                "bullish",
                "high",
                "新多头资金积极入场建仓，突破可信度高",
            )
        if oi_dir == "up" and cvd_dir in ("down", "flat"):
            return (
                "多空分歧加剧(偏多)",
                "bullish",
                "low",
                "有新空头入场对抗，多头暂占上风但分歧明显",
            )
        if oi_dir == "down":
            return (
                "空头平仓/爆仓推动",
                "bullish",
                "medium",
                "空头被迫平仓推动的反弹，非新多头入场，反弹力度可疑",
            )
        if oi_dir == "flat" and cvd_dir == "up":
            return (
                "多头小幅加仓",
                "bullish",
                "medium",
                "多头温和建仓推动上涨",
            )
        if oi_dir == "flat" and cvd_dir in ("down", "flat"):
            return (
                "多头推进但动力不足",
                "bullish",
                "low",
                "多头在发力推动价格微涨，但缺乏持仓量与主动买盘配合，随时可能衰竭",
            )
        if oi_dir == "unknown" and cvd_dir == "up":
            return (
                "多头买盘推动上涨",
                "bullish",
                "medium",
                "主动买盘占优推动价格上涨",
            )
        if oi_dir == "unknown" and cvd_dir in ("down", "flat"):
            return (
                "多头推进但买盘存疑",
                "bullish",
                "low",
                "多头在发力推进价格，但缺乏持仓数据与主动买盘支撑，上涨可能不健康",
            )

    # --- Price DOWN ---
    if price_dir == "down":
        if oi_dir == "up" and cvd_dir == "down":
            return (
                "空头主动建仓",
                "bearish",
                "high",
                "新空头资金积极入场做空，跌破可信度高",
            )
        if oi_dir == "up" and cvd_dir in ("up", "flat"):
            return (
                "多空分歧加剧(偏空)",
                "bearish",
                "low",
                "有新多头入场抄底，空头暂占上风但分歧明显",
            )
        if oi_dir == "down":
            return (
                "多头割肉/爆仓推动",
                "bearish",
                "medium",
                "多头被迫平仓推动的下跌，非新空头入场，下方可能有超跌反弹",
            )
        if oi_dir == "flat" and cvd_dir == "down":
            return (
                "空头小幅加仓",
                "bearish",
                "medium",
                "空头温和建仓推动下跌",
            )
        if oi_dir == "flat" and cvd_dir in ("up", "flat"):
            return (
                "空头推进但动力不足",
                "bearish",
                "low",
                "空头在发力压低价格，但缺乏持仓量与主动卖盘配合，随时可能反弹",
            )
        if oi_dir == "unknown" and cvd_dir == "down":
            return (
                "空头卖盘推动下跌",
                "bearish",
                "medium",
                "主动卖盘占优推动价格下跌",
            )
        if oi_dir == "unknown" and cvd_dir in ("up", "flat"):
            return (
                "空头推进但卖盘存疑",
                "bearish",
                "low",
                "空头在发力压低价格，但缺乏持仓数据与主动卖盘支撑，下跌可能不健康",
            )

    # --- Price FLAT ---
    if price_dir == "flat":
        if oi_dir == "up":
            return (
                "多空对峙蓄势",
                "neutral",
                "medium",
                "主力密集建仓，方向即将明确",
            )
        if oi_dir == "down":
            return (
                "获利了结/减仓",
                "neutral",
                "low",
                "双方都在减仓离场",
            )

    # Fallback
    return ("市场观望", "neutral", "low", "价格和持仓均无显著变化")


# ---------------------------------------------------------------------------
# Funding rate interpretation
# ---------------------------------------------------------------------------


def _interpret_funding(
    price_dir: _Direction, funding_rate: float | None
) -> str | None:
    if funding_rate is None:
        return None

    rate_pct = funding_rate * 100

    if price_dir == "up":
        if funding_rate <= 0:
            return f"现货买盘驱动的健康上涨(费率 {rate_pct:+.4f}%，无杠杆过热)"
        if funding_rate >= _FUNDING_EXTREME:
            return f"多头杠杆过度拥挤(费率 {rate_pct:+.4f}%，极高)，警惕回调洗盘"
        return f"费率 {rate_pct:+.4f}%，正常偏多"

    if price_dir == "down":
        if funding_rate > 0:
            return f"价跌费升=散户加杠杆抄底(费率 {rate_pct:+.4f}%)，连环爆仓风险高"
        if funding_rate <= -_FUNDING_EXTREME:
            return f"空头杠杆过度拥挤(费率 {rate_pct:+.4f}%，极负)，可能触发空头挤压反弹"
        return f"费率 {rate_pct:+.4f}%，正常偏空"

    # flat
    if abs(funding_rate) >= _FUNDING_EXTREME:
        side = "多头" if funding_rate > 0 else "空头"
        return f"费率 {rate_pct:+.4f}% 处于极值，{side}拥挤"
    return f"费率 {rate_pct:+.4f}%，正常水平"


# ---------------------------------------------------------------------------
# Divergence formatting
# ---------------------------------------------------------------------------

_ZH_INDICATOR = {"cvd": "CVD", "volume": "成交量", "oi": "OI"}
_ZH_SIDE = {"bullish": "看涨", "bearish": "看跌"}


def _format_divergences(
    divergences: list[DivergenceEvent] | None,
) -> tuple[str, ...]:
    if not divergences:
        return ()
    warnings: list[str] = []
    for d in divergences:
        if d.strength < _MIN_DIVERGENCE_STRENGTH:
            continue
        ind = _ZH_INDICATOR.get(d.indicator, d.indicator)
        side = _ZH_SIDE.get(d.side, d.side)
        warnings.append(
            f"{ind} {side}背离 (强度 {d.strength:.0%}) @ ${d.swing_price:,.0f}"
        )
    return tuple(warnings)


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------


def analyze_price_driver(
    *,
    symbol: str,
    timeframe: str,
    timestamp: datetime,
    price_open: float,
    price_close: float,
    oi_start: float | None,
    oi_end: float | None,
    cvd_change: float,
    total_volume: float,
    funding_rate: float | None,
    divergences: list[DivergenceEvent] | None = None,
) -> DriverVerdict:
    """Analyze what is driving the price in the given period.

    All parameters are keyword-only. OI and funding are optional — the
    function degrades gracefully when data is missing.
    """
    # 1. Compute derived metrics
    price_change_pct = (
        (price_close - price_open) / price_open if price_open != 0 else 0.0
    )
    oi_change_pct: float | None = None
    if oi_start is not None and oi_end is not None and oi_start != 0:
        oi_change_pct = (oi_end - oi_start) / oi_start

    # 2. Classify directions
    price_dir = _price_direction(price_change_pct)
    oi_dir = _oi_direction(oi_change_pct)
    cvd_dir = _cvd_direction(cvd_change)

    # 3. Build reasoning chain
    reasoning: list[str] = []

    # Step 1: Price
    if price_dir == "up":
        reasoning.append(
            f"价格上涨 {price_change_pct:+.2%} "
            f"(${price_open:,.2f} → ${price_close:,.2f}) → 买方占优"
        )
    elif price_dir == "down":
        reasoning.append(
            f"价格下跌 {price_change_pct:+.2%} "
            f"(${price_open:,.2f} → ${price_close:,.2f}) → 卖方占优"
        )
    else:
        reasoning.append(
            f"价格横盘 {price_change_pct:+.2%} "
            f"(${price_open:,.2f} → ${price_close:,.2f}) → 多空均衡"
        )

    # Step 2: OI
    if oi_dir == "up":
        reasoning.append(
            f"OI 增加 {oi_change_pct:+.2%} "
            f"({oi_start:,.0f} → {oi_end:,.0f}) → 新资金入场（非平仓驱动）"
        )
    elif oi_dir == "down":
        reasoning.append(
            f"OI 减少 {oi_change_pct:+.2%} "
            f"({oi_start:,.0f} → {oi_end:,.0f}) → 持仓在平仓离场"
        )
    elif oi_dir == "flat":
        reasoning.append(
            f"OI 持平 {oi_change_pct:+.2%} "
            f"({oi_start:,.0f} → {oi_end:,.0f}) → 持仓变化不大"
        )
    else:
        reasoning.append("OI 数据缺失 → 无法判断资金进出")

    # Step 3: CVD
    cvd_label = "买方主导" if cvd_dir == "up" else ("卖方主导" if cvd_dir == "down" else "均衡")
    reasoning.append(f"CVD 变化 {cvd_change:+,.0f} → 主动{cvd_label}")

    # Step 4: Funding
    funding_note = _interpret_funding(price_dir, funding_rate)
    if funding_rate is not None:
        rate_pct = funding_rate * 100
        fr_label = "多头付费" if funding_rate > 0 else ("空头付费" if funding_rate < 0 else "持平")
        reasoning.append(f"资金费率 {rate_pct:+.4f}% ({fr_label}) → 杠杆情绪参考")

    # 4. Match pattern
    driver_label, driver_side, confidence, conclusion = _match_pattern(
        price_dir, oi_dir, cvd_dir
    )

    # 5. Divergences
    divergence_warnings = _format_divergences(divergences)

    return DriverVerdict(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=timestamp,
        price_open=price_open,
        price_close=price_close,
        price_change_pct=price_change_pct,
        oi_start=oi_start,
        oi_end=oi_end,
        oi_change_pct=oi_change_pct,
        cvd_change=cvd_change,
        total_volume=total_volume,
        funding_rate=funding_rate,
        driver_label=driver_label,
        driver_side=driver_side,
        confidence=confidence,
        reasoning_chain=tuple(reasoning),
        funding_note=funding_note,
        divergence_warnings=divergence_warnings,
    )


# ---------------------------------------------------------------------------
# Compact markdown renderer
# ---------------------------------------------------------------------------

_CONFIDENCE_ZH = {"high": "高置信", "medium": "中置信", "low": "低置信"}


def render_driver_markdown(verdict: DriverVerdict) -> str:
    """Render a DriverVerdict as a compact, emoji-rich Chinese markdown report."""

    # Timestamp → Shanghai time
    dt_utc = verdict.timestamp
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    dt_sh = dt_utc.astimezone(timezone(timedelta(hours=8)))

    out: list[str] = []

    # Header
    out.append(f"📊 **{verdict.symbol} {verdict.timeframe} 驱动力报告**")
    out.append(f"{dt_sh:%Y-%m-%d %H:%M} (上海时间)")
    out.append("")

    # Metrics
    out.append(
        f"**💰 价格**: ${verdict.price_open:,.2f} → "
        f"${verdict.price_close:,.2f} ({verdict.price_change_pct:+.2%})"
    )

    if verdict.oi_start is not None and verdict.oi_end is not None:
        oi_pct = f" ({verdict.oi_change_pct:+.2%})" if verdict.oi_change_pct is not None else ""
        out.append(
            f"**📈 持仓量(OI)**: {verdict.oi_start:,.0f} → "
            f"{verdict.oi_end:,.0f}{oi_pct}"
        )
    else:
        out.append("**📈 持仓量(OI)**: 数据缺失")

    cvd_label = "买方主导" if verdict.cvd_change > 0 else ("卖方主导" if verdict.cvd_change < 0 else "均衡")
    out.append(f"**📊 CVD**: {verdict.cvd_change:+,.0f} ({cvd_label})")

    if verdict.funding_rate is not None:
        rate_pct = verdict.funding_rate * 100
        fr_tag = (
            "正·多头付费"
            if verdict.funding_rate > 0
            else ("负·空头付费" if verdict.funding_rate < 0 else "持平")
        )
        out.append(f"**💵 资金费率**: {rate_pct:+.4f}% ({fr_tag})")
    else:
        out.append("**💵 资金费率**: 数据缺失")

    out.append("")
    out.append("---")
    out.append("")

    # Verdict
    conf_label = _CONFIDENCE_ZH.get(verdict.confidence, verdict.confidence)
    out.append(f"**🔍 驱动力: {verdict.driver_label}** ({conf_label})")
    out.append("")

    # Reasoning chain
    out.append("推理链:")
    for i, step in enumerate(verdict.reasoning_chain, 1):
        out.append(f"{i}. {step}")

    # Conclusion
    # Extract conclusion from the pattern match (it's the label + context)
    _, _, _, conclusion = _match_pattern(
        _price_direction(verdict.price_change_pct),
        _oi_direction(verdict.oi_change_pct),
        _cvd_direction(verdict.cvd_change),
    )
    out.append(f"∴ **结论: {conclusion}**")

    # Funding note
    if verdict.funding_note:
        out.append("")
        out.append(f"💡 {verdict.funding_note}")

    # Divergence warnings
    if verdict.divergence_warnings:
        out.append("")
        for w in verdict.divergence_warnings:
            out.append(f"⚠️ {w}")

    return "\n".join(out)
