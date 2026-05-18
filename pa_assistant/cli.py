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
)
from pa_assistant.logging import configure_logging, get_logger
from pa_assistant.storage import (
    insert_funding_weighted,
    insert_oi_snapshot,
    latest_kline_open_time,
    open_db,
    upsert_klines_1m,
)

if TYPE_CHECKING:
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
    typer.echo(f"  swings:  {n_high} highs · {n_low} lows  (lookback={lookback})")
    typer.echo(f"  events:  {len(events)} total")

    if not events:
        return

    typer.echo("")
    typer.secho("  Recent structure events:", bold=True)
    for ev in events[-last:]:
        is_up = "_up" in ev.event_type
        arrow = "↑" if is_up else "↓"
        colour = typer.colors.GREEN if is_up else typer.colors.RED
        typer.secho(
            f"    {ev.timestamp:%Y-%m-%d %H:%M}  {arrow} {ev.event_type:11s}  "
            f"@ ${ev.level:>10,.2f}   ({ev.trend_before} → {ev.trend_after})",
            fg=colour,
        )


if __name__ == "__main__":
    app()
