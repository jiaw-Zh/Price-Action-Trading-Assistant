"""DuckDB connection management and schema bootstrap."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

from pa_assistant.logging import get_logger
from pa_assistant.storage.schema import ALL_TABLES, CURRENT_SCHEMA_VERSION

if TYPE_CHECKING:
    from types import TracebackType

log = get_logger(__name__)


class Database:
    """Thin wrapper around a single DuckDB connection.

    The underlying file is created on first :meth:`connect`. :meth:`init_schema`
    is idempotent — safe to call on every startup.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: duckdb.DuckDBPyConnection | None = None

    # ----- Connection lifecycle -----

    def connect(self) -> duckdb.DuckDBPyConnection:
        """Open (or return) the underlying DuckDB connection."""
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(self.path))
            log.info("duckdb_connected", path=str(self.path))
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            log.info("duckdb_closed", path=str(self.path))

    def __enter__(self) -> Database:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ----- Schema management -----

    def init_schema(self) -> None:
        """Create all tables/indexes if missing and stamp schema version."""
        conn = self.connect()
        for create_sql, indexes in ALL_TABLES:
            conn.execute(create_sql)
            for idx_sql in indexes:
                conn.execute(idx_sql)

        # Record version idempotently.
        conn.execute(
            """
            INSERT INTO schema_migrations (version, description)
            SELECT ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM schema_migrations WHERE version = ?
            );
            """,
            [CURRENT_SCHEMA_VERSION, "initial schema", CURRENT_SCHEMA_VERSION],
        )
        log.info("schema_initialized", version=CURRENT_SCHEMA_VERSION)

    def schema_version(self) -> int:
        """Return the highest applied schema version (0 if none)."""
        conn = self.connect()
        row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations;").fetchone()
        return int(row[0]) if row else 0

    def list_tables(self) -> list[str]:
        """List user tables in the ``main`` schema, alphabetically."""
        conn = self.connect()
        rows = conn.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'main'
            ORDER BY table_name;
            """
        ).fetchall()
        return [r[0] for r in rows]


@contextmanager
def open_db(path: Path | str) -> Iterator[Database]:
    """Open a :class:`Database`, init schema, yield it, and close on exit."""
    db = Database(path)
    try:
        db.connect()
        db.init_schema()
        yield db
    finally:
        db.close()
