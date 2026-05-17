"""Batch writers for ingested data.

These helpers are the only place SQL touches DuckDB for *writes* — analysis
modules go through Polars. Each writer is idempotent (UPSERT semantics) so
re-running an ingestion pipeline cannot corrupt existing data.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import polars as pl

from pa_assistant.logging import get_logger
from pa_assistant.storage.repository import Database

log = get_logger(__name__)


_KLINE_COLUMNS = (
    "open_time",
    "close_time",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trade_count",
    "taker_buy_base",
    "taker_buy_quote",
    "is_closed",
)


def upsert_klines_1m(db: Database, df: pl.DataFrame) -> int:
    """Insert or replace 1-minute klines.

    Args:
        db: An initialized :class:`Database`.
        df: A Polars DataFrame with the canonical ``kline_1m`` schema.

    Returns:
        Number of rows written.
    """
    if df.is_empty():
        return 0

    missing = set(_KLINE_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"klines DataFrame missing columns: {sorted(missing)}")

    conn = db.connect()
    # Reorder columns to match table schema (defensive — explicit beats implicit).
    ordered = df.select(_KLINE_COLUMNS)
    conn.register("_klines_buf", ordered)
    try:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO kline_1m ({", ".join(_KLINE_COLUMNS)})
            SELECT {", ".join(_KLINE_COLUMNS)} FROM _klines_buf;
            """
        )
    finally:
        conn.unregister("_klines_buf")

    written = ordered.height
    log.info("klines_upserted", rows=written, symbol=ordered["symbol"][0] if written else None)
    return written


def insert_oi_snapshot(
    db: Database,
    *,
    symbol: str,
    timestamp: datetime,
    open_interest: float,
    notional_usd: float | None = None,
) -> None:
    """Write a single OI snapshot (idempotent on (symbol, timestamp))."""
    conn = db.connect()
    conn.execute(
        """
        INSERT OR REPLACE INTO oi_1m (timestamp, symbol, open_interest, notional_usd)
        VALUES (?, ?, ?, ?);
        """,
        [timestamp, symbol.upper(), float(open_interest), notional_usd],
    )
    log.info(
        "oi_snapshot_written",
        symbol=symbol.upper(),
        timestamp=timestamp.isoformat(),
        open_interest=open_interest,
    )


def count_klines(db: Database, symbol: str) -> int:
    """Count 1m klines stored for ``symbol``."""
    conn = db.connect()
    row = conn.execute(
        "SELECT COUNT(*) FROM kline_1m WHERE symbol = ?;",
        [symbol.upper()],
    ).fetchone()
    return int(row[0]) if row else 0


def latest_kline_open_time(db: Database, symbol: str) -> datetime | None:
    """Return the most recent ``open_time`` stored for ``symbol`` (or ``None``)."""
    conn = db.connect()
    row = conn.execute(
        "SELECT MAX(open_time) FROM kline_1m WHERE symbol = ?;",
        [symbol.upper()],
    ).fetchone()
    if row is None or row[0] is None:
        return None
    value = row[0]
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def insert_funding_weighted(
    db: Database,
    *,
    symbol: str,
    timestamp: datetime,
    weighted_rate: float,
    source: str,
    sample_count: int | None,
    raw: dict[str, Any] | None = None,
) -> None:
    """Write one weighted-funding row.

    The primary key is ``(symbol, timestamp, source)`` so rows from different
    sources at the same instant coexist (allows side-by-side comparison).
    """
    conn = db.connect()
    raw_json = json.dumps(raw, default=str) if raw else None
    conn.execute(
        """
        INSERT OR REPLACE INTO funding_weighted
        (timestamp, symbol, weighted_rate, source, sample_count, raw)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        [
            timestamp,
            symbol.upper(),
            float(weighted_rate),
            source,
            sample_count,
            raw_json,
        ],
    )
    log.info(
        "funding_weighted_written",
        symbol=symbol.upper(),
        timestamp=timestamp.isoformat(),
        weighted_rate=weighted_rate,
        source=source,
    )
