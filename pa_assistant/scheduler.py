"""Scheduler module for periodic analysis and push.

Uses APScheduler to run analysis tasks at specific times:
* Daily at 08:05 Beijing time: Daily K-line analysis
* Every hour: 1H K-line analysis
* Every 4 hours: 4H K-line analysis

Each job:
1. Fetches latest data from exchanges (klines, OI, funding)
2. Collects market data from DuckDB
3. Runs analysis engine
4. Calls LLM for interpretation
5. Pushes to configured notification channels
"""

from __future__ import annotations

import time

import duckdb
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from pa_assistant.analysis import (
    analyze_wyckoff,
    compute_delta,
    detect_divergences,
    detect_fvgs,
    detect_liquidity_levels,
    detect_order_blocks,
    detect_stop_hunts,
    detect_structure_events,
    detect_swings,
    resample_ohlcv,
)
from pa_assistant.analysis.llm import LLMConfig, MarketData, analyze_with_llm
from pa_assistant.config import Settings
from pa_assistant.logging import get_logger
from pa_assistant.notifications import (
    NotificationMessage,
    configured_channels,
    send_to_all,
)


async def fetch_latest_data(settings: Settings, days: int = 1) -> None:
    """Fetch latest data from exchanges before analysis.

    1. Backfill recent klines (default 1 day)
    2. Update OI snapshot
    3. Update funding rate
    """

    log = get_logger("scheduler.fetch")
    sym = settings.symbol.upper()

    # 1. Backfill klines
    try:
        from pa_assistant.ingestion import BinanceRestClient, klines_to_polars
        from pa_assistant.storage import open_db, upsert_klines_1m

        end_ms = int(time.time() * 1000)
        start_ms = end_ms - days * 86_400_000

        log.info("fetch_klines_start", symbol=sym, days=days)

        async with BinanceRestClient.from_settings(settings) as client:
            with open_db(settings.duckdb_path) as db:
                total = 0
                async for page in client.iter_klines(
                    sym, "1m", start_ms=start_ms, end_ms=end_ms
                ):
                    df = klines_to_polars(page, sym)
                    total += upsert_klines_1m(db, df)

        log.info("fetch_klines_done", symbol=sym, written=total)
    except Exception as e:
        log.error("fetch_klines_failed", error=str(e))

    # 2. Update OI snapshot
    try:
        from pa_assistant.ingestion import BinanceRestClient
        from pa_assistant.storage import insert_oi_snapshot, open_db

        log.info("fetch_oi_start", symbol=sym)

        async with BinanceRestClient.from_settings(settings) as client:
            payload = await client.get_open_interest(sym)

        ts_ms = int(str(payload["time"]))
        from datetime import UTC, datetime

        timestamp = datetime.fromtimestamp(ts_ms / 1000, tz=UTC).replace(tzinfo=None)
        open_interest = float(str(payload["openInterest"]))

        with open_db(settings.duckdb_path) as db:
            insert_oi_snapshot(db, symbol=sym, timestamp=timestamp, open_interest=open_interest)

        log.info("fetch_oi_done", symbol=sym, oi=open_interest)
    except Exception as e:
        log.error("fetch_oi_failed", error=str(e))

    # 3. Update funding rate
    try:
        from pa_assistant.ingestion import make_funding_provider
        from pa_assistant.storage import insert_funding_weighted, open_db

        log.info("fetch_funding_start", symbol=sym)

        provider = make_funding_provider(settings)
        try:
            result = await provider.get_weighted_funding(sym)
        finally:
            await provider.aclose()

        raw_components = {
            s.exchange: {
                "funding_rate": s.funding_rate,
                "open_interest_base": s.open_interest_base,
                "snapshot_time": s.snapshot_time.isoformat(),
            }
            for s in result.components
        }

        with open_db(settings.duckdb_path) as db:
            insert_funding_weighted(
                db,
                symbol=result.symbol,
                timestamp=result.timestamp,
                weighted_rate=result.weighted_rate,
                source=result.source,
                sample_count=result.sample_count,
                raw=raw_components,
            )

        log.info(
            "fetch_funding_done",
            symbol=sym,
            rate=result.weighted_rate,
            source=result.source,
        )
    except Exception as e:
        log.error("fetch_funding_failed", error=str(e))


