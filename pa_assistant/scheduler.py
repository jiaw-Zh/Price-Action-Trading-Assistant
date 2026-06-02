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

import asyncio
import time
from datetime import UTC, datetime, timedelta

import duckdb
import polars as pl
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

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

_fetch_lock = asyncio.Lock()
_last_fetch_time = 0.0


async def fetch_latest_data(settings: Settings, days: int = 1) -> None:
    """Fetch latest data from exchanges before analysis.

    1. Backfill recent klines (default 1 day)
    2. Update OI snapshot
    3. Update funding rate
    """
    global _last_fetch_time

    log = get_logger("scheduler.fetch")
    sym = settings.symbol.upper()

    async with _fetch_lock:
        now = time.time()
        # If a successful fetch occurred within the last 30 seconds, reuse cached database state.
        if now - _last_fetch_time < 30.0:
            log.info("fetch_skipped_due_to_recency", age_sec=now - _last_fetch_time)
            return

        # 1. Backfill klines
        try:
            from pa_assistant.storage import open_db, upsert_klines_1m

            end_ms = int(time.time() * 1000)
            start_ms = end_ms - days * 86_400_000

            # Try OKX first to completely bypass Binance 418/302 blocks
            try:
                from pa_assistant.ingestion.okx import (
                    OkxRestClient,
                    okx_klines_to_polars,
                    okx_symbol,
                )
                okx_inst = okx_symbol(sym)
                async with OkxRestClient(proxy=settings.http_proxy_url) as client:
                    total = 0
                    async for page in client.iter_klines(
                        okx_inst, "1m", start_ms=start_ms, end_ms=end_ms
                    ):
                        df = okx_klines_to_polars(page, sym)
                        with open_db(settings.duckdb_path) as db:
                            total += upsert_klines_1m(db, df)
                log.info("fetch_klines_okx_primary_done", symbol=sym, written=total)
            except Exception as okx_err:
                log.warning("okx_fetch_klines_failed_trying_binance_fallback", error=str(okx_err))
                from pa_assistant.ingestion import BinanceRestClient, klines_to_polars
                async with BinanceRestClient.from_settings(settings) as client:
                    with open_db(settings.duckdb_path) as db:
                        total = 0
                        async for page in client.iter_klines(
                            sym, "1m", start_ms=start_ms, end_ms=end_ms
                        ):
                            df = klines_to_polars(page, sym)
                            total += upsert_klines_1m(db, df)
                log.info("fetch_klines_binance_fallback_done", symbol=sym, written=total)
        except Exception as e:
            log.error("fetch_klines_failed", error=str(e))

        # 2. Update OI snapshot
        try:
            from pa_assistant.storage import insert_oi_snapshot, open_db

            log.info("fetch_oi_start", symbol=sym)

            open_interest = None
            timestamp = None

            # 1. Try CoinGecko first to avoid WAF blocks and geo-blocks
            if settings.coingecko_api_key:
                try:
                    from pa_assistant.ingestion.coingecko import (
                        CoinGeckoRestClient,
                        parse_coingecko_binance_ticker,
                    )
                    cg_key = settings.coingecko_api_key.get_secret_value()
                    async with CoinGeckoRestClient(
                        base_url=settings.coingecko_base_url,
                        api_key=cg_key,
                        proxy=settings.http_proxy_url,
                    ) as cg_client:
                        ticker = await cg_client.get_binance_futures_ticker(sym)
                        if ticker:
                            cg_data = parse_coingecko_binance_ticker(ticker)
                            open_interest = cg_data["open_interest_base"]
                            timestamp = cg_data["timestamp"]
                            log.info("binance_oi_coingecko_primary_success", oi=open_interest, time=timestamp)
                        else:
                            raise ValueError("Binance ticker not found on CoinGecko")
                except Exception as cg_err:
                    log.warning("coingecko_primary_oi_fetch_failed_trying_binance_direct", error=str(cg_err))

            # 2. Try direct Binance network if CoinGecko failed or was not configured
            if open_interest is None:
                try:
                    from pa_assistant.ingestion import BinanceRestClient
                    async with BinanceRestClient.from_settings(settings) as client:
                        payload = await client.get_open_interest(sym)
                    ts_ms = int(str(payload["time"]))
                    timestamp = datetime.fromtimestamp(ts_ms / 1000, tz=UTC).replace(tzinfo=None)
                    open_interest = float(str(payload["openInterest"]))
                    log.info("binance_oi_direct_success", oi=open_interest, time=timestamp)
                except Exception as binance_err:
                    log.error("binance_oi_direct_failed", error=str(binance_err))

            if open_interest is None:
                raise RuntimeError("Failed to retrieve open interest after all fallbacks.")

            assert timestamp is not None
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

        # Record successful fetch completion timestamp
        _last_fetch_time = time.time()


