"""API routes for data and analysis."""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb
from fastapi import APIRouter, Query

from pa_assistant.analysis import (
    analyze_wyckoff,
    compute_delta,
    detect_divergences,
    detect_fvgs,
    detect_liquidity_levels,
    detect_order_blocks,
    detect_structure_events,
    detect_swings,
    resample_ohlcv,
)
from pa_assistant.config import get_settings
from pa_assistant.web.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    Divergence,
    FairValueGap,
    KlineResponse,
    LiquidityLevel,
    OHLCVBar,
    OrderBlock,
    Scorecard,
    StructureEvent,
    TrendState,
    WyckoffState,
)

router = APIRouter(prefix="/api")


@router.get("/klines", response_model=KlineResponse)
async def get_klines(
    symbol: str = Query(default="BTCUSDT"),
    timeframe: str = Query(default="1h"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> KlineResponse:
    """Get OHLCV kline data."""
    settings = get_settings()

    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        df = conn.execute(
            "SELECT open_time, open, high, low, close, volume "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [symbol],
        ).pl()
    finally:
        conn.close()

    if df.is_empty():
        return KlineResponse(bars=[], total=0)

    resampled = resample_ohlcv(df, timeframe)

    if resampled.height > limit:
        resampled = resampled.tail(limit)

    bars = []
    for row in resampled.iter_rows(named=True):
        bars.append(
            OHLCVBar(
                timestamp=row["open_time"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
        )

    return KlineResponse(bars=bars, total=len(bars))


@router.get("/liquidity")
async def get_liquidity(symbol: str = Query(default="BTCUSDT")) -> dict[str, Any]:
    """Get liquidity levels."""
    settings = get_settings()

    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        klines = conn.execute(
            "SELECT open_time, open, high, low, close, volume "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [symbol],
        ).pl()
    finally:
        conn.close()

    if klines.is_empty():
        return {"levels": [], "current_price": 0}

    resampled = resample_ohlcv(klines, "1h")
    levels = detect_liquidity_levels(resampled)
    current_price = float(resampled.row(resampled.height - 1, named=True)["close"])

    return {
        "levels": [
            {
                "price": lv.price,
                "side": lv.side,
                "touches": len(lv.touches),
                "spread_bps": lv.spread_bps,
                "distance": lv.price - current_price,
                "distance_pct": (lv.price - current_price) / current_price * 100,
                "status": "swept" if lv.swept_at else "active",
            }
            for lv in levels
        ],
        "current_price": current_price,
    }


@router.post("/analyze", response_model=AnalyzeResponse)
async def run_analysis(request: AnalyzeRequest) -> AnalyzeResponse:
    """Run full analysis on stored klines."""
    settings = get_settings()

    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        klines = conn.execute(
            "SELECT open_time, open, high, low, close, volume, "
            "quote_volume, taker_buy_base "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [request.symbol],
        ).pl()
        oi_df = conn.execute(
            "SELECT timestamp AS open_time, open_interest AS oi "
            "FROM oi_1m WHERE symbol = ? ORDER BY timestamp",
            [request.symbol],
        ).pl()
    finally:
        conn.close()

    if klines.is_empty():
        return AnalyzeResponse(
            timestamp=datetime.now(UTC),
            symbol=request.symbol,
            timeframe=request.timeframe,
            current_price=0.0,
        )

    working = resample_ohlcv(klines, request.timeframe)
    working = compute_delta(working)

    if not oi_df.is_empty():
        working = working.sort("open_time").join_asof(
            oi_df.sort("open_time"), on="open_time", strategy="backward"
        )

    last_row = working.row(working.height - 1, named=True)
    last_close = float(last_row["close"])
    last_ts = last_row["open_time"]

    annotated = detect_swings(working, lookback=request.swing_lookback)
    structure_events = detect_structure_events(annotated)
    liquidity_levels = detect_liquidity_levels(working, tolerance_bps=request.eq_tolerance_bps)
    order_blocks = detect_order_blocks(working, structure_events)
    fvgs = detect_fvgs(working)
    divergences = detect_divergences(working)

    wyckoff_snaps = analyze_wyckoff(
        working,
        swing_lookback=request.swing_lookback,
        volume_climax_z=request.volume_climax_z,
        eq_tolerance_bps=request.eq_tolerance_bps,
        divergences=divergences,
    )
    wyckoff_snap = wyckoff_snaps[-1]

    working_trend = "none"
    if structure_events:
        last_ev = structure_events[-1]
        if last_ev.event_type in {"BOS_up", "CHoCH_up"}:
            working_trend = "up"
        elif last_ev.event_type in {"BOS_down", "CHoCH_down"}:
            working_trend = "down"

    htf_trend = "none"
    if request.htf:
        htf_df = resample_ohlcv(klines, request.htf)
        htf_annotated = detect_swings(htf_df, lookback=request.swing_lookback)
        htf_events = detect_structure_events(htf_annotated)
        if htf_events:
            last_ev = htf_events[-1]
            if last_ev.event_type in {"BOS_up", "CHoCH_up"}:
                htf_trend = "up"
            elif last_ev.event_type in {"BOS_down", "CHoCH_down"}:
                htf_trend = "down"

    alignment = "无"
    if working_trend == "up" and htf_trend == "up":
        alignment = "双周期一致看多"
    elif working_trend == "down" and htf_trend == "down":
        alignment = "双周期一致看空"

    return AnalyzeResponse(
        timestamp=last_ts,
        symbol=request.symbol,
        timeframe=request.timeframe,
        current_price=last_close,
        wyckoff=WyckoffState(
            phase=wyckoff_snap.phase.value,
            confidence=wyckoff_snap.confidence,
            range_low=wyckoff_snap.range_low,
            range_high=wyckoff_snap.range_high,
            next_watch=wyckoff_snap.next_watch or "",
        ),
        trend=TrendState(
            working=working_trend,
            htf=htf_trend,
            alignment=alignment,
        ),
        liquidity_levels=[
            LiquidityLevel(
                price=lv.price,
                side=lv.side,
                touches=len(lv.touches),
                spread_bps=lv.spread_bps,
                distance=lv.price - last_close,
                distance_pct=(lv.price - last_close) / last_close * 100,
                status="swept" if lv.swept_at else "active",
            )
            for lv in liquidity_levels
        ],
        order_blocks=[
            OrderBlock(
                timestamp=ob.timestamp,
                direction=ob.direction,
                top=ob.top,
                bottom=ob.bottom,
                status="mitigated" if ob.mitigated_at else "active",
            )
            for ob in order_blocks
        ],
        fvgs=[
            FairValueGap(
                timestamp=fvg.timestamp,
                direction=fvg.direction,
                top=fvg.top,
                bottom=fvg.bottom,
                status="filled" if fvg.mitigated_at else "unfilled",
            )
            for fvg in fvgs
        ],
        structure_events=[
            StructureEvent(
                timestamp=ev.timestamp,
                event_type=ev.event_type,
                level=ev.level,
                trend_before=ev.trend_before,
                trend_after=ev.trend_after,
            )
            for ev in structure_events
        ],
        divergences=[
            Divergence(
                timestamp=d.timestamp,
                indicator=d.indicator,
                side=d.side,
                strength=d.strength,
                swing_price=d.swing_price,
                indicator_value=d.indicator_value,
            )
            for d in divergences
        ],
        scorecard=Scorecard(
            net_bias="neutral",
            bullish_factors=[],
            bearish_factors=[],
        ),
    )