def collect_market_data(
    settings: Settings,
    timeframe: str,
    htf: str | None = None,
) -> MarketData:
    """Collect market data from DuckDB for LLM analysis."""

    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        klines = conn.execute(
            "SELECT open_time, open, high, low, close, volume, "
            "quote_volume, taker_buy_base "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [settings.symbol],
        ).pl()
        oi_df = conn.execute(
            "SELECT timestamp AS open_time, open_interest AS oi "
            "FROM oi_1m WHERE symbol = ? ORDER BY timestamp",
            [settings.symbol],
        ).pl()
        funding_row = conn.execute(
            "SELECT weighted_rate FROM funding_weighted "
            "WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
            [settings.symbol],
        ).fetchone()
    finally:
        conn.close()

    if klines.is_empty():
        raise ValueError(f"No klines for {settings.symbol}. Run `pa backfill` first.")

    # Resample
    working = resample_ohlcv(klines, timeframe)
    working = compute_delta(working)

    if not oi_df.is_empty():
        working = working.sort("open_time").join_asof(
            oi_df.sort("open_time"), on="open_time", strategy="backward"
        )

    last_row = working.row(working.height - 1, named=True)
    last_close = float(last_row["close"])
    last_ts = last_row["open_time"]

    # Run detectors
    annotated = detect_swings(working, lookback=3)
    structure_events = detect_structure_events(annotated)
    liquidity_levels = detect_liquidity_levels(working)
    stop_hunts = detect_stop_hunts(working, liquidity_levels)
    order_blocks = detect_order_blocks(working, structure_events)
    fvgs = detect_fvgs(working)
    divergences = detect_divergences(working)

    # Wyckoff
    wyckoff_snaps = analyze_wyckoff(
        working,
        swing_lookback=3,
        divergences=divergences,
    )
    wyckoff_snap = wyckoff_snaps[-1]

    # Trend
    working_trend = "none"
    if structure_events:
        last_ev = structure_events[-1]
        if last_ev.event_type in {"BOS_up", "CHoCH_up"}:
            working_trend = "up"
        elif last_ev.event_type in {"BOS_down", "CHoCH_down"}:
            working_trend = "down"

    htf_trend = "none"
    if htf:
        htf_df = resample_ohlcv(klines, htf)
        htf_annotated = detect_swings(htf_df, lookback=3)
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

    # CVD change
    cvd_series = working.get_column("cvd").to_list() if "cvd" in working.columns else []
    cvd_change = (cvd_series[-1] - cvd_series[0]) if len(cvd_series) >= 2 else None

    # OI change
    oi_now = None
    oi_24h_ago = None
    if not oi_df.is_empty():
        from datetime import timedelta

        oi_now_row = oi_df.row(oi_df.height - 1, named=True)
        oi_now = float(oi_now_row["oi"])
        target = oi_now_row["open_time"] - timedelta(hours=24)
        candidates = oi_df.filter(oi_df["open_time"] <= target)
        if candidates.height > 0:
            oi_24h_ago = float(candidates.row(candidates.height - 1, named=True)["oi"])

    oi_change_pct = None
    if oi_now and oi_24h_ago and oi_24h_ago > 0:
        oi_change_pct = (oi_now - oi_24h_ago) / oi_24h_ago * 100

    funding_rate = float(funding_row[0]) if funding_row else None

    # Key levels
    invalidation_long = wyckoff_snap.range_low if wyckoff_snap.side == "accumulation" else None
    invalidation_short = wyckoff_snap.range_high if wyckoff_snap.side == "distribution" else None

    # Nearest magnet
    nearest_magnet = None
    above = [lv for lv in liquidity_levels if lv.side == "high" and lv.swept_at is None]
    below = [lv for lv in liquidity_levels if lv.side == "low" and lv.swept_at is None]
    if above and below:
        nearest_above = min(above, key=lambda lv: lv.price - last_close)
        nearest_below = min(below, key=lambda lv: last_close - lv.price)
        if (nearest_above.price - last_close) < (last_close - nearest_below.price):
            nearest_magnet = nearest_above.price
        else:
            nearest_magnet = nearest_below.price
    elif above:
        nearest_magnet = min(above, key=lambda lv: lv.price - last_close).price
    elif below:
        nearest_magnet = min(below, key=lambda lv: last_close - lv.price).price

    return MarketData(
        symbol=settings.symbol,
        timeframe=timeframe,
        current_price=last_close,
        timestamp=last_ts,
        wyckoff_phase=wyckoff_snap.phase.value,
        wyckoff_confidence=wyckoff_snap.confidence,
        wyckoff_range_low=wyckoff_snap.range_low,
        wyckoff_range_high=wyckoff_snap.range_high,
        working_trend=working_trend,
        htf_trend=htf_trend if htf else None,
        trend_alignment=alignment,
        liquidity_levels=[
            {
                "price": lv.price,
                "side": lv.side,
                "touches": len(lv.touches),
                "spread_bps": lv.spread_bps,
                "status": "swept" if lv.swept_at else "active",
            }
            for lv in liquidity_levels[:10]
        ],
        stop_hunts=[
            {
                "side": sh.side,
                "pool_price": sh.pool_price,
                "wick_ratio": sh.wick_ratio,
            }
            for sh in stop_hunts[:5]
        ],
        active_obs=[
            {
                "direction": ob.direction,
                "top": ob.top,
                "bottom": ob.bottom,
            }
            for ob in order_blocks
            if ob.mitigated_at is None
        ][:5],
        active_fvgs=[
            {
                "direction": fvg.direction,
                "top": fvg.top,
                "bottom": fvg.bottom,
            }
            for fvg in fvgs
            if fvg.mitigated_at is None
        ][:5],
        cvd_change=cvd_change,
        funding_rate=funding_rate,
        oi_change_pct=oi_change_pct,
        divergences=[
            {
                "indicator": d.indicator,
                "side": d.side,
                "strength": d.strength,
            }
            for d in divergences[:5]
        ],
        invalidation_long=invalidation_long,
        invalidation_short=invalidation_short,
        nearest_magnet=nearest_magnet,
    )