def _drop_incomplete_candle(df: pl.DataFrame, timeframe: str) -> pl.DataFrame:
    """Drop the last row if it represents an incomplete candle based on current UTC time."""
    if df.is_empty():
        return df

    # Parse timeframe duration
    unit = timeframe[-1].lower()
    try:
        val = int(timeframe[:-1])
    except ValueError:
        return df

    if unit == "m":
        delta = timedelta(minutes=val)
    elif unit == "h":
        delta = timedelta(hours=val)
    elif unit == "d":
        delta = timedelta(days=val)
    elif unit == "w":
        delta = timedelta(weeks=val)
    else:
        return df

    last_row = df.row(df.height - 1, named=True)
    last_ts = last_row["open_time"]

    # Get current naive UTC time
    now_utc = datetime.now(UTC).replace(tzinfo=None)

    if now_utc < last_ts + delta:
        # Drop the last row as it is still incomplete
        return df.head(df.height - 1)

    return df


def check_data_freshness(timestamp: datetime, timeframe: str) -> tuple[bool, float]:
    """Check if the latest data timestamp is stale compared to current time.

    Returns (is_stale, gap_minutes).
    """
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    gap = now_utc - timestamp
    gap_min = gap.total_seconds() / 60.0

    unit = timeframe[-1].lower()
    try:
        val = int(timeframe[:-1])
    except ValueError:
        val = 1

    if unit == "m":
        threshold_min = max(15.0, val * 2.0 + 15.0)
    elif unit == "h":
        threshold_min = val * 2.0 * 60.0 + 30.0
    elif unit == "d":
        threshold_min = val * 2.0 * 24.0 * 60.0 + 60.0
    else:
        threshold_min = 60.0

    return gap_min > threshold_min, gap_min


