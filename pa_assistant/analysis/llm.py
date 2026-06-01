"""LLM-powered market analysis module.

Collects structured data from the analysis engine, formats it into a prompt,
calls an OpenAI-compatible API, and returns a human-readable analysis report.

Design:
* Pure async — all IO is non-blocking
* OpenAI-compatible API — works with OpenAI, DeepSeek, local models, etc.
* Structured prompt — consistent format for reliable output
* Error handling — graceful degradation if LLM fails
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from pa_assistant.logging import get_logger


@dataclass(frozen=True, slots=True)
class MarketData:
    """Structured market data for LLM analysis."""

    symbol: str
    timeframe: str
    current_price: float
    timestamp: datetime

    # Wyckoff
    wyckoff_phase: str | None = None
    wyckoff_confidence: float | None = None
    wyckoff_range_low: float | None = None
    wyckoff_range_high: float | None = None

    # Trend
    working_trend: str | None = None
    htf_trend: str | None = None
    trend_alignment: str | None = None

    # Liquidity
    liquidity_levels: list[dict[str, Any]] | None = None
    stop_hunts: list[dict[str, Any]] | None = None

    # Zones
    active_obs: list[dict[str, Any]] | None = None
    active_fvgs: list[dict[str, Any]] | None = None

    # Volume/OI
    cvd_change: float | None = None
    volume_trend: str | None = None
    funding_rate: float | None = None
    oi_change_pct: float | None = None

    # Divergences
    divergences: list[dict[str, Any]] | None = None

    # Key levels
    invalidation_long: float | None = None
    invalidation_short: float | None = None
    nearest_magnet: float | None = None


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """LLM API configuration."""

    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    max_tokens: int = 2000
    timeout: float = 60.0


def build_market_prompt(data: MarketData, language: str = "zh") -> str:
    """Build a structured prompt from market data."""

    lines: list[str] = []

    if language == "zh":
        lines.append("你是一个专业的加密货币交易分析师与风控官。请根据以下提供的结构化市场数据，生成一份极其严谨、可操作的比特币永续合约交易分析报告。")
        lines.append("")
        lines.append("## 核心分析任务与思考框架")
        lines.append("1. **趋势与结构共振**：评估工作周期与高周期趋势一致性。分析价格在 Wyckoff 区间（Range）的位置，重点关注 Phase C（Spring/UTAD）或 Phase D 的关键确认信号。")
        lines.append("2. **订单流与量价动力**：")
        lines.append("   - 结合价格走势解读 CVD 变化与持仓量（OI）变化。必须严格遵循【CVD/OI/价格共振黄金法则】研判真假突破：")
        lines.append("     * 价格上涨 + OI增加 + CVD增加 => 强力主动多头建仓（真突破，看涨信号强）")
        lines.append("     * 价格上涨 + OI减少 + CVD变化平缓 => 空头被迫爆仓/挤压平仓驱动的无量反弹（弱反弹，防范假突破）")
        lines.append("     * 价格下跌 + OI增加 + CVD减少 => 强力主动空头建仓（真跌破，看空信号强）")
        lines.append("     * 价格下跌 + OI减少 + CVD变化平缓 => 多头踩踏/爆仓割肉平仓驱动的被动流失（防范超跌反弹或假跌破）")
        lines.append("     * 价格震荡 + OI显著增加 => 主力密集对倒/建仓蓄势（防范即将到来的剧烈突破）")
        lines.append("   - 结合资金费率评估市场是否过热，防范极端费率下的反向洗盘。")
        lines.append("3. **流动性磁吸与关键价位**：确认迫近的未触发流动性池（磁吸位），以及彻底推翻多空逻辑的结构性失效点（Invalidation）。")
        lines.append("")
        lines.append("## 交易策略输出规范 (必须严格遵守)")
        lines.append("- **双向情景规划**：")
        lines.append("  - **首选方案（主计划）**：当前高确定性共振方向。提供具体的入场区间（Entry Zone）、止损位（Stop Loss，须设在结构失效点之外）以及分批止盈目标（Targets）。")
        lines.append("  - **备选方案（反转计划）**：若关键失效位被有效破位后的应对策略。")
        lines.append("- **严格盈亏比（R:R Ratio）**：首个目标位盈亏比必须 >= 1:1.5，最终目标盈亏比建议 >= 1:2。若不满足，请明确提示“盈亏比不佳，建议观望”。")
        option_duration = "日内超短线交易（持仓数小时）" if data.timeframe in ["1m", "5m", "15m"] else "波段交易（持仓数天）"
        lines.append(f"- **时间跨度匹配**：当前周期为 {data.timeframe}，交易计划必须匹配其波段属性，预期为 {option_duration}。")
        lines.append("- **仓位与风险管理**：依据 Wyckoff 阶段置信度及多周期共振情况，给出具体的仓位风险暴露度（如高共振/常规仓位，或建议空仓观望）。")
        lines.append("")
        lines.append("## 输出格式")
        lines.append("- 使用清晰的 Markdown 标题与列表，粗体标注关键价格。")
        lines.append("- 语言冷静、简明直接，剔除一切无意义的修饰词。")
        lines.append("- 数据不齐备时，坚决给出“数据不足，保持观望”的结论。")
    else:
        lines.append("You are a professional crypto trading analyst and risk manager. Generate an exceptionally rigorous and actionable analysis report for Bitcoin perpetual contracts based on the following structured market data.")
        lines.append("")
        lines.append("## Core Analysis Framework")
        lines.append("1. **Trend & Structure Confluence**: Assess HTF vs. working timeframe trend alignment. Evaluate price location relative to Wyckoff ranges, prioritizing Phase C (Spring/UTAD) or Phase D confirmation signals.")
        lines.append("2. **Order Flow & Volume Dynamics**:")
        lines.append("   - Analyze CVD and Open Interest (OI) alongside price action using the strict [CVD/OI/Price Resonance Golden Rule] to identify true vs. fake breakouts:")
        lines.append("     * Price Up + OI Up + CVD Up => Strong active buying/long accumulation (True Bullish Breakout).")
        lines.append("     * Price Up + OI Down => Short covering/squeeze (Weak/Fake rally, vulnerable to reversal).")
        lines.append("     * Price Down + OI Up + CVD Down => Strong active selling/short accumulation (True Bearish Breakdown).")
        lines.append("     * Price Down + OI Down => Long liquidation/washout (Vulnerable to short-squeeze bounces).")
        lines.append("     * Price Flat + OI Up => Heavy positioning/accumulation during consolidation (Anticipate high volatility breakout).")
        lines.append("   - Assess funding rate extremes to guard against contrarian washouts in overleveraged environments.")
        lines.append("3. **Liquidity Magnets & Key Levels**: Identify nearest unswept liquidity pools (magnets) and structural invalidation levels.")
        lines.append("")
        lines.append("## Trading Strategy Specifications (Strictly Enforced)")
        lines.append("- **Dual-Scenario Planning**:")
        lines.append("  - **Primary Scenario**: High-confluence bias. Provide specific entry zone, invalidation-based stop loss, and tiered take-profit targets.")
        lines.append("  - **Alternative Scenario**: Contingency/reversal plan if the key invalidation level is breached.")
        lines.append("- **Strict Risk-to-Reward (R:R)**: The first target must have an R:R >= 1:1.5, with the final target aiming for R:R >= 1:2. If unmet, explicitly state 'Poor risk-to-reward ratio, hold/neutral recommended'.")
        option_duration = "intraday scalp (holding hours)" if data.timeframe in ["1m", "5m", "15m"] else "swing trade (holding days)"
        lines.append(f"- **Horizon Alignment**: The working timeframe is {data.timeframe}, so the trade setup must reflect this horizon: expected to be an {option_duration}.")
        lines.append("- **Position & Risk Sizing**: Offer explicit risk/position guidelines based on Wyckoff phase confidence and timeframe alignment.")
        lines.append("")
        lines.append("## Output Format")
        lines.append("- Use clean GFM layout with headers, lists, and bold key prices.")
        lines.append("- Maintain a concise, direct, and objective tone. Cut out conversational fillers.")
        lines.append("- If data is insufficient, explicitly state 'INSUFFICIENT DATA - STAY NEUTRAL'.")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 市场数据" if language == "zh" else "## Market Data")
    lines.append("")
    lines.append(f"**标的**: {data.symbol}")
    lines.append(f"**周期**: {data.timeframe}")
    lines.append(f"**当前价格**: ${data.current_price:,.2f}")
    lines.append(f"**时间**: {data.timestamp:%Y-%m-%d %H:%M UTC}")
    lines.append("")

    # Wyckoff
    if data.wyckoff_phase:
        lines.append("### Wyckoff")
        lines.append(f"- 阶段: {data.wyckoff_phase} (置信度 {data.wyckoff_confidence:.0%})" if language == "zh" else f"- Phase: {data.wyckoff_phase} (confidence {data.wyckoff_confidence:.0%})")
        if data.wyckoff_range_low and data.wyckoff_range_high:
            lines.append(f"- 区间: ${data.wyckoff_range_low:,.0f} - ${data.wyckoff_range_high:,.0f}" if language == "zh" else f"- Range: ${data.wyckoff_range_low:,.0f} - ${data.wyckoff_range_high:,.0f}")
        lines.append("")

    # Trend
    if data.working_trend:
        lines.append("### 趋势" if language == "zh" else "### Trend")
        lines.append(f"- 工作周期 ({data.timeframe}): {data.working_trend}")
        if data.htf_trend:
            lines.append(f"- 高周期: {data.htf_trend}")
        if data.trend_alignment:
            lines.append(f"- 一致性: {data.trend_alignment}")
        lines.append("")

    # Liquidity
    if data.liquidity_levels:
        active = [lv for lv in data.liquidity_levels if lv.get("status") == "active"]
        if active:
            lines.append("### 流动性池" if language == "zh" else "### Liquidity Levels")
            for lv in active[:5]:
                side = "等高" if lv.get("side") == "high" else "等低"
                lines.append(f"- {side} ${lv.get('price', 0):,.0f} ({lv.get('touches', 0)}x)")
            lines.append("")

    # Stop Hunts
    if data.stop_hunts:
        lines.append("### 止损猎杀" if language == "zh" else "### Stop Hunts")
        for sh in data.stop_hunts[:3]:
            side = "上方" if sh.get("side") == "high" else "下方"
            lines.append(f"- {side} ${sh.get('pool_price', 0):,.0f} (影线 {sh.get('wick_ratio', 0):.0%})")
        lines.append("")

    # Zones
    if data.active_obs:
        lines.append("### 订单块" if language == "zh" else "### Order Blocks")
        for ob in data.active_obs[:5]:
            direction = "看涨" if ob.get("direction") == "bullish" else "看跌"
            lines.append(f"- {direction} ${ob.get('bottom', 0):,.0f}-${ob.get('top', 0):,.0f}")
        lines.append("")

    if data.active_fvgs:
        lines.append("### FVG")
        for fvg in data.active_fvgs[:5]:
            direction = "看涨" if fvg.get("direction") == "bullish" else "看跌"
            lines.append(f"- {direction} ${fvg.get('bottom', 0):,.0f}-${fvg.get('top', 0):,.0f}")
        lines.append("")

    # Volume/OI
    lines.append("### 量价数据" if language == "zh" else "### Volume/OI")
    if data.cvd_change is not None:
        direction = "买方主导" if data.cvd_change > 0 else "卖方主导"
        lines.append(f"- CVD 变化: {data.cvd_change:+,.0f} ({direction})")
    if data.funding_rate is not None:
        lines.append(f"- 资金费率: {data.funding_rate*100:+.4f}%")
    if data.oi_change_pct is not None:
        lines.append(f"- OI 变化 (24h): {data.oi_change_pct:+.2f}%")
    lines.append("")

    # Divergences
    if data.divergences:
        lines.append("### 背离信号" if language == "zh" else "### Divergences")
        for d in data.divergences[:5]:
            side = "看涨" if d.get("side") == "bullish" else "看跌"
            ind = d.get("indicator", "")
            strength = d.get("strength", 0)
            lines.append(f"- {ind} {side}背离 (强度 {strength:.0%})")
        lines.append("")

    # Key levels
    lines.append("### 关键价位" if language == "zh" else "### Key Levels")
    if data.invalidation_long:
        lines.append(f"- 做多失效: 收盘 < ${data.invalidation_long:,.0f}")
    if data.invalidation_short:
        lines.append(f"- 做空失效: 收盘 > ${data.invalidation_short:,.0f}")
    if data.nearest_magnet:
        lines.append(f"- 最近磁吸: ${data.nearest_magnet:,.0f}")

    return "\n".join(lines)


async def call_llm(
    prompt: str,
    config: LLMConfig,
    proxy_url: str | None = None,
) -> str:
    """Call OpenAI-compatible API and return the response text."""

    log = get_logger("llm")

    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": config.model,
        "messages": [
            {
                "role": "system",
                "content": "你是一名资深的加密货币自营交易员与风控主管，精通以价格行为（Price Action）、Wyckoff 方法以及订单流（Order Flow）为核心的系统化交易。你对风险极其敏感，只在盈亏比极佳且有高确定性多重共振的情况下推荐交易。你的分析风格冷静、严谨、绝无废话，严格基于提供的数据进行逻辑推演。" if prompt.startswith("你") else "You are a senior crypto proprietary trader and risk manager, specializing in systematic trading using Price Action, Wyckoff theory, and Order Flow analysis. Highly risk-averse, you only recommend trades with premium R:R and multi-confluence alignment. Your tone is cold, rigorous, and direct, drawing conclusions strictly from structured data without generic fillers.",
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": config.max_tokens,
        "temperature": 0.3,
    }

    log.info(
        "llm_request",
        model=config.model,
        base_url=config.base_url,
        prompt_len=len(prompt),
    )

    async with httpx.AsyncClient(
        proxy=proxy_url, timeout=config.timeout
    ) as client:
        response = await client.post(
            f"{config.base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})

        log.info(
            "llm_response",
            model=config.model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )

        return content


async def analyze_with_llm(
    market_data: MarketData,
    config: LLMConfig,
    language: str = "zh",
    proxy_url: str | None = None,
) -> str:
    """Full pipeline: build prompt → call LLM → return report."""

    prompt = build_market_prompt(market_data, language=language)
    report = await call_llm(prompt, config, proxy_url=proxy_url)
    return report
