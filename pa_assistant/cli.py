"""Command-line entry point.

Run ``pa --help`` after installing the package (or ``uv run pa --help``).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import typer

from pa_assistant import __version__
from pa_assistant.config import Settings, get_settings
from pa_assistant.ingestion import (
    BinanceRestClient,
    klines_to_polars,
    make_funding_provider,
    oi_hist_to_polars,
)
from pa_assistant.logging import configure_logging, get_logger
from pa_assistant.storage import (
    insert_funding_weighted,
    insert_oi_snapshot,
    latest_kline_open_time,
    open_db,
    upsert_klines_1m,
    upsert_oi_history,
)

if TYPE_CHECKING:
    from pa_assistant.analysis.wyckoff import WyckoffEventType, WyckoffPhase
    from pa_assistant.ingestion import WeightedFundingRate

app = typer.Typer(
    name="pa",
    help="Price Action Trading Assistant — market context engine for BTC futures.",
    no_args_is_help=True,
    add_completion=False,
)


def _bootstrap(settings: Settings) -> None:
    """Configure logging using runtime settings."""
    configure_logging(settings.log_level, json_format=settings.log_json)


# ---------------------------------------------------------------------------
# Meta commands
# ---------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the installed package version."""
    typer.echo(f"pa-assistant {__version__}")


@app.command(name="init-db")
def init_db() -> None:
    """Initialize the DuckDB schema (idempotent — safe to re-run)."""
    settings = get_settings()
    _bootstrap(settings)
    log = get_logger("cli.init_db")
    log.info("init_db_start", path=str(settings.duckdb_path))

    with open_db(settings.duckdb_path) as db:
        tables = db.list_tables()
        ver = db.schema_version()

    typer.secho(
        f"✓ Schema initialized at {settings.duckdb_path}",
        fg=typer.colors.GREEN,
        bold=True,
    )
    typer.echo(f"  schema version : {ver}")
    typer.echo(f"  tables ({len(tables)}) : {', '.join(tables)}")


@app.command(name="show-config")
def show_config() -> None:
    """Print the effective configuration (secrets are masked)."""
    settings = get_settings()
    payload = json.loads(settings.model_dump_json())
    typer.echo(json.dumps(payload, indent=2, default=str, sort_keys=True))


@app.command(name="check-proxy")
def check_proxy() -> None:
    """Ping each exchange's public endpoint to verify network reachability.

    Useful when running behind a local proxy (e.g. clash on 127.0.0.1:7890):
    confirms the proxy is up, that routing rules work, and that each
    exchange is reachable. Hits a lightweight endpoint per exchange and
    reports HTTP status + latency. No data is persisted.
    """
    import asyncio as _asyncio
    import time as _time

    settings = get_settings()
    _bootstrap(settings)

    proxy = settings.http_proxy_url
    typer.secho(
        f"Proxy: {proxy or '(direct, no proxy configured)'}",
        fg=typer.colors.CYAN,
        bold=True,
    )

    targets: list[tuple[str, str, str]] = [
        ("binance", settings.binance_rest_base_url, "/fapi/v1/ping"),
        ("okx", "https://www.okx.com", "/api/v5/public/time"),
        ("bybit", "https://api.bybit.com", "/v5/market/time"),
    ]

    async def _probe(name: str, base: str, path: str) -> None:
        kwargs: dict[str, Any] = {"timeout": 8.0}
        if proxy:
            kwargs["proxy"] = proxy
        async with httpx.AsyncClient(**kwargs) as client:
            t0 = _time.perf_counter()
            try:
                resp = await client.get(base + path)
                dt_ms = (_time.perf_counter() - t0) * 1000
                ok = resp.status_code == 200
                colour = typer.colors.GREEN if ok else typer.colors.RED
                typer.secho(
                    f"  {name:8s} HTTP {resp.status_code}  {dt_ms:>7.0f} ms   {base}{path}",
                    fg=colour,
                )
            except Exception as exc:
                dt_ms = (_time.perf_counter() - t0) * 1000
                typer.secho(
                    f"  {name:8s} ERROR  {dt_ms:>7.0f} ms   "
                    f"{type(exc).__name__}: {exc}",
                    fg=typer.colors.RED,
                )

    async def _run_all() -> None:
        await _asyncio.gather(*(_probe(n, b, p) for n, b, p in targets))

    _asyncio.run(_run_all())


# ---------------------------------------------------------------------------
# Ingestion commands
# ---------------------------------------------------------------------------


@app.command()
def backfill(
    symbol: str | None = typer.Option(None, help="Override SYMBOL setting (default from .env)."),
    days: int = typer.Option(
        7, min=1, max=365, help="How many days of history to backfill (REST only)."
    ),
    interval: str = typer.Option(
        "1m",
        help=(
            "Kline interval. Only '1m' is persisted — higher timeframes are "
            "derived from 1m data via the resampling layer (see ARCHITECTURE.md)."
        ),
    ),
) -> None:
    """Backfill historical 1m klines from Binance Futures REST."""
    if interval != "1m":
        raise typer.BadParameter(
            f"Only '1m' is persisted (got {interval!r}). "
            "Higher timeframes are derived from 1m data via resampling — "
            "see docs/ARCHITECTURE.md §5.3.",
            param_hint="--interval",
        )

    settings = get_settings()
    _bootstrap(settings)
    log = get_logger("cli.backfill")

    sym = (symbol or settings.symbol).upper()
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000

    log.info(
        "backfill_start",
        symbol=sym,
        interval=interval,
        days=days,
        start_ms=start_ms,
        end_ms=end_ms,
    )

    async def _run() -> int:
        async with BinanceRestClient.from_settings(settings) as client:
            with open_db(settings.duckdb_path) as db:
                total = 0
                async for page in client.iter_klines(
                    sym, interval, start_ms=start_ms, end_ms=end_ms
                ):
                    df = klines_to_polars(page, sym)
                    total += upsert_klines_1m(db, df)
                return total

    written = asyncio.run(_run())
    typer.secho(
        f"✓ Backfilled {written} klines for {sym} ({days}d, {interval})",
        fg=typer.colors.GREEN,
        bold=True,
    )

    with open_db(settings.duckdb_path) as db:
        latest = latest_kline_open_time(db, sym)
    if latest is not None:
        typer.echo(f"  latest open_time : {latest.isoformat()}")


@app.command(name="backfill-oi")
def backfill_oi(
    symbol: str | None = typer.Option(None, help="Override SYMBOL setting."),
    days: int = typer.Option(
        7, min=1, max=30, help="How many days of OI history (Binance caps at 30)."
    ),
    period: str = typer.Option(
        "5m",
        help=(
            "OI bucket size. Binance supports: 5m / 15m / 30m / 1h / 2h / "
            "4h / 6h / 12h / 1d. Smaller = more rows, finer resolution."
        ),
    ),
) -> None:
    """Backfill historical Open Interest from Binance Futures (≤ 30 days)."""
    settings = get_settings()
    _bootstrap(settings)
    log = get_logger("cli.backfill_oi")

    sym = (symbol or settings.symbol).upper()
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000

    log.info(
        "backfill_oi_start",
        symbol=sym,
        period=period,
        days=days,
        start_ms=start_ms,
        end_ms=end_ms,
    )

    async def _run() -> int:
        async with BinanceRestClient.from_settings(settings) as client:
            with open_db(settings.duckdb_path) as db:
                total = 0
                async for page in client.iter_open_interest_hist(
                    sym, period, start_ms=start_ms, end_ms=end_ms
                ):
                    df = oi_hist_to_polars(page, sym)
                    total += upsert_oi_history(db, df)
                return total

    written = asyncio.run(_run())
    typer.secho(
        f"✓ Backfilled {written} OI rows for {sym} ({days}d, {period})",
        fg=typer.colors.GREEN,
        bold=True,
    )


