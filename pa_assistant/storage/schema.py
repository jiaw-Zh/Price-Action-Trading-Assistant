"""DuckDB schema definitions.

Tables are created idempotently via ``CREATE TABLE IF NOT EXISTS``. Schema
versions are tracked in ``schema_migrations``; bumping
:data:`CURRENT_SCHEMA_VERSION` requires adding a migration step in
:mod:`pa_assistant.storage.repository`.

Conventions:

* Timestamps are UTC ``TIMESTAMP`` (no timezone column — DuckDB's
  ``TIMESTAMP`` is naive; we treat all values as UTC by convention).
* Money-like values use ``DOUBLE``. We accept FP imprecision in exchange for
  Polars/NumPy interop; backtests that need exactness can use ``DECIMAL``.
* Primary keys are explicit; surrogate IDs are avoided unless needed.
"""

from __future__ import annotations

# Bump this when changing any DDL below and add a migration in repository.
CURRENT_SCHEMA_VERSION: int = 1


# ---------------------------------------------------------------------------
# 1-minute klines — source of truth for all higher timeframes.
# ---------------------------------------------------------------------------
KLINE_1M = """
CREATE TABLE IF NOT EXISTS kline_1m (
    open_time       TIMESTAMP NOT NULL,
    close_time      TIMESTAMP NOT NULL,
    symbol          VARCHAR   NOT NULL,
    open            DOUBLE    NOT NULL,
    high            DOUBLE    NOT NULL,
    low             DOUBLE    NOT NULL,
    close           DOUBLE    NOT NULL,
    volume          DOUBLE    NOT NULL,        -- base-asset volume
    quote_volume    DOUBLE    NOT NULL,
    trade_count     BIGINT    NOT NULL,
    taker_buy_base  DOUBLE    NOT NULL,        -- aggressive-buy volume
    taker_buy_quote DOUBLE    NOT NULL,
    is_closed       BOOLEAN   NOT NULL DEFAULT TRUE,
    PRIMARY KEY (symbol, open_time)
);
"""
KLINE_1M_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_kline_1m_time ON kline_1m(open_time);",
]


# ---------------------------------------------------------------------------
# Aggregated trades (Binance aggTrade stream).
# ---------------------------------------------------------------------------
TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id        BIGINT    NOT NULL,
    symbol          VARCHAR   NOT NULL,
    timestamp       TIMESTAMP NOT NULL,
    price           DOUBLE    NOT NULL,
    quantity        DOUBLE    NOT NULL,
    quote_qty       DOUBLE    NOT NULL,
    is_buyer_maker  BOOLEAN   NOT NULL,        -- false ⇒ aggressive buy
    PRIMARY KEY (symbol, trade_id)
);
"""
TRADES_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trades(symbol, timestamp);",
]


# ---------------------------------------------------------------------------
# Open Interest snapshots (REST polling, ~1 min cadence).
# ---------------------------------------------------------------------------
OI_1M = """
CREATE TABLE IF NOT EXISTS oi_1m (
    timestamp      TIMESTAMP NOT NULL,
    symbol         VARCHAR   NOT NULL,
    open_interest  DOUBLE    NOT NULL,         -- in base asset
    notional_usd   DOUBLE,                     -- nullable: derived field
    PRIMARY KEY (symbol, timestamp)
);
"""


# ---------------------------------------------------------------------------
# OI-weighted funding rate (Coinglass primary, self-aggregated fallback).
# ---------------------------------------------------------------------------
FUNDING_WEIGHTED = """
CREATE TABLE IF NOT EXISTS funding_weighted (
    timestamp      TIMESTAMP NOT NULL,
    symbol         VARCHAR   NOT NULL,
    weighted_rate  DOUBLE    NOT NULL,         -- e.g. 0.0001 = 0.01 %
    source         VARCHAR   NOT NULL,         -- 'coinglass' | 'self_aggregated'
    sample_count   INTEGER,                    -- exchanges aggregated (self-agg only)
    raw            JSON,                       -- per-exchange breakdown
    PRIMARY KEY (symbol, timestamp, source)
);
"""


# ---------------------------------------------------------------------------
# Forced liquidations (Binance forceOrder stream).
# ---------------------------------------------------------------------------
LIQUIDATIONS = """
CREATE TABLE IF NOT EXISTS liquidations (
    timestamp      TIMESTAMP NOT NULL,
    symbol         VARCHAR   NOT NULL,
    side           VARCHAR   NOT NULL,         -- 'BUY' (short liq) | 'SELL' (long liq)
    price          DOUBLE    NOT NULL,
    quantity       DOUBLE    NOT NULL,
    notional_usd   DOUBLE    NOT NULL,
    order_id       VARCHAR
);
"""
LIQUIDATIONS_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_liq_time ON liquidations(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_liq_side_time ON liquidations(side, timestamp);",
]


# ---------------------------------------------------------------------------
# Context snapshots — the system's primary output, persisted for replay.
# ---------------------------------------------------------------------------
CONTEXT_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS context_snapshots (
    timestamp      TIMESTAMP NOT NULL,
    symbol         VARCHAR   NOT NULL,
    trend_4h       VARCHAR,
    trend_15m      VARCHAR,
    structure      VARCHAR,
    wyckoff_phase  VARCHAR,
    liquidity      JSON,
    vsa            JSON,
    score_long     DOUBLE,
    score_short    DOUBLE,
    report         JSON      NOT NULL,         -- full report payload
    PRIMARY KEY (symbol, timestamp)
);
"""


# ---------------------------------------------------------------------------
# Manual trading journal — links each trade to its context snapshot.
# ---------------------------------------------------------------------------
JOURNAL = """
CREATE TABLE IF NOT EXISTS journal (
    trade_id       VARCHAR   NOT NULL PRIMARY KEY,
    symbol         VARCHAR   NOT NULL,
    side           VARCHAR   NOT NULL,         -- 'LONG' | 'SHORT'
    entry_time     TIMESTAMP NOT NULL,
    entry_price    DOUBLE    NOT NULL,
    exit_time      TIMESTAMP,
    exit_price     DOUBLE,
    quantity       DOUBLE    NOT NULL,
    pnl            DOUBLE,
    snapshot_time  TIMESTAMP,                  -- references context_snapshots
    notes          VARCHAR
);
"""


# ---------------------------------------------------------------------------
# Schema migration ledger.
# ---------------------------------------------------------------------------
SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version       INTEGER     NOT NULL PRIMARY KEY,
    applied_at    TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description   VARCHAR
);
"""


# Order matters: schema_migrations first so it's available for bookkeeping.
ALL_TABLES: tuple[tuple[str, list[str]], ...] = (
    (SCHEMA_MIGRATIONS, []),
    (KLINE_1M, KLINE_1M_INDEXES),
    (TRADES, TRADES_INDEXES),
    (OI_1M, []),
    (FUNDING_WEIGHTED, []),
    (LIQUIDATIONS, LIQUIDATIONS_INDEXES),
    (CONTEXT_SNAPSHOTS, []),
    (JOURNAL, []),
)


# Public list of table names — used by tests and the ``init-db`` CLI command.
TABLE_NAMES: tuple[str, ...] = (
    "schema_migrations",
    "kline_1m",
    "trades",
    "oi_1m",
    "funding_weighted",
    "liquidations",
    "context_snapshots",
    "journal",
)
