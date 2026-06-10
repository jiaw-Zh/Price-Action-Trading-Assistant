"""Tests for the price driver analysis module."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from pa_assistant.analysis.driver import (
    analyze_price_driver,
    render_driver_markdown,
    DriverVerdict,
    _price_direction,
    _oi_direction,
    _cvd_direction,
    _match_pattern,
    _interpret_funding,
)
from pa_assistant.analysis.divergence import DivergenceEvent


def test_direction_classifiers() -> None:
    # Price direction
    assert _price_direction(0.0002) == "up"
    assert _price_direction(-0.0002) == "down"
    assert _price_direction(0.00005) == "flat"

    # OI direction
    assert _oi_direction(0.0006) == "up"
    assert _oi_direction(-0.0006) == "down"
    assert _oi_direction(0.00002) == "flat"
    assert _oi_direction(None) == "unknown"

    # CVD direction
    assert _cvd_direction(100.0) == "up"
    assert _cvd_direction(-50.0) == "down"
    assert _cvd_direction(0.0) == "flat"


def test_match_pattern() -> None:
    # Up + Up + Up -> bullish high
    lbl, side, conf, desc = _match_pattern("up", "up", "up")
    assert lbl == "多头主动建仓"
    assert side == "bullish"
    assert conf == "high"

    # Down + Up + Down -> bearish high
    lbl, side, conf, desc = _match_pattern("down", "up", "down")
    assert lbl == "空头主动建仓"
    assert side == "bearish"
    assert conf == "high"

    # Flat + Up -> neutral
    lbl, side, conf, desc = _match_pattern("flat", "up", "flat")
    assert lbl == "多空对峙蓄势"
    assert side == "neutral"
    assert conf == "medium"


def test_interpret_funding() -> None:
    # healthy rise
    assert "健康上涨" in _interpret_funding("up", 0.0)
    # leverage risk
    assert "杠杆过度拥挤" in _interpret_funding("up", 0.0004)
    # retail leverage dip
    assert "散户加杠杆抄底" in _interpret_funding("down", 0.0001)
    # extreme short squeeze potential
    assert "可能触发空头挤压" in _interpret_funding("down", -0.0004)


def test_analyze_price_driver() -> None:
    ts = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    verdict = analyze_price_driver(
        symbol="BTCUSDT",
        timeframe="1h",
        timestamp=ts,
        price_open=60000.0,
        price_close=61000.0,
        oi_start=10000.0,
        oi_end=11000.0,
        cvd_change=500.0,
        total_volume=50000.0,
        funding_rate=0.0001,
        divergences=[],
    )

    assert isinstance(verdict, DriverVerdict)
    assert verdict.symbol == "BTCUSDT"
    assert verdict.timeframe == "1h"
    assert verdict.price_change_pct == pytest.approx(1000.0 / 60000.0)
    assert verdict.oi_change_pct == pytest.approx(1000.0 / 10000.0)
    assert verdict.driver_side == "bullish"
    assert verdict.driver_label == "多头主动建仓"
    assert len(verdict.reasoning_chain) >= 3


def test_render_driver_markdown() -> None:
    ts = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    
    # Create a dummy divergence event
    div = DivergenceEvent(
        timestamp=ts,
        side="bullish",
        indicator="cvd",
        swing_price=60000.0,
        prior_swing_price=59000.0,
        prior_swing_time=datetime(2026, 6, 10, 11, 0, tzinfo=timezone.utc),
        indicator_value=100.0,
        prior_indicator_value=50.0,
        strength=0.5,
    )

    verdict = analyze_price_driver(
        symbol="BTCUSDT",
        timeframe="1h",
        timestamp=ts,
        price_open=60000.0,
        price_close=61000.0,
        oi_start=10000.0,
        oi_end=11000.0,
        cvd_change=500.0,
        total_volume=50000.0,
        funding_rate=0.0001,
        divergences=[div],
    )

    md = render_driver_markdown(verdict)
    assert "BTCUSDT 1h 驱动力报告" in md
    assert "价格" in md
    assert "持仓量" in md
    assert "CVD" in md
    assert "推理链" in md
    assert "结论" in md
    assert "CVD 看涨背离" in md