@app.command(name="poll-oi")
def poll_oi(
    symbol: str | None = typer.Option(None, help="Override SYMBOL setting."),
) -> None:
    """One-shot Open Interest snapshot — writes a single row to ``oi_1m``."""
    settings = get_settings()
    _bootstrap(settings)
    log = get_logger("cli.poll_oi")

    sym = (symbol or settings.symbol).upper()

    async def _run() -> dict[str, object]:
        async with BinanceRestClient.from_settings(settings) as client:
            return await client.get_open_interest(sym)

    payload = asyncio.run(_run())
    ts_ms = int(str(payload["time"]))
    timestamp = datetime.fromtimestamp(ts_ms / 1000, tz=UTC).replace(tzinfo=None)
    open_interest = float(str(payload["openInterest"]))

    with open_db(settings.duckdb_path) as db:
        insert_oi_snapshot(db, symbol=sym, timestamp=timestamp, open_interest=open_interest)

    log.info("oi_polled", symbol=sym, oi=open_interest, timestamp=timestamp.isoformat())
    typer.secho(
        f"✓ OI = {open_interest:,.4f} {sym} @ {timestamp.isoformat()}Z",
        fg=typer.colors.GREEN,
        bold=True,
    )


@app.command(name="poll-funding")
def poll_funding(
    symbol: str | None = typer.Option(None, help="Override SYMBOL setting."),
) -> None:
    """One-shot OI-weighted funding rate snapshot.

    With no Coinglass key configured (default), this aggregates Binance + OKX
    + Bybit and computes the weighted rate ourselves. Writes one row to
    ``funding_weighted``.
    """
    settings = get_settings()
    _bootstrap(settings)
    log = get_logger("cli.poll_funding")

    sym = (symbol or settings.symbol).upper()

    async def _run() -> WeightedFundingRate:
        provider = make_funding_provider(settings)
        try:
            return await provider.get_weighted_funding(sym)
        finally:
            await provider.aclose()

    result = asyncio.run(_run())

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
        "funding_polled",
        symbol=result.symbol,
        weighted_rate=result.weighted_rate,
        source=result.source,
        sample_count=result.sample_count,
    )

    typer.secho(
        f"✓ {result.symbol} weighted funding = {result.weighted_rate * 100:+.4f}%  "
        f"({result.source}, {result.sample_count} sources)",
        fg=typer.colors.GREEN,
        bold=True,
    )
    for s in result.components:
        typer.echo(
            f"  {s.exchange:8s} rate={s.funding_rate * 100:+.4f}%  "
            f"OI={s.open_interest_base:>14,.2f} (base)"
        )