async def run_analysis_job(
    timeframe: str,
    htf: str | None = None,
    language: str = "zh",
) -> None:
    """Run analysis, call LLM, and push to notification channels."""

    log = get_logger("scheduler")
    settings = Settings()

    log.info("analysis_job_start", timeframe=timeframe, htf=htf)

    try:
        # 0. Fetch latest data from exchanges
        log.info("fetching_latest_data")
        await fetch_latest_data(settings, days=1)

        # 1. Collect market data
        market_data = collect_market_data(settings, timeframe, htf=htf)

        # 2. Call LLM
        if not settings.llm_api_key:
            log.error("llm_api_key_not_configured")
            return

        llm_config = LLMConfig(
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
        )

        report = await analyze_with_llm(
            market_data,
            llm_config,
            language=language,
            proxy_url=settings.http_proxy_url,
        )

        # 3. Build notification message
        tf_label = timeframe.upper()
        title = f"[{settings.symbol} {tf_label}] AI 分析报告"

        message = NotificationMessage(
            title=title,
            body=report,
            format="markdown",
        )

        # 4. Push to configured channels
        channels = configured_channels(settings)
        if not channels:
            log.warning("no_notification_channels_configured")
            return

        outcome = await send_to_all(channels, message)
        successes = [name for name, err in outcome.items() if err is None]
        failures = [name for name, err in outcome.items() if err is not None]

        log.info(
            "analysis_job_complete",
            timeframe=timeframe,
            successes=successes,
            failures=failures,
        )

    except Exception as e:
        log.error("analysis_job_failed", timeframe=timeframe, error=str(e))


def create_scheduler(language: str = "zh") -> AsyncIOScheduler:
    """Create and configure the APScheduler instance."""

    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # Daily at 08:05 Beijing time: Daily K-line analysis
    scheduler.add_job(
        run_analysis_job,
        trigger=CronTrigger(hour=8, minute=5, timezone="Asia/Shanghai"),
        kwargs={"timeframe": "1d", "htf": None, "language": language},
        id="daily_analysis",
        name="Daily K-line Analysis (08:05)",
        replace_existing=True,
    )

    # Every hour: 1H K-line analysis
    scheduler.add_job(
        run_analysis_job,
        trigger=IntervalTrigger(hours=1),
        kwargs={"timeframe": "1h", "htf": "4h", "language": language},
        id="hourly_analysis",
        name="Hourly 1H Analysis",
        replace_existing=True,
    )

    # Every 4 hours: 4H K-line analysis
    scheduler.add_job(
        run_analysis_job,
        trigger=IntervalTrigger(hours=4),
        kwargs={"timeframe": "4h", "htf": "1d", "language": language},
        id="4h_analysis",
        name="4H Analysis",
        replace_existing=True,
    )

    return scheduler
