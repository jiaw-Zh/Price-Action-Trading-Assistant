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
        lines.append("你是一个专业的加密货币交易分析师。请根据以下市场数据，生成一份简洁的分析报告。")
        lines.append("")
        lines.append("## 分析要求")
        lines.append("1. 市场概况：当前趋势、关键价位、多空倾向")
        lines.append("2. 量价分析：资金费率、OI 变化、背离信号解读")
        lines.append("3. 交易建议：具体的操作方向、入场区间、止损位、目标位")
        lines.append("")
        lines.append("## 输出格式")
        lines.append("- 使用 Markdown 格式")
        lines.append("- 语言简洁直接，不要废话")
        lines.append("- 交易建议要具体（价格、方向、仓位建议）")
        lines.append("- 如果数据不足，明确说明而非猜测")
    else:
        lines.append("You are a professional crypto trading analyst. Generate a concise analysis report based on the following market data.")
        lines.append("")
        lines.append("## Requirements")
        lines.append("1. Market Overview: current trend, key levels, bias")
        lines.append("2. Volume/OI Analysis: funding rate, OI changes, divergences")
        lines.append("3. Trading Suggestions: direction, entry zone, stop loss, targets")
        lines.append("")
        lines.append("## Output Format")
        lines.append("- Use Markdown format")
        lines.append("- Be concise and direct")
        lines.append("- Be specific with prices and levels")
        lines.append("- If data is insufficient, say so clearly")

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
                "content": "你是一个专业的加密货币交易分析师，擅长价格行为分析、Wyckoff 方法和市场结构分析。输出简洁、直接、可操作的分析报告。" if prompt.startswith("你") else "You are a professional crypto trading analyst specializing in price action, Wyckoff method, and market structure analysis. Output concise, direct, actionable analysis.",
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