@app.command(name="analyze-structure")
def analyze_structure(
    timeframe: str = typer.Option("15m", help="Resample 1m klines to this TF."),
    lookback: int = typer.Option(2, min=1, help="Swing fractal lookback (bars each side)."),
    last: int = typer.Option(20, min=1, help="Print only the last N events."),
    symbol: str | None = typer.Option(None, help="Override SYMBOL setting."),
) -> None:
    """Run swing + BOS/CHoCH detection on stored 1m klines.

    Reads ``kline_1m`` from DuckDB, resamples to ``--timeframe``, runs the
    fractal swing detector and structure-event walker, prints a chronological
    list of events and trend transitions.
    """
    import duckdb

    from pa_assistant.analysis import (
        detect_structure_events,
        detect_swings,
        resample_ohlcv,
    )

    settings = get_settings()
    _bootstrap(settings)
    sym = (symbol or settings.symbol).upper()

    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        df = conn.execute(
            "SELECT open_time, open, high, low, close, volume "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [sym],
        ).pl()
    finally:
        conn.close()

    if df.is_empty():
        typer.secho(
            f"No klines for {sym}. Run `pa backfill` first.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    resampled = resample_ohlcv(df, timeframe)
    annotated = detect_swings(resampled, lookback=lookback)
    events = detect_structure_events(annotated)

    n_high = annotated.get_column("swing_high").is_not_null().sum()
    n_low = annotated.get_column("swing_low").is_not_null().sum()

    typer.secho(
        f"{sym}  {timeframe}  ({df.height:,} 1m bars → {resampled.height} {timeframe} bars)",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo(f"  swing:  {n_high} 个高点 · {n_low} 个低点  (lookback={lookback})")
    typer.echo(f"  事件:   {len(events)} 个")

    if not events:
        return

    typer.echo("")
    typer.secho("  近期结构事件:", bold=True)
    event_type_zh = {
        "BOS_up": "BOS 上破",
        "BOS_down": "BOS 下破",
        "CHoCH_up": "CHoCH 上破",
        "CHoCH_down": "CHoCH 下破",
    }
    trend_zh = {"up": "上升", "down": "下降", "none": "无"}
    for ev in events[-last:]:
        is_up = "_up" in ev.event_type
        arrow = "↑" if is_up else "↓"
        colour = typer.colors.GREEN if is_up else typer.colors.RED
        ev_zh = event_type_zh.get(ev.event_type, ev.event_type)
        before_zh = trend_zh.get(ev.trend_before, ev.trend_before)
        after_zh = trend_zh.get(ev.trend_after, ev.trend_after)
        typer.secho(
            f"    {ev.timestamp:%Y-%m-%d %H:%M}  {arrow} {ev_zh:8s} "
            f"@ ${ev.level:>10,.2f}   ({before_zh} → {after_zh})",
            fg=colour,
        )


@app.command(name="analyze-volume")
def analyze_volume(
    timeframe: str = typer.Option("1h", help="Resample 1m klines to this TF."),
    bars: int = typer.Option(168, min=1, help="How many recent bars to use."),
    n_bins: int = typer.Option(50, min=2, help="Volume Profile bins."),
    symbol: str | None = typer.Option(None, help="Override SYMBOL setting."),
) -> None:
    """Print delta / CVD trend, VWAP + bands, and Volume Profile summary."""
    import duckdb

    from pa_assistant.analysis import (
        compute_delta,
        compute_volume_profile,
        compute_vwap,
        resample_ohlcv,
    )

    settings = get_settings()
    _bootstrap(settings)
    sym = (symbol or settings.symbol).upper()

    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        df = conn.execute(
            "SELECT open_time, open, high, low, close, volume, "
            "quote_volume, taker_buy_base "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [sym],
        ).pl()
    finally:
        conn.close()

    if df.is_empty():
        typer.secho(f"No klines for {sym}.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    resampled = resample_ohlcv(df, timeframe).tail(bars)
    with_delta = compute_delta(resampled)
    with_vwap = compute_vwap(with_delta)
    profile = compute_volume_profile(resampled, n_bins=n_bins)

    last = with_vwap.row(with_vwap.height - 1, named=True)
    cvd_first = float(with_delta.get_column("cvd").head(1).item())
    cvd_last = float(with_delta.get_column("cvd").tail(1).item())
    cvd_change = cvd_last - cvd_first
    last_close = float(last["close"])

    typer.secho(
        f"{sym}  {timeframe}  (最近 {bars} 根 K 线)",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo(
        f"  区间:    {with_delta.row(0, named=True)['open_time']:%Y-%m-%d %H:%M}"
        f"  ->  {last['open_time']:%Y-%m-%d %H:%M}"
    )
    typer.echo(f"  收盘:    ${last_close:>10,.2f}")

    typer.echo("")
    typer.secho("  订单流 (Delta / CVD)", bold=True)
    typer.echo(f"    最近一根 Delta:    {last['delta']:>+14,.4f}")
    typer.echo(f"    窗口内 CVD:        {cvd_change:>+14,.4f}")
    direction = "买方主导" if cvd_change > 0 else "卖方主导"
    typer.echo(f"    解读:              {direction}")

    typer.echo("")
    typer.secho("  VWAP", bold=True)
    typer.echo(f"    VWAP:              ${last['vwap']:>10,.2f}")
    typer.echo(
        f"    1 标准差区间:       ${last['vwap_lower_1']:>10,.2f}"
        f"  -  ${last['vwap_upper_1']:>10,.2f}"
    )
    typer.echo(
        f"    2 标准差区间:       ${last['vwap_lower_2']:>10,.2f}"
        f"  -  ${last['vwap_upper_2']:>10,.2f}"
    )
    pos = "在 VWAP 上方" if last_close > last["vwap"] else "在 VWAP 下方"
    typer.echo(f"    价格{pos}")

    typer.echo("")
    typer.secho("  成交量分布 (Volume Profile)", bold=True)
    typer.echo(f"    POC:               ${profile.poc:>10,.2f}")
    typer.echo(
        f"    价值区间 (70%):    ${profile.val:>10,.2f}"
        f"  -  ${profile.vah:>10,.2f}"
    )
    typer.echo(f"    每档宽度:          ${profile.bin_width:>10,.2f}")
    if last_close < profile.val:
        typer.echo("    价格在价值区间下方 (潜在均值回归做多)")
    elif last_close > profile.vah:
        typer.echo("    价格在价值区间上方 (潜在均值回归做空)")
    else:
        typer.echo("    价格在价值区间内")


@app.command(name="analyze-zones")
def analyze_zones(
    timeframe: str = typer.Option("1h", help="Resample 1m klines to this TF."),
    lookback: int = typer.Option(2, min=1, help="Swing fractal lookback."),
    ob_lookback: int = typer.Option(10, min=1, help="OB backward search bars."),
    show_mitigated: bool = typer.Option(
        False, "--show-mitigated", help="Include already-mitigated zones."
    ),
    symbol: str | None = typer.Option(None, help="Override SYMBOL setting."),
) -> None:
    """Print active Order Blocks and Fair Value Gaps."""
    import duckdb

    from pa_assistant.analysis import (
        detect_fvgs,
        detect_order_blocks,
        detect_structure_events,
        detect_swings,
        resample_ohlcv,
    )

    settings = get_settings()
    _bootstrap(settings)
    sym = (symbol or settings.symbol).upper()

    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        df = conn.execute(
            "SELECT open_time, open, high, low, close, volume "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [sym],
        ).pl()
    finally:
        conn.close()

    if df.is_empty():
        typer.secho(f"No klines for {sym}.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    resampled = resample_ohlcv(df, timeframe)
    annotated = detect_swings(resampled, lookback=lookback)
    events = detect_structure_events(annotated)
    obs = detect_order_blocks(resampled, events, lookback=ob_lookback)
    fvgs = detect_fvgs(resampled)

    last_close = float(resampled.row(resampled.height - 1, named=True)["close"])

    typer.secho(
        f"{sym}  {timeframe}  current price: ${last_close:,.2f}",
        fg=typer.colors.CYAN,
        bold=True,
    )

    typer.echo("")
    typer.secho(
        f"  订单块 (Order Block)  ({sum(1 for o in obs if o.mitigated_at is None)} 个生效中 "
        f"/ {len(obs)} 个总计)",
        bold=True,
    )
    for ob in obs:
        if not show_mitigated and ob.mitigated_at is not None:
            continue
        arrow = "↑" if ob.direction == "bullish" else "↓"
        colour = typer.colors.GREEN if ob.direction == "bullish" else typer.colors.RED
        direction_zh = "看涨" if ob.direction == "bullish" else "看跌"
        status = (
            "已缓解" if ob.mitigated_at is not None else "生效中"
        )
        typer.secho(
            f"    {ob.timestamp:%Y-%m-%d %H:%M}  {arrow} {direction_zh:4s}  "
            f"实体 ${ob.bottom:>9,.2f}-${ob.top:<9,.2f}  [{status}]",
            fg=colour,
        )

    typer.echo("")
    typer.secho(
        f"  公允价值缺口 (FVG)  ({sum(1 for f in fvgs if f.mitigated_at is None)} 个未填补 "
        f"/ {len(fvgs)} 个总计)",
        bold=True,
    )
    # Show the most recent unfilled gaps near current price (top 10 by recency).
    unfilled = [f for f in fvgs if f.mitigated_at is None]
    shown = unfilled[-10:] if not show_mitigated else fvgs[-10:]
    for fvg in shown:
        arrow = "↑" if fvg.direction == "bullish" else "↓"
        colour = typer.colors.GREEN if fvg.direction == "bullish" else typer.colors.RED
        direction_zh = "看涨" if fvg.direction == "bullish" else "看跌"
        status = "已填补" if fvg.mitigated_at is not None else "未填补"
        # Annotate distance from current price.
        if last_close < fvg.bottom:
            pos = f"价格在缺口下方 ${(fvg.bottom - last_close):,.0f}"
        elif last_close > fvg.top:
            pos = f"价格在缺口上方 ${last_close - fvg.top:,.0f}"
        else:
            pos = "价格在缺口内"
        typer.secho(
            f"    {fvg.timestamp:%Y-%m-%d %H:%M}  {arrow} {direction_zh:4s}  "
            f"${fvg.bottom:>9,.2f}-${fvg.top:<9,.2f}  [{status}]  {pos}",
            fg=colour,
        )


@app.command(name="analyze-liquidity")
def analyze_liquidity(
    timeframe: str = typer.Option("1h", help="Resample 1m klines to this TF."),
    lookback: int = typer.Option(2, min=1, help="Swing fractal lookback."),
    tolerance_bps: float = typer.Option(
        5.0, min=0.1, help="Cluster tolerance in basis points."
    ),
    min_touches: int = typer.Option(
        2, min=2, help="Minimum swings to form a pool."
    ),
    show_swept: bool = typer.Option(
        False, "--show-swept", help="Include already-swept levels."
    ),
    symbol: str | None = typer.Option(None, help="Override SYMBOL setting."),
) -> None:
    """Print active Equal-Highs / Equal-Lows liquidity pools."""
    import duckdb

    from pa_assistant.analysis import (
        detect_liquidity_levels,
        resample_ohlcv,
    )

    settings = get_settings()
    _bootstrap(settings)
    sym = (symbol or settings.symbol).upper()

    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        df = conn.execute(
            "SELECT open_time, open, high, low, close, volume "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [sym],
        ).pl()
    finally:
        conn.close()

    if df.is_empty():
        typer.secho(f"No klines for {sym}.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    resampled = resample_ohlcv(df, timeframe)
    levels = detect_liquidity_levels(
        resampled,
        lookback=lookback,
        tolerance_bps=tolerance_bps,
        min_touches=min_touches,
    )
    last_close = float(resampled.row(resampled.height - 1, named=True)["close"])

    n_active = sum(1 for lv in levels if lv.swept_at is None)
    typer.secho(
        f"{sym}  {timeframe}  当前价格: ${last_close:,.2f}",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.secho(
        f"  流动性池  ({n_active} 个生效中 / {len(levels)} 个总计)",
        bold=True,
    )

    if not levels:
        typer.echo("    (没有满足容差/最小触碰次数条件的聚集)")
        return

    # Sort displayed levels by price for a "ladder" view: highs above lows.
    shown = [lv for lv in levels if show_swept or lv.swept_at is None]
    shown.sort(key=lambda lv: -lv.price)

    typer.echo("")
    for lv in shown:
        arrow = "▲" if lv.side == "high" else "▼"
        colour = typer.colors.RED if lv.side == "high" else typer.colors.GREEN
        side_zh = "等高" if lv.side == "high" else "等低"
        status = "已扫" if lv.swept_at is not None else "生效中"
        # Distance to current price.
        if lv.side == "high":
            dist = lv.price - last_close
            pos = f"价格上方 ${dist:,.0f}" if dist > 0 else f"价格下方 ${-dist:,.0f}"
        else:
            dist = last_close - lv.price
            pos = f"价格下方 ${dist:,.0f}" if dist > 0 else f"价格上方 ${-dist:,.0f}"
        typer.secho(
            f"    {arrow} {side_zh}  ${lv.price:>10,.2f}  "
            f"{len(lv.touches)} 次触碰  spread {lv.spread_bps:>4.1f}bps  "
            f"[{status}]  {pos}",
            fg=colour,
        )


@app.command(name="analyze-stop-hunts")
def analyze_stop_hunts(
    timeframe: str = typer.Option("1h", help="Resample 1m klines to this TF."),
    lookback: int = typer.Option(2, min=1, help="Swing fractal lookback."),
    tolerance_bps: float = typer.Option(5.0, min=0.1, help="Pool cluster tolerance."),
    min_touches: int = typer.Option(2, min=2, help="Minimum swings per pool."),
    min_wick_ratio: float = typer.Option(
        0.5, min=0.0, max=1.0, help="Minimum rejection wick fraction."
    ),
    confirmation_bars: int = typer.Option(
        3, min=0, help="Bars after sweep to verify reversal."
    ),
    confirmed_only: bool = typer.Option(
        False, "--confirmed-only", help="Show only confirmed hunts."
    ),
    symbol: str | None = typer.Option(None, help="Override SYMBOL setting."),
) -> None:
    """Print detected stop-hunt / liquidity-sweep events."""
    import duckdb

    from pa_assistant.analysis import (
        detect_liquidity_levels,
        detect_stop_hunts,
        resample_ohlcv,
    )

    settings = get_settings()
    _bootstrap(settings)
    sym = (symbol or settings.symbol).upper()

    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        df = conn.execute(
            "SELECT open_time, open, high, low, close, volume "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [sym],
        ).pl()
    finally:
        conn.close()

    if df.is_empty():
        typer.secho(f"No klines for {sym}.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    resampled = resample_ohlcv(df, timeframe)
    levels = detect_liquidity_levels(
        resampled,
        lookback=lookback,
        tolerance_bps=tolerance_bps,
        min_touches=min_touches,
    )
    hunts = detect_stop_hunts(
        resampled,
        levels,
        min_wick_ratio=min_wick_ratio,
        confirmation_bars=confirmation_bars,
    )

    last_close = float(resampled.row(resampled.height - 1, named=True)["close"])

    if confirmed_only:
        hunts = [h for h in hunts if h.confirmed]

    typer.secho(
        f"{sym}  {timeframe}  current price: ${last_close:,.2f}",
        fg=typer.colors.CYAN,
        bold=True,
    )

    n_confirmed = sum(1 for h in hunts if h.confirmed)
    typer.secho(
        f"  止损猎杀  ({len(hunts)} 个总计, {n_confirmed} 个已确认)",
        bold=True,
    )

    if not hunts:
        typer.echo("    (未检测到止损猎杀形态)")
        return

    typer.echo("")
    for h in hunts:
        arrow = "▼" if h.side == "high" else "▲"
        colour = typer.colors.RED if h.side == "high" else typer.colors.GREEN
        # For high hunts: the bias is bearish (down arrow); for low hunts: bullish (up).
        bias = "看跌反转" if h.side == "high" else "看涨反转"
        cflag = "✓ 已确认" if h.confirmed else "  未确认"
        typer.secho(
            f"    {h.timestamp:%Y-%m-%d %H:%M}  {arrow} {bias:6s}  "
            f"池 ${h.pool_price:>9,.2f} ({h.pool_touches}x)  "
            f"影线 {h.wick_ratio:.0%}  量比 {h.volume_ratio:>4.1f}x  "
            f"{cflag}",
            fg=colour,
        )


@app.command(name="analyze-divergences")
def analyze_divergences(
    timeframe: str = typer.Option("1h", help="Resample 1m klines to this TF."),
    indicators: str = typer.Option(
        "cvd,volume,oi",
        help="Comma-separated subset of cvd / volume / oi.",
    ),
    lookback: int = typer.Option(2, min=1, help="Swing fractal lookback."),
    min_separation_bars: int = typer.Option(
        3, min=0, help="Minimum bars between two compared swings."
    ),
    min_strength: float = typer.Option(
        0.0,
        min=0.0,
        max=1.0,
        help=(
            "Filter out divergences below this strength. Note: OI strength "
            "is naturally small (~1-5%) due to baseline magnitude — keep "
            "this at 0 if you want OI signals."
        ),
    ),
    symbol: str | None = typer.Option(None, help="Override SYMBOL setting."),
) -> None:
    """Print CVD / Volume / OI divergences against price swings."""
    import duckdb

    from pa_assistant.analysis import (
        compute_delta,
        detect_divergences,
        resample_ohlcv,
    )

    settings = get_settings()
    _bootstrap(settings)
    sym = (symbol or settings.symbol).upper()

    requested = [s.strip() for s in indicators.split(",") if s.strip()]
    valid = {"cvd", "volume", "oi"}
    invalid = set(requested) - valid
    if invalid:
        typer.secho(
            f"Unknown indicator(s): {invalid}. Valid: {valid}.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        klines = conn.execute(
            "SELECT open_time, open, high, low, close, volume, "
            "quote_volume, taker_buy_base "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [sym],
        ).pl()
        oi_df = conn.execute(
            "SELECT timestamp AS open_time, open_interest AS oi "
            "FROM oi_1m WHERE symbol = ? ORDER BY timestamp",
            [sym],
        ).pl()
    finally:
        conn.close()

    if klines.is_empty():
        typer.secho(f"No klines for {sym}.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    # Resample klines to the target TF first.
    resampled = resample_ohlcv(klines, timeframe)

    # Add CVD / volume columns. Volume already exists; CVD is derived.
    if "cvd" in requested:
        resampled = compute_delta(resampled)

    # Resample-and-join OI: take the LAST OI value within each bar (snapshot
    # semantics, not flow). Use Polars' join_asof for nearest-time alignment.
    if "oi" in requested:
        if oi_df.is_empty():
            typer.secho(
                "  ⚠  No OI history found. Run `pa backfill-oi` first.",
                fg=typer.colors.YELLOW,
            )
        else:
            # Sort both inputs (required by join_asof) and align OI to each
            # resampled bar's open_time, taking the most recent OI <= open_time.
            resampled = resampled.sort("open_time").join_asof(
                oi_df.sort("open_time"),
                on="open_time",
                strategy="backward",
            )

    from typing import cast

    from pa_assistant.analysis.divergence import Indicator

    active_indicators = cast(
        list[Indicator],
        [ind for ind in requested if ind in valid],
    )
    events = detect_divergences(
        resampled,
        indicators=active_indicators,
        lookback=lookback,
        min_separation_bars=min_separation_bars,
    )
    events = [e for e in events if e.strength >= min_strength]

    last_close = float(resampled.row(resampled.height - 1, named=True)["close"])

    typer.secho(
        f"{sym}  {timeframe}  当前价格: ${last_close:,.2f}",
        fg=typer.colors.CYAN,
        bold=True,
    )
    by_indicator: dict[str, int] = {"cvd": 0, "volume": 0, "oi": 0}
    for e in events:
        by_indicator[e.indicator] = by_indicator.get(e.indicator, 0) + 1

    ind_zh_map = {"cvd": "CVD", "volume": "成交量", "oi": "OI"}
    summary_parts = [f"{by_indicator[ind]} {ind_zh_map.get(ind, ind)}" for ind in requested if ind in valid]
    typer.secho(
        f"  背离  ({len(events)} 个总计: " + ", ".join(summary_parts) + ")",
        bold=True,
    )

    if not events:
        typer.echo("    (没有满足条件的背离)")
        return

    typer.echo("")
    for e in events:
        arrow = "▼" if e.side == "bearish" else "▲"
        colour = typer.colors.RED if e.side == "bearish" else typer.colors.GREEN
        bias = "看跌反转" if e.side == "bearish" else "看涨反转"
        ind_zh = ind_zh_map.get(e.indicator, e.indicator)
        typer.secho(
            f"    {e.timestamp:%Y-%m-%d %H:%M}  {arrow} {bias:8s}  "
            f"{ind_zh:5s}  "
            f"价格 ${e.prior_swing_price:>8,.0f}→${e.swing_price:<8,.0f}  "
            f"指标 {e.prior_indicator_value:>+10,.1f}→{e.indicator_value:<+10,.1f}  "
            f"强度 {e.strength:.0%}",
            fg=colour,
        )


@app.command(name="wyckoff")
def wyckoff(
    timeframe: str = typer.Option("4h", help="Resample 1m klines to this TF."),
    swing_lookback: int = typer.Option(3, min=1, help="Fractal lookback for swings."),
    volume_climax_z: float = typer.Option(
        2.0, min=0.5, help="Z-score threshold for volume climax detection."
    ),
    volume_window: int = typer.Option(
        20, min=5, help="Rolling window for volume z-score baseline."
    ),
    eq_tolerance_bps: float = typer.Option(
        10.0, min=0.0, help="Tolerance for liquidity pool clustering (bps)."
    ),
    show_events: int = typer.Option(
        15, min=0, help="How many recent events to show in the audit trail."
    ),
    symbol: str | None = typer.Option(None, help="Override SYMBOL setting."),
) -> None:
    """Wyckoff phase state machine: current state, range, event chain."""
    import duckdb

    from pa_assistant.analysis import (
        analyze_wyckoff,
        compute_delta,
        detect_divergences,
        resample_ohlcv,
    )
    from pa_assistant.analysis.wyckoff import WyckoffPhase

    settings = get_settings()
    _bootstrap(settings)
    sym = (symbol or settings.symbol).upper()

    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        klines = conn.execute(
            "SELECT open_time, open, high, low, close, volume, "
            "quote_volume, taker_buy_base "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [sym],
        ).pl()
        oi_df = conn.execute(
            "SELECT timestamp AS open_time, open_interest AS oi "
            "FROM oi_1m WHERE symbol = ? ORDER BY timestamp",
            [sym],
        ).pl()
    finally:
        conn.close()

    if klines.is_empty():
        typer.secho(f"No klines for {sym}.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    resampled = resample_ohlcv(klines, timeframe)
    resampled = compute_delta(resampled)
    if not oi_df.is_empty():
        resampled = resampled.sort("open_time").join_asof(
            oi_df.sort("open_time"), on="open_time", strategy="backward"
        )

    divergences = detect_divergences(resampled)

    snaps = analyze_wyckoff(
        resampled,
        swing_lookback=swing_lookback,
        volume_climax_z=volume_climax_z,
        volume_window=volume_window,
        eq_tolerance_bps=eq_tolerance_bps,
        divergences=divergences,
    )
    current = snaps[-1]
    last_close = float(resampled.row(resampled.height - 1, named=True)["close"])

    typer.secho(
        f"{sym}  {timeframe}  当前价格: ${last_close:,.2f}",
        fg=typer.colors.CYAN,
        bold=True,
    )

    phase_label = _format_phase(current.phase)
    phase_colour = {
        "accumulation": typer.colors.GREEN,
        "distribution": typer.colors.RED,
    }.get(current.side or "", typer.colors.WHITE)
    typer.echo("")
    typer.secho(
        f"  Wyckoff 状态: {phase_label}  (置信度 {current.confidence:.0%})",
        fg=phase_colour,
        bold=True,
    )

    if current.range_high is not None or current.range_low is not None:
        rh = f"${current.range_high:,.0f}" if current.range_high is not None else "?"
        rl = f"${current.range_low:,.0f}" if current.range_low is not None else "?"
        typer.echo(f"  区间:          {rl}  -  {rh}")

    if current.invalidation_price is not None:
        typer.echo(f"  失效条件:      收盘价跌破 ${current.invalidation_price:,.0f}")

    if current.phase == WyckoffPhase.NEUTRAL and not current.events:
        typer.echo(
            "  (未检测到 Wyckoff 事件 — 请尝试更长的时间周期或更多历史数据)"
        )
        return

    if show_events > 0 and current.events:
        typer.echo("")
        typer.secho(
            f"  事件链 (最近 {min(show_events, len(current.events))} 个):", bold=True
        )
        for e in current.events[-show_events:]:
            colour = (
                typer.colors.GREEN
                if e.side == "accumulation"
                else typer.colors.RED
            )
            label = _format_event_type(e.event_type)
            typer.secho(
                f"    {e.timestamp:%Y-%m-%d %H:%M}  {label:<28s} "
                f"@${e.price:>10,.0f}  置信度 {e.confidence:.0%}",
                fg=colour,
            )


def _format_phase(phase: WyckoffPhase | str) -> str:
    """Pretty-print a WyckoffPhase in Chinese (e.g. '累积阶段 C')."""
    s = phase.value if isinstance(phase, WyckoffPhase) else phase
    if s == "neutral":
        return "中性"
    side, _, sub = s.partition("_phase_")
    side_zh = "累积" if side == "accumulation" else "派发"
    return f"{side_zh}阶段 {sub.upper()}"


def _format_event_type(event_type: WyckoffEventType | str) -> str:
    """Translate enum → Chinese readable phrase."""
    s = event_type.value if isinstance(event_type, WyckoffEventType) else event_type
    pretty = {
        "selling_climax": "抛售高潮 (SC)",
        "automatic_rally": "自动反弹 (AR)",
        "secondary_test": "二次测试 (ST)",
        "spring": "弹簧 (Spring)",
        "sign_of_strength": "强势信号 (SOS)",
        "last_point_of_support": "最后支撑点 (LPS)",
        "buying_climax": "买入高潮 (BC)",
        "automatic_reaction": "自动回落 (AR)",
        "secondary_test_distribution": "二次测试 (ST)",
        "upthrust_after_distribution": "冲高回落 (UTAD)",
        "sign_of_weakness": "弱势信号 (SOW)",
        "last_point_of_supply": "最后供给点 (LPSY)",
    }
    return pretty.get(s, s)


@app.command(name="context-report")
def context_report(
    timeframe: str = typer.Option("1h", help="Working timeframe (resample 1m to this)."),
    htf: str | None = typer.Option(
        None,
        help="Optional higher timeframe for trend alignment (e.g. 4h, 1d).",
    ),
    swing_lookback: int = typer.Option(3, min=1, help="Fractal lookback for swings."),
    volume_climax_z: float = typer.Option(
        2.0, min=0.5, help="Z-score for Wyckoff volume climax detection."
    ),
    eq_tolerance_bps: float = typer.Option(
        10.0, min=0.0, help="Tolerance for liquidity pool clustering (bps)."
    ),
    symbol: str | None = typer.Option(None, help="Override SYMBOL setting."),
) -> None:
    """Generate the umbrella market context report (the headline command)."""
    import duckdb

    from pa_assistant.analysis import (
        analyze_wyckoff,
        build_context_report,
        build_flow_context,
        build_funding_context,
        build_liquidity_map,
        build_stop_hunt_context,
        build_trend_context,
        build_wyckoff_context,
        build_zone_context,
        compute_delta,
        compute_volume_profile,
        compute_vwap,
        detect_divergences,
        detect_fvgs,
        detect_liquidity_levels,
        detect_order_blocks,
        detect_stop_hunts,
        detect_structure_events,
        detect_swings,
        render_markdown,
        resample_ohlcv,
    )

    settings = get_settings()
    _bootstrap(settings)
    sym = (symbol or settings.symbol).upper()

    # ------------------------------------------------------------------
    # 1. Load raw data from DuckDB
    # ------------------------------------------------------------------
    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        klines = conn.execute(
            "SELECT open_time, open, high, low, close, volume, "
            "quote_volume, taker_buy_base "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [sym],
        ).pl()
        oi_df = conn.execute(
            "SELECT timestamp AS open_time, open_interest AS oi "
            "FROM oi_1m WHERE symbol = ? ORDER BY timestamp",
            [sym],
        ).pl()
        funding_row = conn.execute(
            "SELECT weighted_rate FROM funding_weighted "
            "WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
            [sym],
        ).fetchone()
    finally:
        conn.close()

    if klines.is_empty():
        typer.secho(
            f"No klines for {sym}. Run `pa backfill` first.", fg=typer.colors.RED
        )
        raise typer.Exit(code=1)

    # ------------------------------------------------------------------
    # 2. Resample to working timeframe + derive flow columns
    # ------------------------------------------------------------------
    working = resample_ohlcv(klines, timeframe)
    working = compute_delta(working)
    if not oi_df.is_empty():
        working = working.sort("open_time").join_asof(
            oi_df.sort("open_time"), on="open_time", strategy="backward"
        )
    last_close = float(working.row(working.height - 1, named=True)["close"])
    last_ts = working.row(working.height - 1, named=True)["open_time"]

    # ------------------------------------------------------------------
    # 3. Run primitive detectors on working TF
    # ------------------------------------------------------------------
    annotated = detect_swings(working, lookback=swing_lookback)
    structure_events = detect_structure_events(annotated)
    liquidity_levels = detect_liquidity_levels(
        working, tolerance_bps=eq_tolerance_bps
    )
    stop_hunts = detect_stop_hunts(working, liquidity_levels)
    order_blocks = detect_order_blocks(working, structure_events)
    fvgs = detect_fvgs(working)
    divergences = detect_divergences(working)
    wyckoff_snaps = analyze_wyckoff(
        working,
        swing_lookback=swing_lookback,
        volume_climax_z=volume_climax_z,
        eq_tolerance_bps=eq_tolerance_bps,
        divergences=divergences,
    )
    wyckoff_snap = wyckoff_snaps[-1]

    # VWAP and Volume Profile
    vwap_df = compute_vwap(working)
    vwap = (
        float(vwap_df.row(vwap_df.height - 1, named=True)["vwap"])
        if "vwap" in vwap_df.columns and vwap_df.height > 0
        else None
    )
    profile = compute_volume_profile(working)
    poc = profile.poc if profile is not None else None

    # ------------------------------------------------------------------
    # 4. Optional HTF trend
    # ------------------------------------------------------------------
    htf_trend = "none"
    htf_events: list[Any] = []
    if htf is not None:
        htf_df = resample_ohlcv(klines, htf)
        htf_annotated = detect_swings(htf_df, lookback=swing_lookback)
        htf_events = detect_structure_events(htf_annotated)
        # Most recent BOS/CHoCH determines trend
        if htf_events:
            last = htf_events[-1]
            if last.event_type in {"BOS_up", "CHoCH_up"}:
                htf_trend = "up"
            elif last.event_type in {"BOS_down", "CHoCH_down"}:
                htf_trend = "down"

    # Working-TF trend from latest structure event
    working_trend = "none"
    if structure_events:
        last = structure_events[-1]
        if last.event_type in {"BOS_up", "CHoCH_up"}:
            working_trend = "up"
        elif last.event_type in {"BOS_down", "CHoCH_down"}:
            working_trend = "down"

    # ------------------------------------------------------------------
    # 5. OI 24h ago: lookup nearest OI sample 24h before now
    # ------------------------------------------------------------------
    oi_now = None
    oi_24h_ago = None
    if not oi_df.is_empty():
        oi_now_row = oi_df.row(oi_df.height - 1, named=True)
        oi_now = float(oi_now_row["oi"])
        from datetime import timedelta as _td

        target = oi_now_row["open_time"] - _td(hours=24)
        # find row closest to target without going past it
        candidates = oi_df.filter(oi_df["open_time"] <= target)
        if candidates.height > 0:
            oi_24h_ago = float(candidates.row(candidates.height - 1, named=True)["oi"])

    funding_rate = float(funding_row[0]) if funding_row else None

    # ------------------------------------------------------------------
    # 6. Build sub-contexts
    # ------------------------------------------------------------------
    cvd_series = (
        working.get_column("cvd").to_list() if "cvd" in working.columns else []
    )

    trend_ctx = build_trend_context(
        working_timeframe=timeframe,
        working_trend=working_trend,  # type: ignore[arg-type]
        working_events=structure_events,
        htf_timeframe=htf,
        htf_trend=htf_trend,  # type: ignore[arg-type]
        htf_events=htf_events,
    )
    wyckoff_ctx = build_wyckoff_context(wyckoff_snap, language="zh")
    liquidity_map = build_liquidity_map(liquidity_levels, current_price=last_close)
    zone_ctx = build_zone_context(order_blocks, fvgs, current_price=last_close)
    flow_ctx = build_flow_context(
        cvd_series=cvd_series,
        vwap=vwap,
        current_price=last_close,
        poc=poc,
        divergences=divergences,
    )
    stop_hunt_ctx = build_stop_hunt_context(stop_hunts)
    funding_ctx = build_funding_context(
        oi=oi_now, oi_24h_ago=oi_24h_ago, funding_rate=funding_rate
    )

    # ------------------------------------------------------------------
    # 7. Compose & render
    # ------------------------------------------------------------------
    report = build_context_report(
        timestamp=last_ts,
        symbol=sym,
        timeframe=timeframe,
        current_price=last_close,
        trend=trend_ctx,
        wyckoff=wyckoff_ctx,
        liquidity=liquidity_map,
        zones=zone_ctx,
        flow=flow_ctx,
        stop_hunts=stop_hunt_ctx,
        funding=funding_ctx,
        language="zh",
    )

    typer.echo(render_markdown(report, language="zh"))


@app.command(name="send-alert")
def send_alert(
    timeframe: str = typer.Option("1h", help="Working timeframe."),
    htf: str | None = typer.Option(None, help="Optional higher TF for trend alignment."),
    title: str | None = typer.Option(
        None, help="Override the message title (default: derived from net bias)."
    ),
    dry_run: bool = typer.Option(
        False, help="Build & print the message but don't send it anywhere."
    ),
    symbol: str | None = typer.Option(None, help="Override SYMBOL setting."),
) -> None:
    """Build a context report and push it to all configured channels."""
    import duckdb

    from pa_assistant.analysis import (
        analyze_wyckoff,
        build_context_report,
        build_flow_context,
        build_funding_context,
        build_liquidity_map,
        build_stop_hunt_context,
        build_trend_context,
        build_wyckoff_context,
        build_zone_context,
        compute_delta,
        compute_volume_profile,
        compute_vwap,
        detect_divergences,
        detect_fvgs,
        detect_liquidity_levels,
        detect_order_blocks,
        detect_stop_hunts,
        detect_structure_events,
        detect_swings,
        render_markdown,
        resample_ohlcv,
    )
    from pa_assistant.notifications import (
        NotificationMessage,
        configured_channels,
        send_to_all,
    )

    settings = get_settings()
    _bootstrap(settings)
    sym = (symbol or settings.symbol).upper()

    # ----- 1. load + analyze (mirrors context-report) -----
    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        klines = conn.execute(
            "SELECT open_time, open, high, low, close, volume, "
            "quote_volume, taker_buy_base "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [sym],
        ).pl()
        oi_df = conn.execute(
            "SELECT timestamp AS open_time, open_interest AS oi "
            "FROM oi_1m WHERE symbol = ? ORDER BY timestamp",
            [sym],
        ).pl()
        funding_row = conn.execute(
            "SELECT weighted_rate FROM funding_weighted "
            "WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
            [sym],
        ).fetchone()
    finally:
        conn.close()

    if klines.is_empty():
        typer.secho(f"No klines for {sym}.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    working = resample_ohlcv(klines, timeframe)
    working = compute_delta(working)
    if not oi_df.is_empty():
        working = working.sort("open_time").join_asof(
            oi_df.sort("open_time"), on="open_time", strategy="backward"
        )
    last_close = float(working.row(working.height - 1, named=True)["close"])
    last_ts = working.row(working.height - 1, named=True)["open_time"]

    annotated = detect_swings(working, lookback=3)
    structure_events = detect_structure_events(annotated)
    liquidity_levels = detect_liquidity_levels(working, tolerance_bps=10.0)
    stop_hunts = detect_stop_hunts(working, liquidity_levels)
    order_blocks = detect_order_blocks(working, structure_events)
    fvgs = detect_fvgs(working)
    divergences = detect_divergences(working)
    wyckoff_snap = analyze_wyckoff(working, swing_lookback=3, divergences=divergences)[-1]

    vwap_df = compute_vwap(working)
    vwap = (
        float(vwap_df.row(vwap_df.height - 1, named=True)["vwap"])
        if "vwap" in vwap_df.columns and vwap_df.height > 0
        else None
    )
    profile = compute_volume_profile(working)
    poc = profile.poc if profile is not None else None

    htf_trend = "none"
    htf_events: list[Any] = []
    if htf is not None:
        htf_df = resample_ohlcv(klines, htf)
        htf_annotated = detect_swings(htf_df, lookback=3)
        htf_events = detect_structure_events(htf_annotated)
        if htf_events:
            last = htf_events[-1]
            if last.event_type in {"BOS_up", "CHoCH_up"}:
                htf_trend = "up"
            elif last.event_type in {"BOS_down", "CHoCH_down"}:
                htf_trend = "down"

    working_trend = "none"
    if structure_events:
        last = structure_events[-1]
        if last.event_type in {"BOS_up", "CHoCH_up"}:
            working_trend = "up"
        elif last.event_type in {"BOS_down", "CHoCH_down"}:
            working_trend = "down"

    oi_now = None
    oi_24h_ago = None
    if not oi_df.is_empty():
        from datetime import timedelta as _td

        oi_now_row = oi_df.row(oi_df.height - 1, named=True)
        oi_now = float(oi_now_row["oi"])
        target = oi_now_row["open_time"] - _td(hours=24)
        candidates = oi_df.filter(oi_df["open_time"] <= target)
        if candidates.height > 0:
            oi_24h_ago = float(candidates.row(candidates.height - 1, named=True)["oi"])
    funding_rate = float(funding_row[0]) if funding_row else None

    cvd_series = working.get_column("cvd").to_list() if "cvd" in working.columns else []
    report = build_context_report(
        timestamp=last_ts,
        symbol=sym,
        timeframe=timeframe,
        current_price=last_close,
        trend=build_trend_context(
            working_timeframe=timeframe,
            working_trend=working_trend,  # type: ignore[arg-type]
            working_events=structure_events,
            htf_timeframe=htf,
            htf_trend=htf_trend,  # type: ignore[arg-type]
            htf_events=htf_events,
        ),
        wyckoff=build_wyckoff_context(wyckoff_snap, language="zh"),
        liquidity=build_liquidity_map(liquidity_levels, current_price=last_close),
        zones=build_zone_context(order_blocks, fvgs, current_price=last_close),
        flow=build_flow_context(
            cvd_series=cvd_series,
            vwap=vwap,
            current_price=last_close,
            poc=poc,
            divergences=divergences,
        ),
        stop_hunts=build_stop_hunt_context(stop_hunts),
        funding=build_funding_context(
            oi=oi_now, oi_24h_ago=oi_24h_ago, funding_rate=funding_rate
        ),
        language="zh",
    )

    # ----- 2. build the notification message -----
    bias_zh = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}[
        report.scorecard.net_bias
    ]
    derived_title = (
        title or f"[{sym} {timeframe}] 综合倾向: {bias_zh} @ ${last_close:,.0f}"
    )
    body = render_markdown(report, language="zh")
    message = NotificationMessage(title=derived_title, body=body, format="markdown")

    if dry_run:
        typer.secho("[dry-run] would send:", fg=typer.colors.YELLOW, bold=True)
        typer.echo(f"\nTitle: {message.title}\n")
        typer.echo(message.body)
        return

    # ----- 3. dispatch -----
    channels = configured_channels(settings)
    if not channels:
        typer.secho(
            "No notification channels configured. "
            "Set TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID, "
            "WECHAT_WORK_WEBHOOK_URL, or LARK_WEBHOOK_URL in your .env.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=2)

    typer.echo(f"Dispatching to {len(channels)} channel(s)...")
    outcome = asyncio.run(send_to_all(channels, message))
    failures = [name for name, err in outcome.items() if err is not None]
    successes = [name for name, err in outcome.items() if err is None]
    for name in successes:
        typer.secho(f"  ✓ {name}", fg=typer.colors.GREEN)
    for name in failures:
        err = outcome[name]
        typer.secho(f"  ✗ {name}: {err}", fg=typer.colors.RED)
    if failures and not successes:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Web server command
# ---------------------------------------------------------------------------


@app.command(name="ai-analyze")
def ai_analyze(
    timeframe: str = typer.Option("1h", help="Working timeframe (1h/4h/1d)."),
    htf: str | None = typer.Option(None, help="Higher timeframe for trend alignment."),
    language: str = typer.Option("zh", help="Report language (zh/en)."),
    dry_run: bool = typer.Option(False, help="Print report without sending."),
    no_fetch: bool = typer.Option(False, "--no-fetch", help="Skip fetching latest data from exchanges."),
    symbol: str | None = typer.Option(None, help="Override SYMBOL setting."),
) -> None:
    """Run AI analysis and push to notification channels."""
    from pa_assistant.analysis.llm import LLMConfig, analyze_with_llm
    from pa_assistant.notifications import NotificationMessage, configured_channels, send_to_all
    from pa_assistant.scheduler import collect_market_data, fetch_latest_data

    settings = get_settings()
    _bootstrap(settings)
    log = get_logger("cli.ai_analyze")

    sym = (symbol or settings.symbol).upper()
    log.info("ai_analyze_start", symbol=sym, timeframe=timeframe, htf=htf)

    # 0. Fetch latest data from exchanges
    if not no_fetch:
        typer.echo("正在从交易所拉取最新数据...")
        asyncio.run(fetch_latest_data(settings, days=1))
        typer.echo("数据拉取完成")

    # 1. Collect market data
    try:
        market_data = collect_market_data(settings, timeframe, htf=htf)
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED)
        raise typer.Exit(code=1) from e

    # 2. Check LLM config
    if not settings.llm_api_key:
        typer.secho(
            "LLM API key not configured. Set LLM_API_KEY in .env",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    llm_config = LLMConfig(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
    )

    # 3. Call LLM
    typer.echo(f"Calling LLM ({settings.llm_model})...")
    report = asyncio.run(
        analyze_with_llm(
            market_data,
            llm_config,
            language=language,
            proxy_url=settings.http_proxy_url,
        )
    )

    # 4. Build message
    tf_label = timeframe.upper()
    title = f"[{sym} {tf_label}] AI 分析报告"
    message = NotificationMessage(title=title, body=report, format="markdown")

    if dry_run:
        typer.secho("[dry-run] would send:", fg=typer.colors.YELLOW, bold=True)
        typer.echo(f"\nTitle: {message.title}\n")
        typer.echo(message.body)
        return

    # 5. Push to channels
    channels = configured_channels(settings)
    if not channels:
        typer.secho(
            "No notification channels configured. "
            "Set TELEGRAM_BOT_TOKEN, WECHAT_WORK_WEBHOOK_URL, or LARK_WEBHOOK_URL in .env",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=2)

    typer.echo(f"Dispatching to {len(channels)} channel(s)...")
    outcome = asyncio.run(send_to_all(channels, message))
    successes = [name for name, err in outcome.items() if err is None]
    failures = [name for name, err in outcome.items() if err is not None]

    for name in successes:
        typer.secho(f"  ✓ {name}", fg=typer.colors.GREEN)
    for name in failures:
        err = outcome[name]
        typer.secho(f"  ✗ {name}: {err}", fg=typer.colors.RED)

    if failures and not successes:
        raise typer.Exit(code=1)


@app.command(name="schedule-start")
def schedule_start(
    language: str = typer.Option("zh", help="Report language (zh/en)."),
) -> None:
    """Start the scheduled analysis scheduler.

    Scheduled jobs:
    * Daily 08:05 (Beijing): 1D K-line analysis
    * Every hour: 1H K-line analysis
    * Every 4 hours: 4H K-line analysis
    """
    import signal

    from pa_assistant.scheduler import create_scheduler

    settings = get_settings()
    _bootstrap(settings)

    typer.secho(
        "Starting PA Assistant Scheduler",
        fg=typer.colors.GREEN,
        bold=True,
    )
    typer.echo("")
    typer.echo("Scheduled jobs:")
    typer.echo("  • 每天 08:05 (北京时间): 日K 分析")
    typer.echo("  • 每小时: 1H K 线分析")
    typer.echo("  • 每 4 小时: 4H K 线分析")
    typer.echo("")
    typer.echo("Press Ctrl+C to stop")
    typer.echo("")

    scheduler = create_scheduler(language=language)
    scheduler.start()

    # Keep the main thread alive
    try:
        signal.pause()
    except AttributeError:
        # Windows doesn't have signal.pause()
        import time

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    scheduler.shutdown()
    typer.echo("\nScheduler stopped.")


if __name__ == "__main__":
    app()
