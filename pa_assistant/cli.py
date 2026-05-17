"""Command-line entry point.

Run ``pa --help`` after installing the package (or ``uv run pa --help``).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime

import typer

from pa_assistant import __version__
from pa_assistant.config import Settings, get_settings
from pa_assistant.ingestion import BinanceRestClient, klines_to_polars
from pa_assistant.logging import configure_logging, get_logger
from pa_assistant.storage import (
    insert_oi_snapshot,
    latest_kline_open_time,
    open_db,
    upsert_klines_1m,
)

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


if __name__ == "__main__":
    app()
