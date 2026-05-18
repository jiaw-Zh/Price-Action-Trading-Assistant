"""DuckDB storage layer.

A thin, opinionated wrapper around DuckDB that:

* lazily opens a single connection per process
* bootstraps the schema idempotently
* tracks schema version via the ``schema_migrations`` table
* offers idempotent batch writers (see :mod:`pa_assistant.storage.writers`)
"""

from pa_assistant.storage.repository import Database, open_db
from pa_assistant.storage.schema import CURRENT_SCHEMA_VERSION
from pa_assistant.storage.writers import (
    count_klines,
    insert_funding_weighted,
    insert_oi_snapshot,
    latest_kline_open_time,
    upsert_klines_1m,
    upsert_oi_history,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "Database",
    "count_klines",
    "insert_funding_weighted",
    "insert_oi_snapshot",
    "latest_kline_open_time",
    "open_db",
    "upsert_klines_1m",
    "upsert_oi_history",
]