def collect_market_data(
    settings: Settings,
    timeframe: str,
    htf: str | None = None,
) -> MarketData:
    """Collect market data from DuckDB for LLM analysis."""


    timeframe_minutes = {
        "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
        "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480,
        "12h": 720, "1d": 1440, "1w": 10080,
    }
    max_tf = timeframe
    if htf and timeframe_minutes.get(htf, 0) > timeframe_minutes.get(timeframe, 0):
        max_tf = htf
    minutes_per_bar = timeframe_minutes.get(max_tf, 60)
    total_minutes_needed = int(200 * minutes_per_bar * 2.0)

    now_naive = datetime.now(UTC).replace(tzinfo=None)
    since_time = now_naive - timedelta(minutes=total_minutes_needed)

    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        klines = conn.execute(
            "SELECT open_time, open, high, low, close, volume, "
            "quote_volume, taker_buy_base "
            "FROM kline_1m WHERE symbol = ? AND open_time >= ? ORDER BY open_time",
            [settings.symbol, since_time],
        ).pl()

        # Fallback if too few records are retrieved (e.g. fresh test db)
        if klines.height < 50:
            klines = conn.execute(
                "SELECT open_time, open, high, low, close, volume, "
                "quote_volume, taker_buy_base "
                "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
                [settings.symbol],
            ).pl()

        oi_df = conn.execute(
            "SELECT timestamp AS open_time, open_interest AS oi "
            "FROM oi_1m WHERE symbol = ? AND timestamp >= ? ORDER BY timestamp",
            [settings.symbol, since_time],
        ).pl()

        if oi_df.height < 50:
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
    working = _drop_incomplete_candle(working, timeframe)
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

        # 1.1 Freshness check & emergency fallback
        is_stale, gap_min = check_data_freshness(market_data.timestamp, timeframe)
        if is_stale:
            log.warning("data_stale_triggering_emergency_push", gap_min=gap_min, timeframe=timeframe)
            title = f"⚠️ [系统警报] {settings.symbol} {timeframe.upper()} 数据同步失效"
            body = (
                f"### ⚠️ 系统运行警报：实时数据同步失效\n\n"
                f"**标的**: {settings.symbol} | **周期**: {timeframe.upper()}\n\n"
                f"**检测状态**: 🔴 数据滞后严重，实盘安全拦截已触发\n"
                f"**本地最新K线时间**: `{market_data.timestamp:%Y-%m-%d %H:%M UTC}`\n"
                f"**已滞后时长**: `{gap_min:.1f} 分钟`\n\n"
                f"---\n\n"
                f"**【风控提示】**\n"
                f"为防止以“过期/非实时数据”做出错误的交易决策，系统已**自动拦截并取消了本次 AI 研判流程**（未调用大模型）。\n\n"
                f"**【紧急排查建议】**\n"
                f"1. 币安（Binance）与备用（Bybit）K 线与持仓量接口拉取均失败，请检查服务器网络与代理配置。\n"
                f"2. 请检查 `.env` 中的 `HTTP_PROXY_URL` 是否失效，或更换为其他可用代理 IP。\n"
                f"3. 检查交易所 API 是否有临时维护公告。"
            )
            message = NotificationMessage(title=title, body=body, format="markdown")
            channels = configured_channels(settings)

            # Apply Lark specific routing override if configured
            tf_lower = timeframe.lower()
            specific_webhook = None
            if tf_lower == "1h" and settings.lark_webhook_url_1h:
                specific_webhook = settings.lark_webhook_url_1h.get_secret_value()
            elif tf_lower == "4h" and settings.lark_webhook_url_4h:
                specific_webhook = settings.lark_webhook_url_4h.get_secret_value()
            elif tf_lower == "1d" and settings.lark_webhook_url_1d:
                specific_webhook = settings.lark_webhook_url_1d.get_secret_value()

            if specific_webhook:
                channels = [c for c in channels if c.name != "lark"]
                from pa_assistant.notifications.lark import LarkChannel
                signing_secret = (
                    settings.lark_signing_secret.get_secret_value()
                    if settings.lark_signing_secret
                    else None
                )
                channels.append(
                    LarkChannel(
                        webhook_url=specific_webhook,
                        signing_secret=signing_secret,
                        proxy_url=settings.http_proxy_url,
                    )
                )

            if channels:
                await send_to_all(channels, message)
            return

        # 2. Call LLM
        if not settings.llm_api_key:
            log.error("llm_api_key_not_configured")
            return

        llm_config = LLMConfig(
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            max_tokens=settings.get_llm_max_tokens_for_timeframe(timeframe),
            timeout=settings.llm_timeout,
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

        # 4. Push to configured channels (with timeframe-specific Lark bot overrides)
        channels = configured_channels(settings)

        specific_webhook = None
        tf_lower = timeframe.lower()
        if tf_lower == "1h" and settings.lark_webhook_url_1h:
            specific_webhook = settings.lark_webhook_url_1h.get_secret_value()
        elif tf_lower == "4h" and settings.lark_webhook_url_4h:
            specific_webhook = settings.lark_webhook_url_4h.get_secret_value()
        elif tf_lower == "1d" and settings.lark_webhook_url_1d:
            specific_webhook = settings.lark_webhook_url_1d.get_secret_value()

        if specific_webhook:
            # Remove global Lark channel if present
            channels = [c for c in channels if c.name != "lark"]
            # Append timeframe-specific Lark channel
            from pa_assistant.notifications.lark import LarkChannel
            signing_secret = (
                settings.lark_signing_secret.get_secret_value()
                if settings.lark_signing_secret
                else None
            )
            channels.append(
                LarkChannel(
                    webhook_url=specific_webhook,
                    signing_secret=signing_secret,
                    proxy_url=settings.http_proxy_url,
                )
            )

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

    # Every hour: 1H K-line analysis (triggered at 5 minutes past the hour)
    scheduler.add_job(
        run_analysis_job,
        trigger=CronTrigger(minute=5, timezone="Asia/Shanghai"),
        kwargs={"timeframe": "1h", "htf": "4h", "language": language},
        id="hourly_analysis",
        name="Hourly 1H Analysis (xx:05)",
        replace_existing=True,
    )

    # Every 4 hours: 4H K-line analysis (triggered at 5 minutes past 4H close: 00:05, 04:05, 08:05, 12:05, 16:05, 20:05)
    scheduler.add_job(
        run_analysis_job,
        trigger=CronTrigger(hour="0,4,8,12,16,20", minute=5, timezone="Asia/Shanghai"),
        kwargs={"timeframe": "4h", "htf": "1d", "language": language},
        id="4h_analysis",
        name="4H Analysis (00/04/08/12/16/20:05)",
        replace_existing=True,
    )

    return scheduler
